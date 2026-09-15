from decimal import Decimal

from investments.models import Asset
from mutual_funds.models import MutualFundHolding, MutualFundUnderlying


class MutualFundLookThroughService:
    """Calculate non-double-counted look-through exposure for mutual funds."""

    ZERO = Decimal("0")
    UNCLASSIFIED = "Unclassified"

    @staticmethod
    def owner_ids(user):
        return [user.pk] if hasattr(user, "pk") else list(user)

    @classmethod
    def latest_underlyings(cls, user):
        owner_ids = cls.owner_ids(user)
        schemes = MutualFundHolding.objects.filter(
            owner_id__in=owner_ids,
            scheme__is_active=True,
            current_value__gt=0,
        ).select_related("scheme")

        result = {}
        for holding in schemes:
            rows = MutualFundUnderlying.objects.filter(scheme=holding.scheme)
            latest_date = rows.order_by("-portfolio_date").values_list("portfolio_date", flat=True).first()
            if latest_date is None:
                continue
            result[holding.scheme_id] = {
                "holding": holding,
                "portfolio_date": latest_date,
                "rows": list(rows.filter(portfolio_date=latest_date)),
            }
        return result

    @classmethod
    def _classification_maps(cls, user):
        owner_ids = cls.owner_ids(user)
        assets = Asset.objects.filter(
            owner_id__in=owner_ids,
            is_active=True,
        ).select_related("security_master")

        by_isin = {}
        by_name = {}
        for asset in assets:
            if asset.isin:
                by_isin[asset.isin.strip().upper()] = asset
            by_name[asset.name.strip().upper()] = asset
        return by_isin, by_name

    @classmethod
    def classify(cls, underlying, by_isin, by_name):
        asset = None
        if underlying.isin:
            asset = by_isin.get(underlying.isin.strip().upper())
        if asset is None:
            asset = by_name.get(underlying.security_name.strip().upper())

        asset_class = None
        sector = (underlying.sector or "").strip() or None
        if asset is not None:
            asset_class = asset.category
            if asset.security_master and not sector:
                sector = (asset.security_master.sector or "").strip() or None

        return asset_class, sector

    @classmethod
    def allocation(cls, user, direct_holdings):
        """Return existing allocation shape, replacing disclosed MF rows with look-through exposure."""
        totals = {}
        total_value = cls.ZERO
        by_isin, by_name = cls._classification_maps(user)
        lookthrough = cls.latest_underlyings(user)

        for holding in direct_holdings:
            value = holding.current_value or cls.ZERO
            if value <= 0:
                continue
            totals[holding.asset.category] = totals.get(holding.asset.category, cls.ZERO) + value
            total_value += value

        # Remove each MF's own bucket only when a usable disclosure exists.
        for scheme_id, data in lookthrough.items():
            mf_value = data["holding"].current_value or cls.ZERO
            if mf_value <= 0:
                continue
            totals["MUTUAL_FUND"] = totals.get("MUTUAL_FUND", cls.ZERO) - mf_value
            if totals["MUTUAL_FUND"] <= 0:
                totals.pop("MUTUAL_FUND", None)

            classified_value = cls.ZERO
            for row in data["rows"]:
                pct = row.percentage_of_nav or cls.ZERO
                exposure = mf_value * pct / Decimal("100")
                asset_class, sector = cls.classify(row, by_isin, by_name)
                if asset_class:
                    bucket = asset_class
                elif sector:
                    # A disclosed industry/sector is sufficient to identify
                    # an equity-style exposure for allocation purposes.
                    bucket = "STOCK"
                else:
                    bucket = cls.UNCLASSIFIED
                totals[bucket] = totals.get(bucket, cls.ZERO) + exposure
                if bucket != cls.UNCLASSIFIED:
                    classified_value += exposure

            # Keep the portfolio total mathematically consistent. The
            # undisclosed/residual portion is genuinely unclassified rather
            # than being assigned to a guessed asset class.
            residual = mf_value - sum(
                (mf_value * (row.percentage_of_nav or cls.ZERO) / Decimal("100") for row in data["rows"]),
                cls.ZERO,
            )
            if residual > 0:
                totals[cls.UNCLASSIFIED] = totals.get(cls.UNCLASSIFIED, cls.ZERO) + residual

        results = []
        grand_total = sum((value for value in totals.values() if value > 0), cls.ZERO)
        for category, value in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            if value <= 0:
                continue
            results.append({
                "category": category,
                "value": value,
                "percentage": round((value / grand_total) * 100, 2) if grand_total else 0,
            })
        return results

    @classmethod
    def sector_allocation(cls, user, direct_holdings, equity_asset_ids):
        totals = {}
        by_isin, by_name = cls._classification_maps(user)

        for holding in direct_holdings:
            if holding.asset_id not in equity_asset_ids:
                continue
            value = holding.current_value or cls.ZERO
            if value <= 0:
                continue
            sector = None
            if holding.asset.security_master:
                sector = (holding.asset.security_master.sector or "").strip() or None
            sector = sector or cls.UNCLASSIFIED
            totals[sector] = totals.get(sector, cls.ZERO) + value

        for data in cls.latest_underlyings(user).values():
            mf_value = data["holding"].current_value or cls.ZERO
            for row in data["rows"]:
                asset_class, sector = cls.classify(row, by_isin, by_name)
                # Sector Allocation is intentionally Equity-only. A disclosed
                # sector is the strongest available classification signal.
                if not sector and asset_class not in {"STOCK", "ETF"}:
                    continue
                exposure = mf_value * (row.percentage_of_nav or cls.ZERO) / Decimal("100")
                totals[sector or cls.UNCLASSIFIED] = totals.get(sector or cls.UNCLASSIFIED, cls.ZERO) + exposure

        grand_total = sum(totals.values(), cls.ZERO)
        results = []
        for sector, value in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            results.append({
                "sector": sector,
                "current_value": value,
                "percentage": round((value / grand_total) * 100, 2) if grand_total else 0,
            })
        return {"results": results, "total_current_value": grand_total}
