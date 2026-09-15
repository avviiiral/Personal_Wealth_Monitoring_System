from django.db.models import Q

from investments.models import Asset, AssetCategory, PortfolioPosition
from users.permissions import get_visible_owner_ids
from watchlist.models import InvestmentProduct, ProductType


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
