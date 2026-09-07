from decimal import Decimal

from analytics.services.unified_wealth import UnifiedWealthAnalytics
from investments.models import Transaction
from mutual_funds.models import (
    MutualFundTransaction,
    SIP,
)


class PortfolioContextBuilder:
    """
    Builds a user-specific portfolio context for the AI chatbot.

    The AI never calculates portfolio values itself.
    All financial values come from PWMS database records
    and existing analytics services.
    """

    @staticmethod
    def decimal_to_float(value):
        if isinstance(value, Decimal):
            return float(value)

        return value

    @staticmethod
    def serialize_value(value):
        if isinstance(value, Decimal):
            return float(value)

        if hasattr(value, "isoformat"):
            return value.isoformat()

        return value

    @staticmethod
    def build(user):
        summary = UnifiedWealthAnalytics.calculate_summary(user)

        allocation = UnifiedWealthAnalytics.calculate_allocation(user)

        performance = UnifiedWealthAnalytics.calculate_performance(user)

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        equity_transactions = (
            Transaction.objects
            .filter(owner=user)
            .select_related("asset")
            .order_by(
                "-transaction_date",
                "-created_at",
            )[:100]
        )

        mutual_fund_transactions = (
            MutualFundTransaction.objects
            .filter(owner=user)
            .select_related("scheme")
            .order_by(
                "-transaction_date",
                "-created_at",
            )[:100]
        )

        sips = (
            SIP.objects
            .filter(owner=user)
            .select_related("scheme")
            .order_by("next_installment_date")
        )

        equity = []

        for holding in equity_holdings:
            equity.append({
                "holding_id": holding.id,
                "name": holding.asset.name,
                "category": holding.asset.category,
                "symbol": holding.asset.symbol,
                "isin": holding.asset.isin,
                "institution": holding.asset.institution,
                "currency": holding.asset.currency,
                "quantity": PortfolioContextBuilder.serialize_value(
                    holding.quantity
                ),
                "average_cost": PortfolioContextBuilder.serialize_value(
                    holding.average_cost
                ),
                "invested_value": PortfolioContextBuilder.serialize_value(
                    holding.invested_value
                ),
                "current_price": PortfolioContextBuilder.serialize_value(
                    holding.current_price
                ),
                "current_value": PortfolioContextBuilder.serialize_value(
                    holding.current_value
                ),
                "unrealized_pnl": PortfolioContextBuilder.serialize_value(
                    holding.unrealized_pnl
                ),
                "updated_at": PortfolioContextBuilder.serialize_value(
                    holding.updated_at
                ),
            })

        mutual_funds = []

        for holding in mutual_fund_holdings:
            mutual_funds.append({
                "holding_id": holding.id,
                "scheme_name": holding.scheme.scheme_name,
                "amc_name": holding.scheme.amc_name,
                "scheme_code": holding.scheme.scheme_code,
                "category": holding.scheme.category,
                "plan": holding.scheme.plan,
                "option": holding.scheme.option,
                "units": PortfolioContextBuilder.serialize_value(
                    holding.units
                ),
                "invested_value": PortfolioContextBuilder.serialize_value(
                    holding.invested_value
                ),
                "average_nav": PortfolioContextBuilder.serialize_value(
                    holding.average_nav
                ),
                "current_nav": PortfolioContextBuilder.serialize_value(
                    holding.current_nav
                ),
                "current_value": PortfolioContextBuilder.serialize_value(
                    holding.current_value
                ),
                "unrealized_pnl": PortfolioContextBuilder.serialize_value(
                    holding.unrealized_pnl
                ),
                "updated_at": PortfolioContextBuilder.serialize_value(
                    holding.updated_at
                ),
            })

        transactions = []

        for transaction in equity_transactions:
            transactions.append({
                "type": "EQUITY",
                "asset": transaction.asset.name,
                "symbol": transaction.asset.symbol,
                "transaction_type": transaction.transaction_type,
                "transaction_date": PortfolioContextBuilder.serialize_value(
                    transaction.transaction_date
                ),
                "quantity": PortfolioContextBuilder.serialize_value(
                    transaction.quantity
                ),
                "price_per_unit": PortfolioContextBuilder.serialize_value(
                    transaction.price_per_unit
                ),
                "amount": PortfolioContextBuilder.serialize_value(
                    transaction.amount
                ),
                "fees": PortfolioContextBuilder.serialize_value(
                    transaction.fees
                ),
                "notes": transaction.notes,
            })

        for transaction in mutual_fund_transactions:
            transactions.append({
                "type": "MUTUAL_FUND",
                "scheme_name": transaction.scheme.scheme_name,
                "transaction_type": transaction.transaction_type,
                "transaction_date": PortfolioContextBuilder.serialize_value(
                    transaction.transaction_date
                ),
                "units": PortfolioContextBuilder.serialize_value(
                    transaction.units
                ),
                "nav": PortfolioContextBuilder.serialize_value(
                    transaction.nav
                ),
                "amount": PortfolioContextBuilder.serialize_value(
                    transaction.amount
                ),
                "fees": PortfolioContextBuilder.serialize_value(
                    transaction.fees
                ),
                "notes": transaction.notes,
            })

        sip_data = []

        for sip in sips:
            sip_data.append({
                "scheme_name": sip.scheme.scheme_name,
                "amc_name": sip.scheme.amc_name,
                "amount": PortfolioContextBuilder.serialize_value(
                    sip.amount
                ),
                "frequency": sip.frequency,
                "start_date": PortfolioContextBuilder.serialize_value(
                    sip.start_date
                ),
                "end_date": PortfolioContextBuilder.serialize_value(
                    sip.end_date
                ),
                "next_installment_date": (
                    PortfolioContextBuilder.serialize_value(
                        sip.next_installment_date
                    )
                ),
                "is_active": sip.is_active,
            })

        return {
            "user": {
                "id": user.id,
                "username": user.username,
            },

            "summary": {
                key: PortfolioContextBuilder.serialize_value(value)
                if not isinstance(value, dict)
                else {
                    nested_key:
                    PortfolioContextBuilder.serialize_value(
                        nested_value
                    )
                    for nested_key, nested_value in value.items()
                }
                for key, value in summary.items()
            },

            "allocation": [
                {
                    key: PortfolioContextBuilder.serialize_value(
                        value
                    )
                    for key, value in item.items()
                }
                for item in allocation
            ],

            "performance": [
                {
                    key: PortfolioContextBuilder.serialize_value(
                        value
                    )
                    for key, value in item.items()
                }
                for item in performance
            ],

            "equity_holdings": equity,

            "mutual_fund_holdings": mutual_funds,

            "transactions": transactions,

            "sips": sip_data,
        }