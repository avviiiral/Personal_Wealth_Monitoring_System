from collections import defaultdict
from decimal import Decimal
from datetime import date

from django.db.models import OuterRef, Subquery

from mutual_funds.models import MutualFundHolding, MutualFundTransaction
from portfolio.services.portfolio_tree_service import PortfolioTreeService


class MISReportService:
    """Build the MIS report from the same trusted portfolio valuation sources."""

    @staticmethod
    def _clean(value, default="Unassigned"):
        value = str(value or "").strip()
        return value or default

    @classmethod
    def build(cls, family):
        tree = PortfolioTreeService.build(owner=[], family_id=family.id)

        holdings = []
        for family_node in tree.get("families", []):
            for portfolio_node in family_node.get("portfolios", []):
                for asset_class_node in portfolio_node.get("asset_classes", []):
                    asset_class = cls._clean(asset_class_node.get("asset_class"))
                    for subclass_node in asset_class_node.get("sub_classes", []):
                        sub_class = cls._clean(subclass_node.get("sub_class"))
                        for asset in subclass_node.get("assets", []):
                            holdings.append({
                                "family_name": family.name,
                                "portfolio": cls._clean(portfolio_node.get("portfolio")),
                                "asset_class": asset_class,
                                "sub_class": sub_class,
                                "asset_name": cls._clean(asset.get("asset_name")),
                                "asset_id": asset.get("id"),
                                "isin": asset.get("isin"),
                                "symbol": asset.get("symbol"),
                                "quantity": asset.get("quantity"),
                                "average_cost": asset.get("average_cost"),
                                "invested_value": asset.get("invested_value") or 0,
                                "current_price": asset.get("current_price"),
                                "current_value": asset.get("current_value") or 0,
                                "pnl": asset.get("pnl") or 0,
                                "pnl_percentage": asset.get("pnl_percentage"),
                                "xirr": asset.get("xirr"),
                            })

        latest_tx = (
            MutualFundTransaction.objects
            .filter(
                family_id=family.id,
                scheme_id=OuterRef("scheme_id"),
            )
            .order_by("-transaction_date", "-created_at", "-id")
        )
        mf_holdings = (
            MutualFundHolding.objects
            .filter(
                family_id=family.id,
                scheme__is_active=True,
                units__gt=0,
            )
            .select_related("scheme")
            .annotate(
                latest_family_name=Subquery(latest_tx.values("family_name")[:1]),
                latest_portfolio=Subquery(latest_tx.values("portfolio")[:1]),
            )
            .order_by("scheme__scheme_name")
        )

        for holding in mf_holdings:
            invested = Decimal(holding.invested_value or 0)
            current = Decimal(holding.current_value or 0)
            pnl = Decimal(holding.unrealized_pnl or 0)
            holdings.append({
                "family_name": family.name,
                "portfolio": cls._clean(holding.latest_portfolio),
                "asset_class": "Mutual Funds",
                "sub_class": cls._clean(holding.scheme.category),
                "asset_name": holding.scheme.scheme_name,
                "asset_id": None,
                "isin": holding.scheme.isin_growth or holding.scheme.isin_dividend,
                "symbol": holding.scheme.scheme_code,
                "quantity": float(holding.units or 0),
                "average_cost": float(holding.average_nav or 0),
                "invested_value": float(invested),
                "current_price": float(holding.current_nav or 0),
                "current_value": float(current),
                "pnl": float(pnl),
                "pnl_percentage": round(float(pnl / invested * 100), 2) if invested else 0,
                "xirr": None,
            })

        # Match the Portfolio page's Asset Name level: positions are
        # consolidated by Sub Class + Asset Name, rather than exposing
        # each underlying/position row separately. Family Name remains
        # visible on the consolidated holding row.
        consolidated = {}
        for item in holdings:
            key = (
                cls._clean(item.get("sub_class")),
                cls._clean(item.get("asset_name")),
            )
            bucket = consolidated.setdefault(
                key,
                {
                    **item,
                    "portfolio_names": set(),
                    "asset_classes": set(),
                    "asset_ids": set(),
                    "isins": set(),
                    "symbols": set(),
                    "quantity": Decimal("0"),
                    "invested_value": Decimal("0"),
                    "current_value": Decimal("0"),
                    "pnl": Decimal("0"),
                },
            )

            if item.get("portfolio"):
                bucket["portfolio_names"].add(str(item["portfolio"]).strip())
            if item.get("asset_class"):
                bucket["asset_classes"].add(str(item["asset_class"]).strip())
            if item.get("asset_id") is not None:
                bucket["asset_ids"].add(item["asset_id"])
            if item.get("isin"):
                bucket["isins"].add(str(item["isin"]).strip())
            if item.get("symbol"):
                bucket["symbols"].add(str(item["symbol"]).strip())

            bucket["quantity"] += Decimal(str(item.get("quantity") or 0))
            bucket["invested_value"] += Decimal(str(item.get("invested_value") or 0))
            bucket["current_value"] += Decimal(str(item.get("current_value") or 0))
            bucket["pnl"] += Decimal(str(item.get("pnl") or 0))

            if bucket.get("xirr") is None and item.get("xirr") is not None:
                bucket["xirr"] = item["xirr"]

        holdings = []
        for bucket in consolidated.values():
            quantity = bucket["quantity"]
            invested = bucket["invested_value"]
            current = bucket["current_value"]
            pnl = bucket["pnl"]

            bucket["portfolio"] = ", ".join(sorted(bucket["portfolio_names"], key=str.casefold)) or "Unassigned"
            bucket["asset_class"] = ", ".join(sorted(bucket["asset_classes"], key=str.casefold)) or "Unassigned"
            bucket["asset_id"] = next(iter(bucket["asset_ids"])) if len(bucket["asset_ids"]) == 1 else None
            bucket["isin"] = next(iter(bucket["isins"])) if len(bucket["isins"]) == 1 else None
            bucket["symbol"] = next(iter(bucket["symbols"])) if len(bucket["symbols"]) == 1 else None
            bucket["quantity"] = float(quantity)
            bucket["invested_value"] = float(invested)
            bucket["current_value"] = float(current)
            bucket["pnl"] = float(pnl)
            bucket["average_cost"] = float(invested / quantity) if quantity else None
            bucket["current_price"] = float(current / quantity) if quantity else None
            bucket["pnl_percentage"] = float(pnl / invested * Decimal("100")) if invested else 0
            bucket["portfolio_names"] = None
            bucket["asset_classes"] = None
            bucket["asset_ids"] = None
            bucket["isins"] = None
            bucket["symbols"] = None
            holdings.append(bucket)

        holdings.sort(
            key=lambda item: (
                str(item["sub_class"]).casefold(),
                str(item["asset_name"]).casefold(),
            )
        )

        total_invested = sum(Decimal(str(item["invested_value"] or 0)) for item in holdings)
        total_current = sum(Decimal(str(item["current_value"] or 0)) for item in holdings)
        total_pnl = total_current - total_invested

        summary = {
            "total_invested": total_invested,
            "total_current_value": total_current,
            "total_pnl": total_pnl,
            "pnl_percentage": (
                total_pnl / total_invested * Decimal("100")
                if total_invested else Decimal("0")
            ),
            "number_of_holdings": len(holdings),
        }

        asset_classes = defaultdict(lambda: {"invested_value": Decimal("0"), "current_value": Decimal("0")})
        for item in holdings:
            bucket = asset_classes[item["asset_class"]]
            bucket["invested_value"] += Decimal(str(item["invested_value"] or 0))
            bucket["current_value"] += Decimal(str(item["current_value"] or 0))

        asset_class_summary = []
        for name in sorted(asset_classes, key=str.casefold):
            bucket = asset_classes[name]
            pnl = bucket["current_value"] - bucket["invested_value"]
            asset_class_summary.append({
                "asset_class": name,
                "invested_value": bucket["invested_value"],
                "current_value": bucket["current_value"],
                "pnl": pnl,
                "pnl_percentage": (
                    pnl / bucket["invested_value"] * Decimal("100")
                    if bucket["invested_value"] else Decimal("0")
                ),
            })

        return {
            "family_name": family.name,
            "reporting_date": date.today(),
            "summary": summary,
            "asset_class_summary": asset_class_summary,
            "holdings": holdings,
        }
