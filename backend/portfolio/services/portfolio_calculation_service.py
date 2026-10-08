from collections import defaultdict
from decimal import Decimal

from portfolio.services.portfolio_tree_service import PortfolioTreeService


class PortfolioCalculationService:
    """Authoritative financial aggregations for Portfolio and Reports UI."""

    @staticmethod
    def _number(value):
        if value is None:
            return Decimal("0")
        return Decimal(str(value))

    @classmethod
    def _asset_rows(cls, tree):
        rows = []
        for family in tree.get("families", []):
            for portfolio in family.get("portfolios", []):
                for asset_class in portfolio.get("asset_classes", []):
                    for sub_class in asset_class.get("sub_classes", []):
                        for asset in sub_class.get("assets", []):
                            rows.append({
                                "family_name": family.get("family_name") or "Unassigned",
                                "portfolio": portfolio.get("portfolio") or "Unassigned",
                                "asset_class": asset_class.get("asset_class") or "Unassigned",
                                "sub_class": sub_class.get("sub_class") or "Unassigned",
                                "asset": asset,
                            })
        return rows

    @classmethod
    def _matches(cls, row, family=None, asset_class=None, advisor=None):
        asset = row["asset"]
        if family and row["family_name"] != family:
            return False
        if asset_class and row["asset_class"] != asset_class:
            return False
        if advisor and (asset.get("advisors") or "").strip() != advisor:
            return False
        return True

    @classmethod
    def _xirr(cls, transactions, assets):
        if not transactions:
            return None

        unique_assets = {}
        for item in assets:
            unique_assets[item["asset"].get("id")] = item["asset"]
        asset_ids = set(unique_assets)
        selected_transactions = [
            tx for tx in transactions
            if tx.asset_id in asset_ids
        ]
        if not selected_transactions:
            return None

        quantity = sum(
            (cls._number(asset.get("quantity")) for asset in unique_assets.values()),
            Decimal("0"),
        )
        current_value_by_asset = {
            asset_id: cls._number(asset.get("current_value"))
            for asset_id, asset in unique_assets.items()
        }
        current_value = sum(current_value_by_asset.values(), Decimal("0"))

        return PortfolioTreeService._calculate_xirr(
            selected_transactions,
            quantity,
            current_value if quantity > 0 and current_value > 0 else None,
        )

    @classmethod
    def _row(cls, group_key, assets, transactions):
        quantity = sum(
            (cls._number(item["asset"].get("quantity")) for item in assets),
            Decimal("0"),
        )
        invested = sum(
            (cls._number(item["asset"].get("invested_value")) for item in assets),
            Decimal("0"),
        )
        current = sum(
            (cls._number(item["asset"].get("current_value")) for item in assets),
            Decimal("0"),
        )
        pnl = current - invested
        return {
            **group_key,
            "quantity": float(quantity),
            "invested_value": float(invested),
            "current_value": float(current),
            "pnl": float(pnl),
            "xirr": cls._xirr(transactions, assets),
        }

    @classmethod
    def calculate(
        cls,
        owner,
        family_id,
        family=None,
        asset_class=None,
        advisor=None,
    ):
        # Build the complete authoritative asset tree first. Filtering here
        # selects already-calculated positions; it never reconstructs a
        # position from a filtered transaction subset.
        tree = tree or PortfolioTreeService.build(
            owner=owner,
            family_id=family_id,
            xirr_filters={},
        )
        rows = [
            row for row in cls._asset_rows(tree)
            if cls._matches(row, family=family, asset_class=asset_class, advisor=advisor)
        ]

        owner_ids = [owner.pk] if hasattr(owner, "pk") else list(owner)
        transactions = list(
            PortfolioTreeService._get_transactions(owner_ids, family_id=family_id)
        )

        subclass_groups = defaultdict(list)
        asset_name_groups = defaultdict(list)
        family_subclass_groups = defaultdict(list)

        for row in rows:
            subclass_groups[row["sub_class"]].append(row)
            asset_name_groups[(row["sub_class"], row["asset"].get("asset_name") or "Unassigned")].append(row)
            family_subclass_groups[(row["family_name"], row["sub_class"])].append(row)

        subclasses = [
            cls._row({"sub_class": key}, group, transactions)
            for key, group in subclass_groups.items()
        ]
        subclasses.sort(key=lambda item: item["sub_class"].casefold())

        asset_names = [
            cls._row(
                {
                    "sub_class": key[0],
                    "asset_name": key[1],
                    "family_names": sorted(
                        {item["family_name"] for item in group},
                        key=str.casefold,
                    ),
                },
                group,
                transactions,
            )
            for key, group in asset_name_groups.items()
        ]
        asset_names.sort(
            key=lambda item: (item["sub_class"].casefold(), item["asset_name"].casefold())
        )

        family_subclasses = [
            cls._row(
                {"family_name": key[0], "sub_class": key[1]},
                group,
                transactions,
            )
            for key, group in family_subclass_groups.items()
        ]
        family_subclasses.sort(
            key=lambda item: (item["family_name"].casefold(), item["sub_class"].casefold())
        )

        return {
            "subclasses": subclasses,
            "asset_names": asset_names,
            "family_subclasses": family_subclasses,
        }
