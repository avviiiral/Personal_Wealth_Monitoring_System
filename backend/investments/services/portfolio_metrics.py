from datetime import date
from decimal import Decimal

from investments.models import (
    Transaction,
    TransactionType,
)

from investments.services.portfolio_position_engine import (
    PortfolioPositionEngine,
)

from investments.services.xirr import (
    XIRRCalculator,
)

from market_data.models import (
    ManualAssetPrice,
    MarketPrice,
)


class PortfolioMetricsService:

    ZERO = Decimal("0")

    @staticmethod
    def get_current_price(asset):
        """
        Return the price that should be used for this asset.

        Priority:

            1. ManualAssetPrice  - an explicit override entered
               by the user always wins over scraped data.
            2. MarketPrice       - the latest automatically
               collected price.
            3. None              - the price is genuinely unknown.

        None is returned rather than zero. Zero is a real price
        and using it as a placeholder silently reports the holding
        as worthless and the position as a total loss.
        """

        manual_price = (
            ManualAssetPrice.objects
            .filter(asset=asset)
            .first()
        )

        if (
            manual_price is not None
            and manual_price.price is not None
        ):
            return manual_price.price

        latest_price = (
            MarketPrice.objects
            .filter(asset=asset)
            .order_by(
                "-date",
                "-id",
            )
            .first()
        )

        if latest_price is None:
            return None

        return latest_price.close_price

    @staticmethod
    def get_price_metadata(asset):
        """
        Return the source and as-of date for the price returned
        by get_current_price, so the UI can distinguish a manual
        override from a live quote and a fresh price from a
        stale one.
        """

        manual_price = (
            ManualAssetPrice.objects
            .filter(asset=asset)
            .first()
        )

        if (
            manual_price is not None
            and manual_price.price is not None
        ):
            return {
                "price_source": "MANUAL",
                "price_date": manual_price.price_date,
            }

        latest_price = (
            MarketPrice.objects
            .filter(asset=asset)
            .order_by(
                "-date",
                "-id",
            )
            .first()
        )

        if latest_price is None:
            return {
                "price_source": None,
                "price_date": None,
            }

        return {
            "price_source": latest_price.source,
            "price_date": latest_price.date,
        }

    @classmethod
    def calculate_asset_metrics(
        cls,
        owner,
        family_name,
        portfolio,
        asset,
    ):
        """
        Calculate metrics for:

            Family
                -> Portfolio
                    -> Asset
        """

        position = (
            PortfolioPositionEngine
            .calculate_position(
                owner=owner,
                family_name=family_name,
                portfolio=portfolio,
                asset=asset,
            )
        )

        quantity = (
            position["quantity"]
        )

        invested_value = (
            position["invested_value"]
        )

        average_cost = (
            position["average_cost"]
        )

        # --------------------------------------------------
        # Current market value
        # --------------------------------------------------

        current_price = cls.get_current_price(
            asset
        )

        price_metadata = cls.get_price_metadata(
            asset
        )

        # --------------------------------------------------
        # Current value
        #
        # When the price is unknown the value, P&L and P&L %
        # are unknown too. They stay None rather than being
        # zero-filled, so the UI can show "-" instead of
        # reporting a fabricated total loss.
        # --------------------------------------------------

        if current_price is None:

            current_value = None
            gain = None
            pnl_percentage = None

        else:

            current_value = (
                quantity
                * current_price
            )

            gain = (
                current_value
                - invested_value
            )

            if invested_value > 0:

                pnl_percentage = (
                    gain
                    / invested_value
                ) * Decimal("100")

            else:

                pnl_percentage = cls.ZERO

        # --------------------------------------------------
        # XIRR cash flows
        # --------------------------------------------------

        transactions = (
            Transaction.objects
            .filter(
                owner=owner,
                family_name=family_name,
                portfolio=portfolio,
                asset=asset,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        cash_flows = []

        for tx in transactions:

            amount = (
                tx.amount
                or cls.ZERO
            )

            # Dividend reinvestment is not
            # fresh external cash.
            if (
                tx.notes
                == "DIVIDEND REINVESTMENT"
            ):
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

            elif tx.transaction_type == (
                TransactionType.SELL
            ):

                cash_flows.append(
                    (
                        tx.transaction_date,
                        float(amount),
                    )
                )

        # --------------------------------------------------
        # Current value as terminal cash flow
        # --------------------------------------------------

        if (
            quantity > 0
            and current_value is not None
            and current_value > 0
        ):

            cash_flows.append(
                (
                    date.today(),
                    float(current_value),
                )
            )

        xirr = (
            XIRRCalculator.calculate(
                cash_flows
            )
            if len(cash_flows) >= 2
            else None
        )

        return {
            "quantity": quantity,

            "average_cost": average_cost,

            "invested_value": invested_value,

            "current_price": current_price,

            "current_value": current_value,

            "pnl": gain,

            "pnl_percentage": (
                None
                if pnl_percentage is None
                else round(
                    float(pnl_percentage),
                    2,
                )
            ),

            "price_source": price_metadata[
                "price_source"
            ],

            "price_date": price_metadata[
                "price_date"
            ],

            "xirr": xirr,
        }