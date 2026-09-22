import logging
from datetime import date
from decimal import Decimal

from django.db.models import OuterRef, QuerySet, Subquery, Q

from investments.models import SecurityMaster, Transaction, TransactionType
from investments.services.security_master import SecurityMasterService
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
        return 0.0 if value is None else float(value)

    @staticmethod
    def _optional_float(value):
        return None if value is None else float(value)

    @classmethod
    def _get_transactions(cls, owner_ids, family_id=None) -> QuerySet:
        if family_id is not None:
            scope = Q(family_id=family_id)
        else:
            # Backward-compatible service mode: when no explicit family is
            # supplied, include the caller's own legacy ungrouped transactions
            # as well as family-linked transactions owned by the requested users.
            scope = Q(owner_id__in=owner_ids)

        return (
            Transaction.objects
            .filter(scope)
            .select_related("owner", "asset", "asset__security_master")
            .order_by(
                "family_name", "portfolio", "asset_class", "sub_class",
                "asset_name", "transaction_date", "id",
            )
        )

    @classmethod
    def _matches_xirr_filters(cls, tx, filters):
        if filters.get("family") and cls._clean(tx.family_name) != filters["family"]:
            return False
        if filters.get("asset_class") and cls._clean(tx.asset_class) != filters["asset_class"]:
            return False
        if filters.get("advisor") and cls._clean(tx.advisors, "") != filters["advisor"]:
            return False
        return True

    @staticmethod
    def _calculate_position(transactions):
        quantity = Decimal("0")
        invested_value = Decimal("0")
        for tx in transactions:
            tx_quantity = tx.quantity or Decimal("0")
            tx_amount = tx.amount or Decimal("0")
            transaction_type = str(tx.transaction_type).strip().upper()
            if transaction_type in ("BUY", "SIP"):
                quantity += tx_quantity
                invested_value += tx_amount
            elif transaction_type == "SELL":
                if tx_quantity <= 0 or quantity <= 0:
                    continue
                average_cost = invested_value / quantity if quantity > 0 else Decimal("0")
                sell_quantity = min(tx_quantity, quantity)
                quantity -= sell_quantity
                invested_value -= average_cost * sell_quantity
                if quantity <= 0:
                    quantity = Decimal("0")
                    invested_value = Decimal("0")
            elif transaction_type in ("BONUS", "SPLIT"):
                quantity += max(tx_quantity, Decimal("0"))
        average_cost = invested_value / quantity if quantity > 0 else Decimal("0")
        return {"quantity": quantity, "invested_value": invested_value, "average_cost": average_cost}

    @staticmethod
    def _calculate_xirr(transactions, current_quantity, current_value):
        cash_flows = []
        for tx in transactions:
            amount = tx.amount or Decimal("0")
            if tx.notes == "DIVIDEND REINVESTMENT":
                continue
            if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
                cash_flows.append((tx.transaction_date, -float(amount)))
            elif tx.transaction_type == TransactionType.SELL:
                cash_flows.append((tx.transaction_date, float(amount)))
        if current_quantity > 0 and current_value is not None and current_value > 0:
            cash_flows.append((date.today(), float(current_value)))
        return XIRRCalculator.calculate(cash_flows) if len(cash_flows) >= 2 else None

    @classmethod
    def _load_price_cache(cls, asset_ids):
        if not asset_ids:
            return {}
        price_cache = {}
        manual_prices = ManualAssetPrice.objects.filter(asset_id__in=asset_ids)
        for manual in manual_prices:
            price_cache[manual.asset_id] = {
                "current_price": manual.price,
                "price_source": "MANUAL",
                "price_date": manual.price_date,
            }
        latest_market_id = (
            MarketPrice.objects.filter(asset_id=OuterRef("asset_id"))
            .order_by("-date", "-id").values("id")[:1]
        )
        market_prices = MarketPrice.objects.filter(
            asset_id__in=asset_ids, id=Subquery(latest_market_id)
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
    def _build_asset(cls, transactions, xirr_transactions, asset_name_xirr, sub_class_xirr, price_cache, security_master_cache=None):
        first = transactions[0]
        asset = first.asset
        position = cls._calculate_position(transactions)
        quantity = position["quantity"]
        invested_value = position["invested_value"]
        average_cost = position["average_cost"]
        price_data = price_cache.get(asset.id, {})
        current_price = price_data.get("current_price")
        current_value = quantity * Decimal(str(current_price)) if current_price is not None else None
        pnl = current_value - invested_value if current_value is not None else None
        pnl_percentage = (pnl / invested_value) * Decimal("100") if pnl is not None and invested_value > Decimal("0") else None
        xirr = cls._calculate_xirr(xirr_transactions, quantity, current_value)
        security_master = getattr(asset, "security_master", None)
        if security_master is None and security_master_cache is not None:
            isin = asset.isin.strip() if asset.isin else ""
            if asset.family_id is not None:
                security_key = ("family_isin", asset.family_id, isin) if isin else ("family_name", asset.family_id, asset.name)
            else:
                security_key = ("owner_isin", asset.owner_id, isin) if isin else ("owner_name", asset.owner_id, asset.name)
            security_master = security_master_cache.get(security_key)
        if security_master is None:
            security_master = SecurityMasterService.get_for_asset(owner=asset.owner, asset=asset, family=asset.family)
        asset_name = cls._clean(first.asset_name, getattr(asset, "name", "Unassigned"))
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
            "price_date": str(price_data["price_date"]) if price_data.get("price_date") else None,
            "xirr": xirr,
            "asset_name_xirr": asset_name_xirr,
            "sub_class_xirr": sub_class_xirr,
            "sector": getattr(security_master, "sector", None) if security_master else None,
            "cap_type": getattr(security_master, "cap_type", None) if security_master else None,
            "amc_name": getattr(security_master, "amc_name", None) if security_master else None,
            "pe_ratio": cls._optional_float(getattr(security_master, "pe_ratio", None)) if security_master else None,
            "pb_ratio": cls._optional_float(getattr(security_master, "pb_ratio", None)) if security_master else None,
            "peg_ratio": cls._optional_float(getattr(security_master, "peg_ratio", None)) if security_master else None,
            "roe": cls._optional_float(getattr(security_master, "roe", None)) if security_master else None,
            "credit_rating": getattr(security_master, "credit_rating", None) if security_master else None,
            "ytm": cls._optional_float(getattr(security_master, "ytm", None)) if security_master else None,
            "modified_duration": cls._optional_float(getattr(security_master, "modified_duration", None)) if security_master else None,
            "average_maturity": cls._optional_float(getattr(security_master, "average_maturity", None)) if security_master else None,
        }

    @classmethod
    def build(cls, owner, xirr_filters=None, family_id=None):
        owner_ids = [owner.pk] if hasattr(owner, "pk") else list(owner)
        transactions = list(cls._get_transactions(owner_ids, family_id=family_id))
        filters = {key: str(value).strip() for key, value in (xirr_filters or {}).items() if value}
        xirr_transactions = [tx for tx in transactions if cls._matches_xirr_filters(tx, filters)]

        tree = {}
        grouped = {}
        xirr_grouped = {}
        asset_name_xirr_grouped = {}
        sub_class_xirr_grouped = {}

        for tx in transactions:
            family = cls._clean(tx.family_name)
            portfolio = cls._clean(tx.portfolio)
            asset_class = cls._clean(tx.asset_class)
            sub_class = cls._clean(tx.sub_class)
            asset_name = cls._clean(tx.asset_name, getattr(tx.asset, "name", "Unassigned"))
            group_key = (family, portfolio, asset_class, sub_class, tx.asset_id)
            grouped.setdefault(group_key, []).append(tx)

        filtered_grouped = {}
        for tx in xirr_transactions:
            family = cls._clean(tx.family_name)
            portfolio = cls._clean(tx.portfolio)
            asset_class = cls._clean(tx.asset_class)
            sub_class = cls._clean(tx.sub_class)
            asset_name = cls._clean(tx.asset_name, getattr(tx.asset, "name", "Unassigned"))
            group_key = (family, portfolio, asset_class, sub_class, tx.asset_id)
            filtered_grouped.setdefault(group_key, []).append(tx)

            # Underlying XIRR follows the exact Portfolio holding hierarchy.
            xirr_grouped.setdefault((family, portfolio, asset_class, sub_class, tx.asset_id), []).append(tx)
            # Asset Name XIRR intentionally ignores family/portfolio/asset-class boundaries.
            asset_name_xirr_grouped.setdefault((sub_class, asset_name), []).append(tx)
            sub_class_xirr_grouped.setdefault(sub_class, []).append(tx)

        asset_ids = {tx.asset_id for tx in transactions}
        price_cache = cls._load_price_cache(asset_ids)

        assets_for_security_master = {}
        for tx in transactions:
            assets_for_security_master.setdefault(tx.asset_id, tx.asset)

        family_ids = {asset.family_id for asset in assets_for_security_master.values() if asset.family_id is not None}
        owner_ids_for_legacy = {asset.owner_id for asset in assets_for_security_master.values() if asset.family_id is None}

        security_filters = Q()
        if family_ids:
            security_filters |= Q(family_id__in=family_ids)
        if owner_ids_for_legacy:
            security_filters |= Q(family__isnull=True, owner_id__in=owner_ids_for_legacy)

        security_master_cache = {}
        if security_filters:
            security_masters = SecurityMaster.objects.filter(security_filters).only(
                "id", "owner_id", "family_id", "isin", "asset_name",
                "sector", "cap_type", "amc_name", "pe_ratio", "pb_ratio",
                "peg_ratio", "roe", "credit_rating", "ytm",
                "modified_duration", "average_maturity",
            )
            for security in security_masters:
                if security.family_id is not None:
                    if security.isin:
                        security_master_cache.setdefault(("family_isin", security.family_id, security.isin), security)
                    else:
                        security_master_cache.setdefault(("family_name", security.family_id, security.asset_name), security)
                elif security.isin:
                    security_master_cache.setdefault(("owner_isin", security.owner_id, security.isin), security)
                else:
                    security_master_cache.setdefault(("owner_name", security.owner_id, security.asset_name), security)

        asset_name_terminal_values = {}
        asset_name_quantities = {}
        sub_class_terminal_values = {}
        sub_class_quantities = {}

        for (family, portfolio, asset_class, sub_class, asset_id), asset_transactions in filtered_grouped.items():
            first = asset_transactions[0]
            asset_name = cls._clean(first.asset_name, getattr(first.asset, "name", "Unassigned"))
            asset_name_key = (sub_class, asset_name)
            sub_class_key = sub_class
            position = cls._calculate_position(asset_transactions)
            quantity = position["quantity"]
            asset_name_quantities[asset_name_key] = asset_name_quantities.get(asset_name_key, Decimal("0")) + quantity
            sub_class_quantities[sub_class_key] = sub_class_quantities.get(sub_class_key, Decimal("0")) + quantity
            current_price = price_cache.get(asset_id, {}).get("current_price")
            if current_price is not None and quantity > 0:
                current_value = quantity * Decimal(str(current_price))
                asset_name_terminal_values[asset_name_key] = asset_name_terminal_values.get(asset_name_key, Decimal("0")) + current_value
                sub_class_terminal_values[sub_class_key] = sub_class_terminal_values.get(sub_class_key, Decimal("0")) + current_value

        asset_name_xirr_values = {
            key: cls._calculate_xirr(
                txs,
                asset_name_quantities.get(key, Decimal("0")),
                asset_name_terminal_values.get(key),
            )
            for key, txs in asset_name_xirr_grouped.items()
        }
        sub_class_xirr_values = {
            key: cls._calculate_xirr(
                txs,
                sub_class_quantities.get(key, Decimal("0")),
                sub_class_terminal_values.get(key),
            )
            for key, txs in sub_class_xirr_grouped.items()
        }

        for (family, portfolio, asset_class, sub_class, asset_id), asset_transactions in grouped.items():
            first = asset_transactions[0]
            xirr_key = (family, portfolio, asset_class, sub_class, asset_id)
            asset_name = cls._clean(first.asset_name, getattr(first.asset, "name", "Unassigned"))
            asset_name_key = (sub_class, asset_name)
            sub_class_key = sub_class
            asset_data = cls._build_asset(
                transactions=asset_transactions,
                xirr_transactions=xirr_grouped.get(xirr_key, []),
                asset_name_xirr=asset_name_xirr_values.get(asset_name_key),
                sub_class_xirr=sub_class_xirr_values.get(sub_class_key),
                price_cache=price_cache,
                security_master_cache=security_master_cache,
            )
            if asset_data["quantity"] <= 0:
                continue
            family_data = tree.setdefault(family, {"family_name": family, "portfolios": {}})
            portfolio_data = family_data["portfolios"].setdefault(portfolio, {"portfolio": portfolio, "asset_classes": {}})
            asset_class_data = portfolio_data["asset_classes"].setdefault(asset_class, {"asset_class": asset_class, "sub_classes": {}})
            subclass_data = asset_class_data["sub_classes"].setdefault(sub_class, {"sub_class": sub_class, "assets": []})
            subclass_data["assets"].append(asset_data)

        families = []
        for family_data in tree.values():
            portfolios = []
            for portfolio_data in family_data["portfolios"].values():
                asset_classes = []
                for asset_class_data in portfolio_data["asset_classes"].values():
                    sub_classes = []
                    for sub_class_data in asset_class_data["sub_classes"].values():
                        sub_class_data["assets"].sort(key=lambda item: (item["asset_name"] or "").lower())
                        sub_class_data["asset_count"] = len(sub_class_data["assets"])
                        sub_classes.append(sub_class_data)
                    sub_classes.sort(key=lambda item: (item["sub_class"] or "").lower())
                    asset_class_data["sub_classes"] = sub_classes
                    asset_class_data["sub_class_count"] = len(sub_classes)
                    asset_classes.append(asset_class_data)
                asset_classes.sort(key=lambda item: (item["asset_class"] or "").lower())
                portfolio_data["asset_classes"] = asset_classes
                portfolio_data["asset_class_count"] = len(asset_classes)
                portfolios.append(portfolio_data)
            portfolios.sort(key=lambda item: (item["portfolio"] or "").lower())
            family_data["portfolios"] = portfolios
            family_data["portfolio_count"] = len(portfolios)
            families.append(family_data)
        families.sort(key=lambda item: (item["family_name"] or "").lower())
        return {"count": len(families), "families": families}
