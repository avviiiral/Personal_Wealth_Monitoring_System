from datetime import date
from decimal import Decimal


class AnalyticsViewModelService:
    """
    Builds the authoritative backend view model consumed by the Analytics page.

    Existing financial services remain the source of truth for valuation,
    XIRR, allocation, historical valuation, advisor metrics, sector exposure,
    and market-cap exposure. This service only orchestrates those services and
    owns the Analytics-specific derived insights that were previously computed
    in Angular.
    """

    @staticmethod
    def _number(value):
        try:
            number = Decimal(str(value))
        except (TypeError, ValueError):
            return Decimal("0")
        return number

    @classmethod
    def _investment_performance(cls, user, family_name=None, investment_summary=None):
        from portfolio.services.portfolio_tree_service import PortfolioTreeService
        from users.permissions import require_active_family

        family = require_active_family(user)
        tree = PortfolioTreeService.build(
            owner=user,
            family_id=family.id,
            xirr_filters={"family": family_name} if family_name else {},
        )

        asset_category_by_class = {}
        for row in (investment_summary or {}).get("results", []):
            category = row.get("asset_category") or "Unassigned"
            asset_category_by_class[row.get("asset_class")] = category
            for raw_class in row.get("raw_asset_classes") or []:
                asset_category_by_class[raw_class] = category

        rows = []
        for family_node in tree.get("families", []):
            if family_name and family_node.get("family_name") != family_name:
                continue
            for portfolio in family_node.get("portfolios", []):
                for asset_class in portfolio.get("asset_classes", []):
                    for sub_class in asset_class.get("sub_classes", []):
                        for asset in sub_class.get("assets", []):
                            xirr = asset.get("xirr")
                            if xirr is None:
                                continue
                            try:
                                xirr_value = float(xirr)
                            except (TypeError, ValueError):
                                continue
                            rows.append({
                                "asset_name": asset.get("asset_name") or "Unnamed Asset",
                                "asset_class": sub_class.get("sub_class") or "Unassigned",
                                "xirr_percentage": xirr_value,
                                "underlying": asset.get("underlying") or asset.get("asset_name") or "Unnamed Underlying",
                                "asset_category": asset_category_by_class.get(
                                    sub_class.get("sub_class"),
                                    asset_category_by_class.get(asset_class.get("asset_class"), "Unassigned"),
                                ),
                            })

        rows.sort(key=lambda row: row["xirr_percentage"], reverse=True)
        return rows, tree

    @classmethod
    def _dashboard_investment_summary(cls, tree, family_name=None):
        groups = {}

        for family_node in tree.get("families", []):
            if family_name and family_node.get("family_name") != family_name:
                continue
            for portfolio in family_node.get("portfolios", []):
                for asset_class in portfolio.get("asset_classes", []):
                    category = (asset_class.get("asset_class") or "Unassigned").strip() or "Unassigned"
                    group = groups.setdefault(
                        category,
                        {
                            "asset_category": category,
                            "current_value": Decimal("0"),
                            "asset_classes": {},
                        },
                    )

                    for sub_class in asset_class.get("sub_classes", []):
                        sub_class_name = (sub_class.get("sub_class") or "Unassigned").strip() or "Unassigned"
                        row = group["asset_classes"].setdefault(
                            sub_class_name,
                            {
                                "asset_class": sub_class_name,
                                "current_value": Decimal("0"),
                                "raw_asset_classes": [],
                            },
                        )

                        if sub_class_name not in row["raw_asset_classes"]:
                            row["raw_asset_classes"].append(sub_class_name)

                        for asset in sub_class.get("assets", []):
                            current_value = cls._number(asset.get("current_value"))
                            group["current_value"] += current_value
                            row["current_value"] += current_value

        total = sum((group["current_value"] for group in groups.values()), Decimal("0"))
        result = []

        for group in groups.values():
            group_value = group["current_value"]
            result.append({
                "asset_category": group["asset_category"],
                "current_value": float(group_value),
                "percentage_of_total": round(
                    float((group_value / total) * Decimal("100")) if total else 0,
                    2,
                ),
                "asset_classes": [
                    {
                        "asset_class": row["asset_class"],
                        "current_value": float(row["current_value"]),
                        "percentage_of_total": round(
                            float((row["current_value"] / total) * Decimal("100")) if total else 0,
                            2,
                        ),
                        "raw_asset_classes": row["raw_asset_classes"],
                    }
                    for row in group["asset_classes"].values()
                ],
            })

        return result

    @classmethod
    def _insights(cls, performance, allocation, historical):
        best = performance[0] if performance else None
        worst = performance[-1] if performance else None

        allocation_rows = allocation.get("results", []) if isinstance(allocation, dict) else []
        largest = (
            max(
                allocation_rows,
                key=lambda row: cls._number(row.get("percentage")),
            )
            if allocation_rows
            else None
        )

        historical_rows = historical.get("results", []) if isinstance(historical, dict) else []
        period_value_change = Decimal("0")
        if len(historical_rows) >= 2:
            first = cls._number(historical_rows[0].get("portfolio_value"))
            last = cls._number(historical_rows[-1].get("portfolio_value"))
            if first > 0:
                period_value_change = ((last - first) / first) * Decimal("100")

        return {
            "best_performer": best,
            "worst_performer": worst,
            "largest_allocation": largest,
            "period_value_change": round(float(period_value_change), 2),
        }

    @classmethod
    def calculate(cls, user, *, historical_loader, family_name=None):
        from .investment_summary import InvestmentSummaryService
        from .mutual_fund_lookthrough import MutualFundLookThroughService
        from .unified_wealth import UnifiedWealthAnalytics

        summary = UnifiedWealthAnalytics.calculate_summary(user, family_name=family_name)
        investment_summary = InvestmentSummaryService.calculate(user, family_name=family_name)
        allocation_totals = {}
        for row in investment_summary.get("results", []):
            category = row.get("asset_category") or "Unassigned"
            allocation_totals[category] = allocation_totals.get(category, Decimal("0")) + cls._number(row.get("current_value"))
        allocation_total = sum(allocation_totals.values(), Decimal("0"))
        allocation = {
            "results": [
                {
                    "category": category,
                    "value": value,
                    "percentage": round((value / allocation_total) * Decimal("100"), 2) if allocation_total else Decimal("0"),
                }
                for category, value in sorted(
                    allocation_totals.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
                if value > 0
            ],
            "total_current_value": allocation_total,
        }
        advisor_allocation = InvestmentSummaryService.calculate_allocation_by_advisor(user)
        advisor_performance = {
            "results": InvestmentSummaryService.calculate_performance_by_advisor(user),
        }

        historical = historical_loader(user)
        performance, portfolio_tree = cls._investment_performance(user, family_name=family_name, investment_summary=investment_summary)

        market_cap_allocation = InvestmentSummaryService.calculate_market_cap_allocation(user)
        sector_allocation = MutualFundLookThroughService.sector_allocation(
            user,
            list(UnifiedWealthAnalytics.get_equity_holdings(user)),
            {
                asset_id
                for asset_id, raw_class in InvestmentSummaryService._equity_asset_class_by_asset_id(user).items()
                if InvestmentSummaryService._normalize_asset_class(raw_class)
                in InvestmentSummaryService.EQUITY_ASSET_CLASSES
            },
        )

        insights = cls._insights(performance, allocation, historical)

        from analytics.models import StandardAllocation
        standard_rows = StandardAllocation.objects.filter(family_name__isnull=True)
        if family_name:
            family_rows = {
                row.asset_category: row
                for row in StandardAllocation.objects.filter(family_name=family_name)
            }
            global_rows = {
                row.asset_category: row
                for row in standard_rows
            }
            standard_rows = list({**global_rows, **family_rows}.values())

        total_current_value = cls._number(summary.get("total_current_value"))
        standard_allocations = {}
        for row in standard_rows:
            percent = cls._number(row.allocation_percent)
            amount = (total_current_value * percent / Decimal("100")).quantize(Decimal("0.01"))
            standard_allocations[row.asset_category] = {
                "percent": float(percent),
                "amount": float(amount),
            }

        return {
            "summary": summary,
            "investment_summary": investment_summary,
            "dashboard_investment_summary": cls._dashboard_investment_summary(portfolio_tree, family_name=family_name),
            "allocation": allocation,
            "performance": {"results": performance},
            "advisor_allocation": advisor_allocation,
            "advisor_performance": advisor_performance,
            "xirr": {"xirr_percentage": summary.get("xirr_percentage")},
            "historical": historical,
            "market_cap_allocation": market_cap_allocation,
            "sector_allocation": sector_allocation,
            "insights": insights,
            "portfolio_tree": portfolio_tree,
            "standard_allocations": standard_allocations,
        }
