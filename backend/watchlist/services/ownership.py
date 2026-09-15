from datetime import date
from decimal import Decimal

from django.db.models import Q

from investments.models import Asset, AssetCategory, PortfolioPosition, Transaction, TransactionType
from users.permissions import get_visible_owner_ids
from watchlist.models import InvestmentProduct, ProductType
from analytics.services.xirr import XIRRCalculator


class OwnershipService:
    """Derive ownership from existing PWMS portfolio positions."""

    @staticmethod
    def _asset_queryset(product, owner_ids):
        qs = Asset.objects.filter(owner_id__in=owner_ids)
        if product.isin:
            qs = qs.filter(isin__iexact=product.isin)
            if product.product_type == ProductType.MUTUAL_FUND:
                qs = qs.filter(category=AssetCategory.MUTUAL_FUND)
            return qs
        if product.external_identifier:
            return qs.filter(Q(symbol__iexact=product.external_identifier) | Q(name__iexact=product.name))
        return qs.filter(name__iexact=product.name)

    @staticmethod
    def _position_xirr(position):
        flows = []
        transactions = Transaction.objects.filter(
            owner=position.owner,
            asset=position.asset,
            family_name=position.family_name,
            portfolio=position.portfolio,
        ).order_by("transaction_date", "created_at", "id")
        for tx in transactions:
            amount = tx.amount or Decimal("0")
            fees = tx.fees or Decimal("0")
            if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
                flows.append((tx.transaction_date, -(amount + fees)))
            elif tx.transaction_type in (TransactionType.SELL, TransactionType.DIVIDEND, TransactionType.INTEREST):
                flows.append((tx.transaction_date, amount - fees))
        if position.current_value and position.current_value > 0:
            flows.append((date.today(), position.current_value))
        if len(flows) < 2:
            return None
        value = XIRRCalculator.calculate(flows)
        return round(value * 100, 2) if value is not None else None

    @classmethod
    def ownership_rows(cls, product, user):
        owner_ids = get_visible_owner_ids(user)
        assets = cls._asset_queryset(product, owner_ids)
        if product.product_type == ProductType.MUTUAL_FUND:
            assets = assets.filter(category=AssetCategory.MUTUAL_FUND)
        positions = PortfolioPosition.objects.filter(owner_id__in=owner_ids, asset__in=assets).select_related("asset")
        rows = []
        for position in positions:
            if position.quantity <= 0 and position.current_value <= 0:
                continue
            rows.append({
                "family": position.family_name,
                "portfolio": position.portfolio,
                "current_value": position.current_value,
                "invested_value": position.invested_value,
                "quantity": position.quantity,
                "current_value_per_unit": position.current_price,
                "return_percent": (
                    ((position.current_value / position.invested_value) - 1) * 100
                    if position.invested_value else None
                ),
                "xirr": cls._position_xirr(position),
                "holding_status": "OWNED",
            })
        return rows

    @classmethod
    def enrich(cls, product, user):
        rows = cls.ownership_rows(product, user)
        return {
            "status": "OWNED" if rows else "UNIVERSAL",
            "ownership": rows,
            "owned_current_value": sum((row["current_value"] for row in rows), 0),
            "owned_invested_value": sum((row["invested_value"] for row in rows), 0),
        }
