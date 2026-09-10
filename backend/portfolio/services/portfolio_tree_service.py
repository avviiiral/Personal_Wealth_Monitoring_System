import logging
from datetime import date
from decimal import Decimal

from django.db.models import OuterRef, QuerySet, Subquery

from investments.models import Transaction, TransactionType
from investments.services.xirr import XIRRCalculator
from market_data.models import ManualAssetPrice, MarketPrice


logger = logging.getLogger(__name__)


class PortfolioTreeService:
    ZERO = Decimal("0")

    @staticmethod
    def _clean(value, default="Unassigned"):
        if value is None:
            return default

        value = str(value).strip()
        return value or default

    @staticmethod
    def _decimal_to_float(value):
        if value is None:
            return 0.0

        return float(value)

    @staticmethod
    def _optional_float(value):
        if value is None:
            return None

        return float(value)

    @classmethod
    def _get_transactions(cls, family_group_id=None, owner_ids=None) -> QuerySet:
        transactions = Transaction.objects.all()

        if family_group_id is not None:
            transactions = transactions.filter(
                family_group_id=family_group_id,
            )
        elif owner_ids is not None:
            transactions = transactions.filter(
                owner_id__in=owner_ids,
            )
        else:
            transactions = transactions.none()

        return (
            transactions
            .select_related(
                "owner",
                "asset",
                "asset__security_master",
            )
            .order_by(
                "family_name",
                "portfolio",
                "asset_class",
                "sub_class",
                "asset_name",
                "transaction_date",
                "id",
            )
        )

    @staticmethod
    def _calculate_position(transactions):
        quantity = Decimal("0")
        invested_value = Decimal("0")

        for tx in transactions:
            tx_quantity = tx.quantity or Decimal("0")
            tx_amount = tx.amount or Decimal("0")

            transaction_type = (
                str(tx.transaction_type)
                .strip()
                .upper()
            )

            if transaction_type in ("BUY", "SIP"):
                quantity += tx_quantity
                invested_value += tx_amount

            elif transaction_type == "SELL":
                if tx_quantity <= 0 or quantity <= 0:
                    continue

                average_cost = (
                    invested_value / quantity
                    if quantity > 0
                    else Decimal("0")
                )

                sell_quantity = min(tx_quantity, quantity)
                quantity -= sell_quantity
                invested_value -= average_cost * sell_quantity

                if quantity <= 0:
                    quantity = Decimal("0")
                    invested_value = Decimal("0")

            elif transaction_type in ("BONUS", "SPLIT"):
                quantity += max(tx_quantity, Decimal("0"))

        average_cost = (
            invested_value / quantity
            if quantity > 0
            else Decimal("0")
        )

        return {
            "quantity": quantity,
            "invested_value": invested_value,
            "average_cost": average_cost,
        }

    @staticmethod
    def _calculate_xirr(transactions, current_quantity, current_value):
        """
        Preserve the existing PortfolioMetricsService XIRR semantics.

        The previous implementation queried all transactions for the
        same owner + family + portfolio + asset (without sub_class),
        skipped DIVIDEND REINVESTMENT rows, treated BUY/SIP as negative
        cash flow and SELL as positive cash flow, and added today's
        current value when the position and current value were positive.

        The tree already has those transactions in memory, so the same
        calculation can be performed without another transaction query.
        """
        cash_flows = []

        for tx in transactions:
            amount = tx.amount or Decimal("0")

            if tx.notes == "DIVIDEND REINVESTMENT":
                continue

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                cash_flows.append(
                    (
                        tx.transaction_date,
                        -float(amount),
                    )
                )

            elif tx.transaction_type == TransactionType.SELL:
                cash_flows.append(
                    (
                        tx.transaction_date,
                        float(amount),
                    )
                )

        if (
            current_quantity > 0
            and current_value is not None
            and current_value > 0
        ):
            cash_flows.append(
                (
                    date.today(),
                    float(current_value),
                )
            )

        return (
            XIRRCalculator.calculate(cash_flows)
            if len(cash_flows) >= 2
            else None
        )

    @classmethod
    def _load_price_cache(cls, asset_ids):
        """
        Load price data in bulk while preserving existing priority:

            1. ManualAssetPrice
            2. latest MarketPrice
            3. None

        Manual prices are one-to-one. For market data, a correlated
        subquery selects only the latest row per asset instead of loading
        the full historical price table into Python.
        """
        if not asset_ids:
            return {}

        price_cache = {}

        manual_prices = (
            ManualAssetPrice.objects
            .filter(asset_id__in=asset_ids)
        )

        for manual in manual_prices:
            price_cache[manual.asset_id] = {
                "current_price": manual.price,
                "price_source": "MANUAL",
                "price_date": manual.price_date,
            }

        latest_market_id = (
            MarketPrice.objects
            .filter(asset_id=OuterRef("asset_id"))
            .order_by("-date", "-id")
            .values("id")[:1]
        )

        market_prices = (
            MarketPrice.objects
            .filter(
                asset_id__in=asset_ids,
                id=Subquery(latest_market_id),
            )
        )

        for market in market_prices:
            if market.asset_id in price_cache:
                continue

            price_cache[market.asset_id] = {
                "current_price": market.close_price,
                "price_source": market.source,
                "price_date": market.date,
            }

        return price_cache

    @classmethod
    def _build_asset(
        cls,
        transactions,
        xirr_transactions,
        price_cache,
    ):
        first = transactions[0]
        asset = first.asset

        position = cls._calculate_position(transactions)

        quantity = position["quantity"]
        invested_value = position["invested_value"]
        average_cost = position["average_cost"]

        price_data = price_cache.get(asset.id, {})
        current_price = price_data.get("current_price")

        if current_price is not None:
            current_price_decimal = Decimal(str(current_price))
            current_value = quantity * current_price_decimal
        else:
            current_value = None

        if current_value is not None:
            pnl = current_value - invested_value
        else:
            pnl = None

        if pnl is not None and invested_value > Decimal("0"):
            pnl_percentage = (
                pnl / invested_value
            ) * Decimal("100")
        else:
            pnl_percentage = None

        xirr = cls._calculate_xirr(
            xirr_transactions,
            quantity,
            current_value,
        )

        security_master = getattr(
            asset,
            "security_master",
            None,
        )

        asset_name = cls._clean(
            first.asset_name,
            getattr(asset, "name", "Unassigned"),
        )

        return {
            "id": asset.id,
            "family_name": cls._clean(first.family_name),
            "asset_name": asset_name,
            "underlying": cls._clean(first.underlying, ""),
            "isin": getattr(asset, "isin", None),
            "symbol": getattr(asset, "symbol", None),
            "advisors": cls._clean(first.advisors, ""),
            "quantity": cls._decimal_to_float(quantity),
            "average_cost": cls._decimal_to_float(average_cost),
            "invested_value": cls._decimal_to_float(invested_value),
            "current_price": cls._optional_float(current_price),
            "current_value": cls._optional_float(current_value),
            "pnl": cls._optional_float(pnl),
            "pnl_percentage": cls._optional_float(pnl_percentage),
            "price_source": price_data.get("price_source"),
            "price_date": (
                str(price_data["price_date"])
                if price_data.get("price_date")
                else None
            ),
            "xirr": xirr,
            "sector": (
                getattr(security_master, "sector", None)
                if security_master
                else None
            ),
            "cap_type": (
                getattr(security_master, "cap_type", None)
                if security_master
                else None
            ),
            "amc_name": (
                getattr(security_master, "amc_name", None)
                if security_master
                else None
            ),
            "pe_ratio": (
                cls._optional_float(
                    getattr(security_master, "pe_ratio", None)
                )
                if security_master
                else None
            ),
            "pb_ratio": (
                cls._optional_float(
                    getattr(security_master, "pb_ratio", None)
                )
                if security_master
                else None
            ),
            "roe": (
                cls._optional_float(
                    getattr(security_master, "roe", None)
                )
                if security_master
                else None
            ),
            "credit_rating": (
                getattr(security_master, "credit_rating", None)
                if security_master
                else None
            ),
            "ytm": (
                cls._optional_float(
                    getattr(security_master, "ytm", None)
                )
                if security_master
                else None
            ),
            "modified_duration": (
                cls._optional_float(
                    getattr(security_master, "modified_duration", None)
                )
                if security_master
                else None
            ),
            "average_maturity": (
                cls._optional_float(
                    getattr(security_master, "average_maturity", None)
                )
                if security_master
                else None
            ),
        }

    @classmethod
    def build(cls, owner=None, family_group_id=None):
        if family_group_id is not None:
            transactions = list(
                cls._get_transactions(
                    family_group_id=family_group_id,
                )
            )
        elif owner is not None:
            owner_ids = [owner.pk] if hasattr(owner, "pk") else list(owner)
            transactions = list(
                cls._get_transactions(
                    owner_ids=owner_ids,
                )
            )
        else:
            transactions = []

        tree = {}
        grouped = {}
        xirr_grouped = {}

        for tx in transactions:
            family = cls._clean(tx.family_name)
            portfolio = cls._clean(tx.portfolio)
            asset_class = cls._clean(tx.asset_class)
            sub_class = cls._clean(tx.sub_class)

            group_key = (
                family,
                portfolio,
                asset_class,
                sub_class,
                tx.asset_id,
            )
            grouped.setdefault(group_key, []).append(tx)

            # Preserve the old XIRR grouping: owner + family +
            # portfolio + asset, intentionally without sub_class.
            xirr_key = (
                tx.owner_id,
                family,
                portfolio,
                tx.asset_id,
            )
            xirr_grouped.setdefault(xirr_key, []).append(tx)

        asset_ids = {tx.asset_id for tx in transactions}
        price_cache = cls._load_price_cache(asset_ids)

        for (
            family,
            portfolio,
            asset_class,
            sub_class,
            asset_id,
        ), asset_transactions in grouped.items():
            first = asset_transactions[0]
            xirr_key = (
                first.owner_id,
                family,
                portfolio,
                asset_id,
            )

            asset_data = cls._build_asset(
                transactions=asset_transactions,
                xirr_transactions=xirr_grouped.get(xirr_key, []),
                price_cache=price_cache,
            )

            if asset_data["quantity"] <= 0:
                continue

            family_data = tree.setdefault(
                family,
                {
                    "family_name": family,
                    "portfolios": {},
                },
            )

            portfolio_data = family_data["portfolios"].setdefault(
                portfolio,
                {
                    "portfolio": portfolio,
                    "asset_classes": {},
                },
            )

            asset_class_data = portfolio_data["asset_classes"].setdefault(
                asset_class,
                {
                    "asset_class": asset_class,
                    "sub_classes": {},
                },
            )

            subclass_data = asset_class_data["sub_classes"].setdefault(
                sub_class,
                {
                    "sub_class": sub_class,
                    "assets": [],
                },
            )

            subclass_data["assets"].append(asset_data)

        families = []

        for family_data in tree.values():
            portfolios = []

            for portfolio_data in family_data["portfolios"].values():
                asset_classes = []

                for asset_class_data in portfolio_data["asset_classes"].values():
                    sub_classes = []

                    for sub_class_data in asset_class_data["sub_classes"].values():
                        sub_class_data["assets"].sort(
                            key=lambda item: (item["asset_name"] or "").lower()
                        )
                        sub_class_data["asset_count"] = len(
                            sub_class_data["assets"]
                        )
                        sub_classes.append(sub_class_data)

                    sub_classes.sort(
                        key=lambda item: (item["sub_class"] or "").lower()
                    )
                    asset_class_data["sub_classes"] = sub_classes
                    asset_class_data["sub_class_count"] = len(sub_classes)
                    asset_classes.append(asset_class_data)

                asset_classes.sort(
                    key=lambda item: (item["asset_class"] or "").lower()
                )
                portfolio_data["asset_classes"] = asset_classes
                portfolio_data["asset_class_count"] = len(asset_classes)
                portfolios.append(portfolio_data)

            portfolios.sort(
                key=lambda item: (item["portfolio"] or "").lower()
            )
            family_data["portfolios"] = portfolios
            family_data["portfolio_count"] = len(portfolios)
            families.append(family_data)

        families.sort(
            key=lambda item: (item["family_name"] or "").lower()
        )

        return {
            "count": len(families),
            "families": families,
        }
