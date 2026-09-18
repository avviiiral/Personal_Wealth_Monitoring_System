from django.db.models import Q
from decimal import Decimal

from django.db.models import Q

from users.permissions import get_active_family_group, is_system_owner
from investments.models import Asset

from .unified_wealth import UnifiedWealthAnalytics


class EquityAnalysisService:
    """Build equity valuation metrics using current-value weighting."""

    ZERO = Decimal("0")

    @staticmethod
    def _scope_q(user):
        if is_system_owner(user):
            return Q()
        family = get_active_family_group(user)
        if family is None:
            return Q(pk__in=[])
        return Q(family_id=family.id)

    @classmethod
    def calculate(cls, user):
        equity_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(user))

        rows = (
            Asset.objects
            .filter(EquityAnalysisService._scope_q(user), security_master__isnull=False)
            .values_list(
                "id",
                "security_master__cap_type",
                "security_master__pe_ratio",
                "security_master__pb_ratio",
                "security_master__peg_ratio",
                "security_master__roe",
            )
        )

        security_by_asset_id = {
            asset_id: {
                "cap_type": cap_type,
                "pe_ratio": pe_ratio,
                "pb_ratio": pb_ratio,
                "peg_ratio": peg_ratio,
                "roe": roe,
            }
            for asset_id, cap_type, pe_ratio, pb_ratio, peg_ratio, roe in rows
        }

        total_current_value = sum(
            (holding.current_value or cls.ZERO) for holding in equity_holdings
        )
        cap_totals = {}
        weighted_sums = {
            "pe_ratio": cls.ZERO,
            "pb_ratio": cls.ZERO,
            "peg_ratio": cls.ZERO,
            "roe": cls.ZERO,
        }
        weighted_bases = {
            "pe_ratio": cls.ZERO,
            "pb_ratio": cls.ZERO,
            "peg_ratio": cls.ZERO,
            "roe": cls.ZERO,
        }
        weighted_counts = {
            "pe_ratio": 0,
            "pb_ratio": 0,
            "peg_ratio": 0,
            "roe": 0,
        }

        for holding in equity_holdings:
            current_value = holding.current_value or cls.ZERO
            sm = security_by_asset_id.get(holding.asset_id, {})
            cap_type = (sm.get("cap_type") or "").strip() or "Unclassified"
            cap_totals[cap_type] = cap_totals.get(cap_type, cls.ZERO) + current_value

            if current_value <= 0:
                continue

            for field in weighted_sums:
                value = sm.get(field)
                if value is None:
                    continue
                weighted_sums[field] += value * current_value
                weighted_bases[field] += current_value
                weighted_counts[field] += 1

        def weighted_average(field):
            base = weighted_bases[field]
            if not base:
                return None
            return round(weighted_sums[field] / base, 2)

        market_cap_allocation = []
        for cap_type, value in sorted(
            cap_totals.items(), key=lambda item: item[1], reverse=True
        ):
            percentage = (
                (value / total_current_value) * 100
                if total_current_value
                else cls.ZERO
            )
            market_cap_allocation.append({
                "cap_type": cap_type,
                "current_value": value,
                "percentage": round(percentage, 2),
            })

        return {
            "current_value": total_current_value,
            "number_of_holdings": len(equity_holdings),
            "portfolio_pe": weighted_average("pe_ratio"),
            "portfolio_pe_holding_count": weighted_counts["pe_ratio"],
            "portfolio_pb": weighted_average("pb_ratio"),
            "portfolio_pb_holding_count": weighted_counts["pb_ratio"],
            "portfolio_peg": weighted_average("peg_ratio"),
            "portfolio_peg_holding_count": weighted_counts["peg_ratio"],
            "portfolio_roe": weighted_average("roe"),
            "portfolio_roe_holding_count": weighted_counts["roe"],
            "market_cap_allocation": market_cap_allocation,
        }
