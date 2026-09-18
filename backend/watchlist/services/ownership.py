from datetime import date
from decimal import Decimal

from django.db.models import Q

from analytics.services.xirr import XIRRCalculator
from investments.models import Asset, AssetCategory, PortfolioPosition, Transaction, TransactionType
from users.permissions import family_scope, require_active_family
from watchlist.models import InvestmentProduct, ProductType


class OwnershipService:
    """Derive ownership from existing PWMS portfolio positions."""

    @staticmethod
    def _product_name(product):
        """Return the canonical name that should be matched to a portfolio source."""
        if product.product_type == ProductType.PMS and getattr(product, "pms", None):
            return product.pms.strategy_name or product.name
        return product.name

    @classmethod
    def _asset_queryset(cls, product, user):
        qs = family_scope(Asset.objects, user)
        match_name = cls._product_name(product)

        # PMS holdings are stored as underlying-stock Assets. The PMS strategy
        # name is preserved on Transaction.asset_name, so use that field to
        # discover the underlying Assets dynamically instead of expecting the
        # PMS strategy itself to exist as an Asset.
        if product.product_type == ProductType.PMS:
            asset_ids = Transaction.objects.filter(
                family_id=require_active_family(user).id,
                asset_name__iexact=match_name,
            ).values_list("asset_id", flat=True).distinct()
            return qs.filter(id__in=asset_ids)

        if product.isin:
            qs = qs.filter(isin__iexact=product.isin)
            if product.product_type == ProductType.MUTUAL_FUND:
                qs = qs.filter(category=AssetCategory.MUTUAL_FUND)
            return qs
        if product.external_identifier:
            return qs.filter(Q(symbol__iexact=product.external_identifier) | Q(name__iexact=match_name))
        return qs.filter(name__iexact=match_name)

    @staticmethod
    def _position_xirr(position, transactions_by_position=None):
        flows = []
        if transactions_by_position is None:
            transactions = Transaction.objects.filter(
                owner=position.owner,
                asset=position.asset,
                family_name=position.family_name,
                portfolio=position.portfolio,
            ).order_by("transaction_date", "created_at", "id")
        else:
            transactions = transactions_by_position.get(
                (position.owner_id, position.asset_id, position.family_name, position.portfolio),
                [],
            )
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
    def _build_rows(cls, matched_positions, transactions_by_position=None):
        rows_by_product = {}
        for position, product_id in matched_positions:
            if position.quantity <= 0 and position.current_value <= 0:
                continue
            rows_by_product.setdefault(product_id, []).append({
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
                "xirr": cls._position_xirr(position, transactions_by_position),
                "holding_status": "OWNED",
            })
        return rows_by_product

    @classmethod
    def bulk_enrich(cls, products, user):
        """Enrich a page of products with a bounded number of DB queries."""
        products = list(products)
        if not products:
            return {}

        product_by_id = {product.id: product for product in products}
        product_match_names = {
            product.id: cls._product_name(product)
            for product in products
        }
        product_asset_ids = {product.id: set() for product in products}

        if all(product.product_type == ProductType.PMS for product in products):
            # PMS portfolio imports retain the strategy name on each
            # Transaction.asset_name while Transaction.asset points to the
            # underlying stock Asset. Resolve that relationship dynamically.
            name_query = Q()
            for product in products:
                name_query |= Q(asset_name__iexact=product_match_names[product.id])

            pms_transactions = family_scope(Transaction.objects, user).filter(name_query).values("asset_id", "asset_name")

            products_by_name = {}
            for product in products:
                products_by_name.setdefault(
                    str(product_match_names[product.id] or "").strip().casefold(),
                    [],
                ).append(product.id)

            for transaction in pms_transactions:
                name_key = str(transaction["asset_name"] or "").strip().casefold()
                for product_id in products_by_name.get(name_key, []):
                    product_asset_ids[product_id].add(transaction["asset_id"])
        else:
            identifier_query = Q()
            for product in products:
                match_name = product_match_names[product.id]
                if product.isin:
                    identifier_query |= Q(isin__iexact=product.isin)
                elif product.external_identifier:
                    identifier_query |= (
                        Q(symbol__iexact=product.external_identifier)
                        | Q(name__iexact=match_name)
                    )
                else:
                    identifier_query |= Q(name__iexact=match_name)

            assets_qs = family_scope(Asset.objects, user).filter(identifier_query)
            if products and all(product.product_type == ProductType.MUTUAL_FUND for product in products):
                assets_qs = assets_qs.filter(category=AssetCategory.MUTUAL_FUND)
            assets = list(assets_qs.only("id", "owner_id", "name", "symbol", "isin", "category"))

            def norm(value):
                return str(value or "").strip().casefold()

            assets_by_isin = {}
            assets_by_symbol = {}
            assets_by_name = {}
            for asset in assets:
                if asset.isin:
                    assets_by_isin.setdefault(norm(asset.isin), []).append(asset)
                if asset.symbol:
                    assets_by_symbol.setdefault(norm(asset.symbol), []).append(asset)
                if asset.name:
                    assets_by_name.setdefault(norm(asset.name), []).append(asset)

            for product in products:
                match_name = product_match_names[product.id]
                if product.isin:
                    matches = assets_by_isin.get(norm(product.isin), [])
                elif product.external_identifier:
                    matches = (
                        assets_by_symbol.get(norm(product.external_identifier), [])
                        + assets_by_name.get(norm(match_name), [])
                    )
                else:
                    matches = assets_by_name.get(norm(match_name), [])
                product_asset_ids[product.id] = {asset.id for asset in matches}

        all_asset_ids = {asset_id for ids in product_asset_ids.values() for asset_id in ids}
        if not all_asset_ids:
            return {
                product.id: {
                    "status": "UNIVERSAL",
                    "ownership": [],
                    "owned_current_value": Decimal("0"),
                    "owned_invested_value": Decimal("0"),
                }
                for product in products
            }

        positions = list(
            family_scope(PortfolioPosition.objects, user).filter(asset_id__in=all_asset_ids)
            .select_related("asset")
        )
        product_for_asset = {}
        for product_id, asset_ids in product_asset_ids.items():
            for asset_id in asset_ids:
                product_for_asset.setdefault(asset_id, []).append(product_id)

        matched_positions = [
            (position, product_id)
            for position in positions
            for product_id in product_for_asset.get(position.asset_id, [])
        ]

        position_keys = {
            (position.owner_id, position.asset_id, position.family_name, position.portfolio)
            for position, _ in matched_positions
            if position.quantity > 0 or position.current_value > 0
        }
        transactions_by_position = {key: [] for key in position_keys}
        if position_keys:
            transactions = family_scope(Transaction.objects, user).filter(
                asset_id__in=all_asset_ids,
            ).order_by("transaction_date", "created_at", "id")
            for tx in transactions:
                key = (tx.owner_id, tx.asset_id, tx.family_name, tx.portfolio)
                if key in transactions_by_position:
                    transactions_by_position[key].append(tx)

        rows_by_product = cls._build_rows(matched_positions, transactions_by_position)
        result = {}
        for product_id in product_by_id:
            rows = rows_by_product.get(product_id, [])
            result[product_id] = {
                "status": "OWNED" if rows else "UNIVERSAL",
                "ownership": rows,
                "owned_current_value": sum((row["current_value"] for row in rows), Decimal("0")),
                "owned_invested_value": sum((row["invested_value"] for row in rows), Decimal("0")),
            }
        return result

    @classmethod
    def ownership_rows(cls, product, user):
        assets = cls._asset_queryset(product, user)
        if product.product_type == ProductType.MUTUAL_FUND:
            assets = assets.filter(category=AssetCategory.MUTUAL_FUND)
        positions = family_scope(PortfolioPosition.objects, user).filter(asset__in=assets).select_related("asset")
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
