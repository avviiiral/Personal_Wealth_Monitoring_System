from django.db.models import Q
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q

from users.permissions import get_active_family_group, is_system_owner
from investments.models import Transaction, TransactionType
from mutual_funds.models import MutualFundTransaction, MutualFundTransactionType

from .investment_summary import InvestmentSummaryService
from .xirr import XIRRCalculator


class XIRRByAssetClassService:
    """Calculate true cash-flow XIRR for each canonical Asset Class."""

    ZERO = Decimal("0")

    @staticmethod
    def _scope_q(user):
        if is_system_owner(user):
            return Q()
        family = get_active_family_group(user)
        if family is None:
            return Q(pk__in=[])
        owner_ids = XIRRByAssetClassService._owner_ids(user)
        return Q(family_id=family.id)

    @classmethod
    def calculate(cls, user, family_name=None):
        cash_flows_by_class = defaultdict(list)

        equity_class_by_asset_id = InvestmentSummaryService._equity_asset_class_by_asset_id(
            user,
            family_name=family_name,
        )

        equity_qs = Transaction.objects.filter(XIRRByAssetClassService._scope_q(user))
        if family_name:
            equity_qs = equity_qs.filter(family_name=family_name)

        for transaction in equity_qs.order_by(
            "transaction_date", "created_at", "id"
        ):
            raw_class = equity_class_by_asset_id.get(transaction.asset_id)
            asset_class = InvestmentSummaryService._normalize_asset_class(raw_class)
            amount = transaction.amount or cls.ZERO
            fees = transaction.fees or cls.ZERO

            if transaction.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
                TransactionType.DEPOSIT,
            ):
                cash_flows_by_class[asset_class].append(
                    (transaction.transaction_date, -(amount + fees))
                )
            elif transaction.transaction_type in (
                TransactionType.SELL,
                TransactionType.DIVIDEND,
                TransactionType.INTEREST,
                TransactionType.WITHDRAWAL,
            ):
                cash_flows_by_class[asset_class].append(
                    (transaction.transaction_date, amount - fees)
                )

        mutual_fund_qs = MutualFundTransaction.objects.filter(XIRRByAssetClassService._scope_q(user))
        if family_name:
            mutual_fund_qs = mutual_fund_qs.filter(family_name=family_name)

        for transaction in mutual_fund_qs.order_by(
            "transaction_date", "created_at", "id"
        ):
            raw_class = getattr(transaction.scheme, "category", None)
            asset_class = InvestmentSummaryService._normalize_asset_class(raw_class)
            amount = transaction.amount or cls.ZERO
            fees = transaction.fees or cls.ZERO

            if transaction.transaction_type in (
                MutualFundTransactionType.PURCHASE,
                MutualFundTransactionType.SIP,
            ):
                cash_flows_by_class[asset_class].append(
                    (transaction.transaction_date, -(amount + fees))
                )
            elif transaction.transaction_type in (
                MutualFundTransactionType.REDEMPTION,
                MutualFundTransactionType.DIVIDEND,
            ):
                cash_flows_by_class[asset_class].append(
                    (transaction.transaction_date, amount - fees)
                )

        # Investment Summary is the authoritative source for today's
        # current value by the same canonical Asset Class used by the UI.
        summary = InvestmentSummaryService.calculate(user, family_name=family_name)
        current_values = {
            row["asset_class"]: Decimal(str(row["current_value"] or 0))
            for row in summary["results"]
        }

        category_by_asset_class = {
            asset_class: category
            for category, asset_classes in InvestmentSummaryService.MASTER_MAPPING
            for asset_class in asset_classes
        }

        results = []
        for asset_class, current_value in current_values.items():
            if current_value > 0:
                cash_flows_by_class[asset_class].append((date.today(), current_value))

            xirr = XIRRCalculator.calculate(cash_flows_by_class[asset_class])
            if xirr is None:
                continue

            results.append({
                "asset_category": category_by_asset_class[asset_class],
                "asset_class": asset_class,
                "xirr": round(xirr * 100, 2),
                "current_value": current_value,
            })

        return results
