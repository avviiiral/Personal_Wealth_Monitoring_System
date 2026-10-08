import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import OuterRef, QuerySet, Subquery, Q

from investments.models import Asset, SecurityMaster, Transaction, TransactionType
from investments.services.security_master import SecurityMasterService
from analytics.services.cash_flows import build_cash_flows, xirr_percent
from market_data.models import DataSource, ManualAssetPrice, MarketPrice
from market_data.services.mutual_fund_nav_service import MutualFundNAVService
from market_data.services.yahoo_finance import YahooFinanceService
from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.services.amfi_asset_resolver import AMFIAssetResolver


logger = logging.getLogger(__name__)


class PortfolioTreeService:
    ZERO = Decimal("0")

    @staticmethod
    def _clean(value, default=""):
        if value is None:
            return default
        return str(value).strip() or default

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
            .select_related("owner", "asset", "asset__security_master").only(
                "owner_id", "family_id", "family_name", "portfolio",
                "asset_class", "sub_class", "asset_name", "underlying",
                "advisors", "transaction_date", "transaction_type",
                "quantity", "price_per_unit", "amount", "fees", "notes", "id",
                "asset__owner_id", "asset__family_id", "asset__name",
                "asset__category", "asset__isin", "asset__symbol",
                "asset__security_master__id",
                "asset__security_master__owner_id",
                "asset__security_master__family_id",
                "asset__security_master__isin",
                "asset__security_master__asset_name",
                "asset__security_master__sector",
                "asset__security_master__cap_type",
                "asset__security_master__amc_name",
                "asset__security_master__pe_ratio",
                "asset__security_master__pb_ratio",
                "asset__security_master__peg_ratio",
                "asset__security_master__roe",
                "asset__security_master__credit_rating",
                "asset__security_master__ytm",
                "asset__security_master__modified_duration",
                "asset__security_master__average_maturity",
            )
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
        cash_flows = build_cash_flows(transactions)
        if current_quantity > 0 and current_value is not None and current_value > 0:
            cash_flows.append((date.today(), float(current_value)))
        return xirr_percent(cash_flows)

    REIT_INVIT_REFERENCE_SYMBOLS = {
        "Mindspace Business Parks": ("MINDSPACE.NS", "MINDSPACE.BO"),
        "Embassy Office Parks": ("EMBASSY.NS", "EMBASSY.BO"),
        "Brookfield India Real Estate Trust": ("BIRET.NS", "BIRET.BO"),
        "National Highways Infra Trust": ("NHIT.NS", "NHIT.BO"),
        "Nexus Select Trust": ("NXST.NS", "NXST.BO"),
        "Knowledge Realty Trust": ("KRT.NS", "KRT.BO"),
        "Bagmane Prime Office Reit": ("BAGMANE.NS", "BAGMANE.BO"),
        "NDR InvIT": ("NDRI.NS", "NDRINVIT.NS", "NDRINVIT.BO"),
        "Cube InvIT": ("CUBEINVIT.NS", "CUBEINVIT.BO"),
    }

    REIT_INVIT_REFERENCE_ALIASES = {
        "Mindspace Business Parks": ("mindspace business parks", "mindspace"),
        "Embassy Office Parks": ("embassy office parks", "embassy"),
        "Brookfield India Real Estate Trust": ("brookfield india real estate trust", "brookfield india reit"),
        "National Highways Infra Trust": ("national highways infra trust", "nhit"),
        "Nexus Select Trust": ("nexus select trust", "nexus"),
        "Knowledge Realty Trust": ("knowledge realty trust", "krt"),
        "Bagmane Prime Office Reit": ("bagmane prime office reit", "bagmane"),
        "NDR InvIT": ("ndr invit", "ndrinvit", "ndr"),
        "Cube InvIT": ("cube invit", "cube highways invit", "cube highways trust", "cube highways"),
    }

    @classmethod
    def _load_reit_invit_reference_prices(cls, assets_by_id, price_cache):
        """Reuse the shared MIS reference-price history for REIT/InvIT holdings.

        MISReportService.refresh_reference_prices already maintains the global
        reference assets and their Yahoo history. Portfolio valuation must not
        make a second provider request or assume the first symbol is valid;
        some REIT/InvITs have no Yahoo history under their NSE symbol while a
        fallback BSE/alternate symbol does.
        """
        today = date.today()

        for asset_id, asset in assets_by_id.items():
            if asset_id in price_cache:
                continue

            subclass = cls._clean(getattr(asset, "_portfolio_sub_class", "")).upper()
            if subclass not in {"REITS", "REIT", "INVITS", "INVIT"}:
                continue

            normalized_name = cls._clean(asset.name).lower()
            normalized_symbol = cls._clean(asset.symbol).lower()
            matched_name = None
            for reference_name, aliases in cls.REIT_INVIT_REFERENCE_ALIASES.items():
                if any(
                    alias in normalized_name or alias == normalized_symbol
                    for alias in aliases
                ):
                    matched_name = reference_name
                    break

            if matched_name is None:
                continue

            # Prefer whichever already-populated shared MIS reference asset has
            # a latest price. Do not stop at an existing but empty first symbol.
            for symbol in cls.REIT_INVIT_REFERENCE_SYMBOLS[matched_name]:
                reference_asset = (
                    Asset.objects
                    .filter(family__isnull=True, symbol=symbol)
                    .order_by("id")
                    .first()
                )
                if reference_asset is None:
                    continue

                latest = (
                    MarketPrice.objects
                    .filter(asset=reference_asset, date__lte=today)
                    .order_by("-date", "-id")
                    .values("close_price", "date", "source")
                    .first()
                )
                if latest is None:
                    continue

                price_cache[asset_id] = {
                    "current_price": latest["close_price"],
                    "price_source": latest["source"] or DataSource.YAHOO_FINANCE,
                    "price_date": latest["date"],
                }
                break

        return price_cache

    @classmethod
    def _load_price_cache(cls, asset_ids, assets_by_id=None):
        """
        Load the latest price for each portfolio asset.

        Mutual funds are valued from the canonical global AMFI master,
        rather than family-owned MutualFundNAV/Yahoo rows. This is
        intentionally done here because the Portfolio Tree is built from
        investments.Transaction and legacy MF holdings may not have current
        dedicated mutual_fund NAV rows.

        The database-backed MutualFundNAV value is preferred. For legacy
        portfolio assets that do not yet have a MutualFundScheme row, the
        existing AMFI NAV service is used as a fallback by ISIN. Its feed
        is cached in-process for the day, so this does not create one
        provider request per mutual fund.
        """
        if not asset_ids:
            return {}

        assets_by_id = assets_by_id or {}
        price_cache = {}

        # Manual prices are explicit overrides. The latest manual
        # MarketPrice wins over automatic market data regardless of
        # which one has the newer date.
        latest_manual_id = (
            MarketPrice.objects
            .filter(
                asset_id=OuterRef("asset_id"),
                source=DataSource.MANUAL,
            )
            .order_by("-date", "-id")
            .values("id")[:1]
        )
        manual_market_prices = MarketPrice.objects.filter(
            asset_id__in=asset_ids,
            source=DataSource.MANUAL,
            id=Subquery(latest_manual_id),
        )
        for manual in manual_market_prices:
            price_cache[manual.asset_id] = {
                "current_price": manual.close_price,
                "price_source": DataSource.MANUAL,
                "price_date": manual.date,
            }

        # Legacy one-row manual prices are kept as a fallback for
        # installations that predate the MarketPrice manual pipeline.
        legacy_manual_prices = ManualAssetPrice.objects.filter(
            asset_id__in=asset_ids,
        )
        for manual in legacy_manual_prices:
            if manual.asset_id in price_cache:
                continue
            price_cache[manual.asset_id] = {
                "current_price": manual.price,
                "price_source": DataSource.MANUAL,
                "price_date": manual.price_date,
            }

        # AMFI NAVs are valid for mutual-fund valuation only. They must
        # never be treated as a generic market quote for ETFs or other
        # exchange-traded assets. Mutual funds are routed to the canonical
        # AMFIMasterNAV path below.
        latest_market_id = (
            MarketPrice.objects
            .filter(
                asset_id=OuterRef("asset_id"),
            )
            .exclude(source__in=[DataSource.MANUAL, DataSource.AMFI])
            .order_by("-date", "-id")
            .values("id")[:1]
        )
        market_prices = MarketPrice.objects.filter(
            asset_id__in=asset_ids,
            id=Subquery(latest_market_id),
        )
        for market in market_prices:
            if market.asset_id in price_cache:
                continue
            price_cache[market.asset_id] = {
                "current_price": market.close_price,
                "price_source": market.source,
                "price_date": market.date,
            }

        # AMFI identity is authoritative for NAV routing. Legacy imports can
        # incorrectly classify mutual funds as STOCK/CASH/BOND/OTHER, so do
        # not rely on Asset.category or presentation metadata here.
        amfi_asset_ids = AMFIAssetResolver.amfi_asset_ids(assets_by_id)
        # AMFI is the canonical NAV source for actual mutual-fund holdings.
        # Legacy generic Asset rows can have inaccurate Asset.category values,
        # so a holding is considered a mutual fund when its Portfolio hierarchy
        # identifies it as a mutual fund. ETFs must remain on market-price
        # routing even when their ISIN also exists in the AMFI master.
        mutual_fund_assets = {
            asset_id: asset
            for asset_id, asset in assets_by_id.items()
            if asset_id in amfi_asset_ids
            and (
                str(getattr(asset, "category", "") or "").strip().upper() == "MUTUAL_FUND"
                or "MUTUAL FUND" in cls._clean(getattr(asset, "_portfolio_sub_class", "")).upper()
            )
        }

        if not mutual_fund_assets:
            return cls._load_reit_invit_reference_prices(assets_by_id, price_cache)

        # Resolve the latest persisted global AMFI NAV strictly by ISIN.
        # AMFIMasterNAV is the canonical source for Portfolio valuation; the
        # family-owned MutualFundNAV table can be stale for legacy generic
        # investments and must not override the investment-driven master.
        isins = {
            cls._clean(asset.isin).upper()
            for asset in mutual_fund_assets.values()
            if cls._clean(asset.isin)
        }
        master_scheme_by_isin = {}
        if isins:
            master_schemes = (
                AMFIMasterScheme.objects
                .filter(is_active=True)
                .filter(Q(isin_growth__in=isins) | Q(isin_dividend__in=isins))
                .only("id", "scheme_code", "scheme_name", "isin_growth", "isin_dividend")
            )
            for scheme in master_schemes:
                for scheme_isin in (scheme.isin_growth, scheme.isin_dividend):
                    normalized = cls._clean(scheme_isin).upper()
                    if normalized and normalized in isins:
                        master_scheme_by_isin.setdefault(normalized, scheme)

        if master_scheme_by_isin:
            master_scheme_ids = {scheme.id for scheme in master_scheme_by_isin.values()}
            latest_master_nav_id = (
                AMFIMasterNAV.objects
                .filter(scheme_id=OuterRef("scheme_id"))
                .order_by("-date", "-id")
                .values("id")[:1]
            )
            master_nav_rows = (
                AMFIMasterNAV.objects
                .filter(scheme_id__in=master_scheme_ids, id=Subquery(latest_master_nav_id))
                .only("scheme_id", "date", "nav", "source")
            )
            master_nav_by_scheme = {row.scheme_id: row for row in master_nav_rows}

            for asset_id, asset in mutual_fund_assets.items():
                # Manual valuation always wins over automatic sources.
                if price_cache.get(asset_id, {}).get("price_source") == DataSource.MANUAL:
                    continue
                isin = cls._clean(asset.isin).upper()
                scheme = master_scheme_by_isin.get(isin)
                nav_row = master_nav_by_scheme.get(scheme.id) if scheme else None
                if nav_row is not None:
                    price_cache[asset_id] = {
                        "current_price": nav_row.nav,
                        "price_source": DataSource.AMFI,
                        "price_date": nav_row.date,
                    }

        # If the investment-driven master has not been populated yet, use the
        # existing AMFI feed as a temporary compatibility fallback. Once the
        # scheduler/command has populated AMFIMasterNAV, Portfolio always uses
        # that persisted canonical value instead of family-level NAV records.
        for asset_id, asset in mutual_fund_assets.items():
            if price_cache.get(asset_id, {}).get("price_source") == DataSource.MANUAL:
                continue
            if asset_id in price_cache:
                continue

            isin = cls._clean(asset.isin).upper()
            if not isin:
                continue

            try:
                nav_record = MutualFundNAVService.get_latest_nav(isin)
            except Exception:
                logger.exception(
                    "Unable to resolve AMFI NAV for portfolio mutual fund asset %s (%s).",
                    asset_id,
                    asset.name,
                )
                continue

            if nav_record is not None:
                price_cache[asset_id] = {
                    "current_price": nav_record["nav"],
                    "price_source": DataSource.AMFI,
                    "price_date": nav_record["date"],
                }

        price_cache = cls._load_reit_invit_reference_prices(assets_by_id, price_cache)

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
        priced_value = (
            quantity * Decimal(str(current_price))
            if current_price is not None
            else None
        )

        # No reliable price: carry the position at cost (value = invested
        # amount, P/L = 0) until a price is entered manually. XIRR still sees
        # ``priced_value`` (None) so no fake terminal cash flow is invented.
        current_value = priced_value if priced_value is not None else invested_value
        pnl = current_value - invested_value
        pnl_percentage = (
            (pnl / invested_value) * Decimal("100")
            if invested_value > Decimal("0")
            else Decimal("0")
        )
        xirr = cls._calculate_xirr(
            xirr_transactions,
            quantity,
            priced_value,
        )
        security_master = getattr(asset, "security_master", None)
        if security_master is None and security_master_cache is not None:
            isin = asset.isin.strip() if asset.isin else ""
            if asset.family_id is not None:
                security_key = ("family_isin", asset.family_id, isin) if isin else ("family_name", asset.family_id, asset.name)
            else:
                security_key = ("owner_isin", asset.owner_id, isin) if isin else ("owner_name", asset.owner_id, asset.name)
            security_master = security_master_cache.get(security_key)
        if security_master is None:
            # Pass primary keys: ORM filters accept them and it avoids two lazy
            # owner/family row loads per asset.
            security_master = SecurityMasterService.get_for_asset(owner=asset.owner_id, asset=asset, family=asset.family_id)
        asset_name = cls._clean(first.asset_name)
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
            asset_name = cls._clean(tx.asset_name)
            group_key = (family, portfolio, asset_class, sub_class, tx.asset_id)
            grouped.setdefault(group_key, []).append(tx)

        filtered_grouped = {}
        for tx in xirr_transactions:
            family = cls._clean(tx.family_name)
            portfolio = cls._clean(tx.portfolio)
            asset_class = cls._clean(tx.asset_class)
            sub_class = cls._clean(tx.sub_class)
            asset_name = cls._clean(tx.asset_name)
            group_key = (family, portfolio, asset_class, sub_class, tx.asset_id)
            filtered_grouped.setdefault(group_key, []).append(tx)

            # Underlying XIRR follows the exact Portfolio holding hierarchy.
            xirr_grouped.setdefault((family, portfolio, asset_class, sub_class, tx.asset_id), []).append(tx)
            # Asset Name XIRR intentionally ignores family/portfolio/asset-class boundaries.
            asset_name_xirr_grouped.setdefault((sub_class, asset_name), []).append(tx)
            sub_class_xirr_grouped.setdefault(sub_class, []).append(tx)

        asset_ids = {tx.asset_id for tx in transactions}
        assets_by_id = {}
        for tx in transactions:
            asset = tx.asset
            # Preserve the hierarchy labels on the asset object so the price
            # loader can recognize legacy MF assets even when their technical
            # AssetCategory was not classified as MUTUAL_FUND.
            asset._portfolio_asset_class = tx.asset_class
            asset._portfolio_sub_class = tx.sub_class
            assets_by_id.setdefault(tx.asset_id, asset)

        price_cache = cls._load_price_cache(
            asset_ids,
            assets_by_id=assets_by_id,
        )

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
            asset_name = cls._clean(first.asset_name)
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
            asset_name = cls._clean(first.asset_name)
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