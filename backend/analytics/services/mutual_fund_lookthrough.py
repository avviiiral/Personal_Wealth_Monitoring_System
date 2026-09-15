from decimal import Decimal

from investments.models import Asset, SecurityMaster
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
        holdings = MutualFundHolding.objects.filter(
            owner_id__in=owner_ids,
            scheme__is_active=True,
            current_value__gt=0,
        ).select_related("scheme")
        result = {}
        for holding in holdings:
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

    @staticmethod
    def _normalize_key(value):
        return " ".join((value or "").strip().upper().split())

    @classmethod
    def _classification_maps(cls, user):
        owner_ids = cls.owner_ids(user)
        assets = Asset.objects.filter(
            owner_id__in=owner_ids,
            is_active=True,
        ).select_related("security_master")
        security_masters = SecurityMaster.objects.filter(owner_id__in=owner_ids)

        by_isin = {}
        by_name = {}
        security_by_isin = {}
        security_by_name = {}

        for asset in assets:
            isin_key = cls._normalize_key(asset.isin)
            name_key = cls._normalize_key(asset.name)
            if isin_key:
                by_isin[isin_key] = asset
            if name_key:
                by_name[name_key] = asset

            if asset.security_master:
                master = asset.security_master
                master_isin = cls._normalize_key(master.isin)
                master_name = cls._normalize_key(master.asset_name)
                if master_isin:
                    security_by_isin[master_isin] = master
                if master_name:
                    security_by_name[master_name] = master

        # Include SecurityMaster records that are not currently linked to an
        # Asset. This is important for MF look-through because an underlying
        # security may be new to the portfolio but already classified in the
        # security master by ISIN/name.
        for master in security_masters:
            isin_key = cls._normalize_key(master.isin)
            name_key = cls._normalize_key(master.asset_name)
            if isin_key:
                security_by_isin.setdefault(isin_key, master)
            if name_key:
                security_by_name.setdefault(name_key, master)

        return by_isin, by_name, security_by_isin, security_by_name

    @classmethod
    def classify(cls, underlying, by_isin, by_name, security_by_isin=None, security_by_name=None):
        security_by_isin = security_by_isin or {}
        security_by_name = security_by_name or {}

        asset = None
        master = None
        isin_key = cls._normalize_key(underlying.isin)
        name_key = cls._normalize_key(underlying.security_name)

        # Portfolio Asset remains the strongest source for allocation class.
        if isin_key:
            asset = by_isin.get(isin_key)
        if asset is None and name_key:
            asset = by_name.get(name_key)

        # Fall back to SecurityMaster when the underlying security has no
        # corresponding portfolio Asset. This supplies sector classification
        # without inventing an Asset or changing portfolio ownership.
        if isin_key:
            master = security_by_isin.get(isin_key)
        if master is None and name_key:
            master = security_by_name.get(name_key)
        if master is None and asset is not None:
            master = asset.security_master

        asset_class = asset.category if asset is not None else None
        sector = (underlying.sector or "").strip() or None
        if not sector and master is not None:
            sector = (master.sector or "").strip() or None

        # A SecurityMaster match with a sector is an equity-like security for
        # the purpose of the existing allocation fallback. We deliberately do
        # not create a broader asset-class guess when sector is absent.
        if asset_class is None and sector:
            asset_class = "STOCK"

        return asset_class, sector

    @classmethod
    def allocation(cls, user, direct_holdings):
        """Preserve the existing allocation response while replacing disclosed MF exposure with look-through exposure."""
        totals = {}
        by_isin, by_name, security_by_isin, security_by_name = cls._classification_maps(user)
        lookthrough = cls.latest_underlyings(user)

        for holding in direct_holdings:
            value = holding.current_value or cls.ZERO
            if value > 0:
                totals[holding.asset.category] = totals.get(holding.asset.category, cls.ZERO) + value

        # Mutual funds are not part of PortfolioAnalytics.get_holdings().
        # Add them here only when no usable disclosure exists; otherwise their
        # value is represented exclusively by their underlying exposures.
        all_mf_holdings = MutualFundHolding.objects.filter(
            owner_id__in=cls.owner_ids(user),
            scheme__is_active=True,
            current_value__gt=0,
        ).select_related("scheme")
        for mf_holding in all_mf_holdings:
            if mf_holding.scheme_id in lookthrough:
                continue
            value = mf_holding.current_value or cls.ZERO
            totals["MUTUAL_FUND"] = totals.get("MUTUAL_FUND", cls.ZERO) + value

        for data in lookthrough.values():
            mf_value = data["holding"].current_value or cls.ZERO
            if mf_value <= 0:
                continue
            disclosed_total = cls.ZERO
            for row in data["rows"]:
                pct = row.percentage_of_nav or cls.ZERO
                exposure = mf_value * pct / Decimal("100")
                disclosed_total += exposure
                asset_class, sector = cls.classify(
                    row,
                    by_isin,
                    by_name,
                    security_by_isin,
                    security_by_name,
                )
                if asset_class:
                    bucket = asset_class
                elif sector:
                    bucket = "STOCK"
                else:
                    bucket = cls.UNCLASSIFIED
                totals[bucket] = totals.get(bucket, cls.ZERO) + exposure

            # Preserve total portfolio value without guessing the class of
            # the residual cash/derivative/other disclosure rows.
            residual = mf_value - disclosed_total
            if residual > 0:
                totals[cls.UNCLASSIFIED] = totals.get(cls.UNCLASSIFIED, cls.ZERO) + residual

        grand_total = sum((value for value in totals.values() if value > 0), cls.ZERO)
        results = []
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
        by_isin, by_name, security_by_isin, security_by_name = cls._classification_maps(user)

        for holding in direct_holdings:
            if holding.asset_id not in equity_asset_ids:
                continue
            value = holding.current_value or cls.ZERO
            if value <= 0:
                continue
            sector = None
            if holding.asset.security_master:
                sector = (holding.asset.security_master.sector or "").strip() or None
            totals[sector or cls.UNCLASSIFIED] = totals.get(sector or cls.UNCLASSIFIED, cls.ZERO) + value

        for data in cls.latest_underlyings(user).values():
            mf_value = data["holding"].current_value or cls.ZERO
            for row in data["rows"]:
                asset_class, sector = cls.classify(
                    row,
                    by_isin,
                    by_name,
                    security_by_isin,
                    security_by_name,
                )
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
