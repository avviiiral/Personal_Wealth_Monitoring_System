from decimal import Decimal

from django.db.models import Q

from investments.models import Asset, SecurityMaster
from mutual_funds.models import MutualFundHolding, MutualFundUnderlying
from users.permissions import get_active_family_group, is_system_owner
from investments.services.asset_underlying import AssetUnderlyingService


class MutualFundLookThroughService:
    """Calculate non-double-counted look-through exposure for mutual funds."""

    ZERO = Decimal("0")
    UNCLASSIFIED = "Unclassified"
    EXCLUDED_ALLOCATION_LABEL = "EQUITY AIF (CATEGORY III)"

    @staticmethod
    def scope_q(user):
        if is_system_owner(user):
            return Q()
        family = get_active_family_group(user)
        if family is None:
            return Q(pk__in=[])
        return Q(family_id=family.id)

    @classmethod
    def latest_underlyings(cls, user):
        holdings = MutualFundHolding.objects.filter(
            MutualFundLookThroughService.scope_q(user),
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
        assets = Asset.objects.filter(
            MutualFundLookThroughService.scope_q(user),
            is_active=True,
        ).select_related("security_master")
        security_masters = SecurityMaster.objects.filter(MutualFundLookThroughService.scope_q(user))

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

        if isin_key:
            asset = by_isin.get(isin_key)
        if asset is None and isin_key:
            master = security_by_isin.get(isin_key)
        if asset is None and name_key:
            asset = by_name.get(name_key)
        if asset is None and name_key:
            master = security_by_name.get(name_key)

        if master is None and asset is not None:
            master = asset.security_master

        asset_class = asset.category if asset is not None else None
        sector = (underlying.sector or "").strip() or None
        if not sector and master is not None:
            sector = (master.sector or "").strip() or None

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

        all_mf_holdings = MutualFundHolding.objects.filter(
            MutualFundLookThroughService.scope_q(user),
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

        underlying_by_asset = AssetUnderlyingService.latest_by_asset(user)
        for holding in direct_holdings:
            if holding.asset_id not in equity_asset_ids:
                continue
            value = holding.current_value or cls.ZERO
            if value <= 0:
                continue
            underlying_rows = underlying_by_asset.get(holding.asset_id, [])
            if underlying_rows:
                disclosed = cls.ZERO
                for row in underlying_rows:
                    exposure = value * (row.holding_percentage or cls.ZERO) / Decimal("100")
                    sector = (row.sector or "").strip() or cls.UNCLASSIFIED
                    if sector.upper() == cls.EXCLUDED_ALLOCATION_LABEL:
                        continue
                    totals[sector] = totals.get(sector, cls.ZERO) + exposure
                    disclosed += row.holding_percentage or cls.ZERO
                residual = value * max(cls.ZERO, Decimal("100") - disclosed) / Decimal("100")
                if residual:
                    totals[cls.UNCLASSIFIED] = totals.get(cls.UNCLASSIFIED, cls.ZERO) + residual
                continue
            sector = None
            if holding.asset.security_master:
                sector = (holding.asset.security_master.sector or "").strip() or None
            sector = sector or cls.UNCLASSIFIED
            if sector.upper() != cls.EXCLUDED_ALLOCATION_LABEL:
                totals[sector] = totals.get(sector, cls.ZERO) + value

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
                if sector and sector.upper() == cls.EXCLUDED_ALLOCATION_LABEL:
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
