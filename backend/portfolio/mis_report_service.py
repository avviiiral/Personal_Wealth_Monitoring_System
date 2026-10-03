from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import re

import requests

from django.db.models import Max, Q

from investments.models import Asset, Transaction, TransactionType
from market_data.models import MarketPrice, ManualAssetPrice
from mutual_funds.models import MutualFundNAV, MutualFundTransaction, MutualFundHolding
from portfolio.services.portfolio_tree_service import PortfolioTreeService
from market_data.services.yahoo_finance import YahooFinanceService
from users.models import TaxRateSetting
from .models import FamilyMISNotes


class MISReportService:
    """
    Builds the MIS in the same three logical sections as the supplied workbook:
    IPS, Data Sheet and Fund Type-wise Summary.

    Historical values are reconstructed from transaction quantities/cost basis and
    the latest available market price/NAV on or before each reporting date.
    """

    ZERO = Decimal("0")

    # Standard external market references used by the MIS Notes sheet.
    REFERENCE_SYMBOLS = {
        "Mindspace Business Parks": ("MINDSPACE.NS", "MINDSPACE.BO"),
        "Embassy Office Parks": ("EMBASSY.NS", "EMBASSY.BO"),
        "Brookfield India Real Estate Trust": ("BIRET.NS", "BIRET.BO"),
        "National Highways Infra Trust": ("NHIT.NS", "NHIT.BO"),
        "Nexus Select Trust": ("NXST.NS", "NXST.BO"),
        "Knowledge Realty Trust": ("KRT.NS", "KRT.BO"),
        "Bagmane Prime Office Reit": ("BAGMANE.NS", "BAGMANE.BO"),
        "NDR InvIT": ("NDRI.NS", "NDRINVIT.NS", "NDRINVIT.BO"),
        "Cube InvIT": ("CUBEINVIT.NS", "CUBEINVIT.BO"),
        "Nifty 50": "^NSEI",
        "$ Rate": "USDINR=X",
        "BSE 500": "BSE-500.BO",
    }

    @staticmethod
    def _clean(value, default=""):
        value = str(value or "").strip()
        return value or default

    @staticmethod
    def _signed_transaction(tx):
        if tx.transaction_type == TransactionType.SELL:
            return -Decimal(str(tx.quantity or 0)), -Decimal(str(tx.amount or 0))
        if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
            return Decimal(str(tx.quantity or 0)), Decimal(str(tx.amount or 0))
        if tx.transaction_type in (TransactionType.BONUS, TransactionType.SPLIT):
            return Decimal(str(tx.quantity or 0)), Decimal("0")
        return Decimal("0"), Decimal("0")

    @classmethod
    def _position_at(cls, transactions, as_of, kind="asset"):
        quantity = cls.ZERO
        invested = cls.ZERO
        for tx in sorted(
            (tx for tx in transactions if tx.transaction_date <= as_of),
            key=lambda item: (item.transaction_date, item.created_at, item.id),
        ):
            tx_qty = Decimal(str(getattr(tx, "quantity", getattr(tx, "units", 0)) or 0))
            tx_amount = Decimal(str(tx.amount or 0))

            if kind == "mutual_fund":
                is_buy = tx.transaction_type in ("PURCHASE", "SIP")
                is_sell = tx.transaction_type == "REDEMPTION"
            else:
                is_buy = tx.transaction_type in (TransactionType.BUY, TransactionType.SIP)
                is_sell = tx.transaction_type == TransactionType.SELL

            if is_buy:
                quantity += tx_qty
                invested += tx_amount
            elif is_sell:
                sell_qty = min(tx_qty, quantity)
                avg_cost = invested / quantity if quantity > 0 else cls.ZERO
                quantity -= sell_qty
                invested -= avg_cost * sell_qty
            elif kind == "asset" and tx.transaction_type in (TransactionType.BONUS, TransactionType.SPLIT):
                quantity += tx_qty

        if quantity < 0:
            quantity = cls.ZERO
        if invested < 0:
            invested = cls.ZERO
        return quantity, invested

    @classmethod
    def _market_price(cls, asset_id, as_of, cache):
        key = (asset_id, as_of)
        if key not in cache:
            cache[key] = (
                MarketPrice.objects
                .filter(asset_id=asset_id, date__lte=as_of)
                .order_by("-date", "-id")
                .values_list("close_price", flat=True)
                .first()
            )
        return cache[key]

    @classmethod
    def _mf_nav(cls, scheme_id, as_of, cache):
        key = (scheme_id, as_of)
        if key not in cache:
            cache[key] = (
                MutualFundNAV.objects
                .filter(scheme_id=scheme_id, date__lte=as_of)
                .order_by("-date", "-id")
                .values_list("nav", flat=True)
                .first()
            )
        return cache[key]

    @classmethod
    def _latest_reporting_date(cls, family):
        tx_date = (
            Transaction.objects.filter(family=family, transaction_date__lte=date.today())
            .aggregate(value=Max("transaction_date"))["value"]
        )
        mf_tx_date = (
            MutualFundTransaction.objects.filter(family=family, transaction_date__lte=date.today())
            .aggregate(value=Max("transaction_date"))["value"]
        )
        price_date = (
            MarketPrice.objects.filter(asset__family=family, date__lte=date.today())
            .aggregate(value=Max("date"))["value"]
        )
        nav_date = (
            MutualFundNAV.objects.filter(scheme__family=family, date__lte=date.today())
            .aggregate(value=Max("date"))["value"]
        )
        candidates = [value for value in (tx_date, mf_tx_date, price_date, nav_date) if value]
        return max(candidates) if candidates else date.today()

    @staticmethod
    def _month_end(year, month):
        if month == 12:
            return date(year, 12, 31)
        return date(year, month + 1, 1) - timedelta(days=1)

    @classmethod
    def _prior_month_end(cls, as_of):
        first = date(as_of.year, as_of.month, 1)
        return first - timedelta(days=1)

    @classmethod
    def _period_start(cls, as_of):
        # The supplied workbook starts its reporting year with the March closing
        # balance and records transactions from April through the reporting month.
        return date(as_of.year, 4, 1)

    @classmethod
    def _opening_date(cls, as_of):
        return cls._month_end(as_of.year, 3)

    @classmethod
    def _base_rows(cls, family):
        rows = []

        transactions = list(
            Transaction.objects
            .filter(family=family)
            .select_related("asset")
            .order_by("transaction_date", "created_at", "id")
        )
        tx_groups = defaultdict(list)
        for tx in transactions:
            family_name = cls._clean(tx.family_name)
            asset_name = cls._clean(tx.asset_name)
            sub_class = cls._clean(tx.sub_class)
            key = (family_name, sub_class, asset_name)
            tx_groups[key].append(tx)

        for key, txs in tx_groups.items():
            first = txs[0]
            advisors = sorted(
                {str(tx.advisors).strip() for tx in txs if str(tx.advisors or "").strip()},
                key=str.casefold,
            )
            rows.append({
                "kind": "asset",
                "key": key,
                "family_name": key[0],
                "asset_name": key[2],
                "asset_class": cls._clean(first.asset_class),
                "sub_class": key[1],
                "advisor": ", ".join(advisors) or "",
                "asset_ids": sorted({tx.asset_id for tx in txs}),
                "transactions": txs,
            })

        mf_transactions = list(
            MutualFundTransaction.objects
            .filter(family=family)
            .select_related("scheme")
            .order_by("transaction_date", "created_at", "id")
        )
        mf_groups = defaultdict(list)
        for tx in mf_transactions:
            family_name = cls._clean(tx.family_name)
            asset_name = cls._clean(tx.scheme.scheme_name)
            sub_class = cls._clean(tx.scheme.category)
            mf_groups[(family_name, sub_class, asset_name)].append(tx)

        for key, txs in mf_groups.items():
            first = txs[0]
            rows.append({
                "kind": "mutual_fund",
                "key": key,
                "family_name": key[0],
                "asset_name": key[2],
                "asset_class": "Mutual Funds",
                "sub_class": key[1],
                "advisor": "",
                "scheme_ids": sorted({tx.scheme_id for tx in txs}),
                "transactions": txs,
            })

        return rows

    @classmethod
    def _build_data_row(cls, row, opening_date, as_of, period_start, price_cache, nav_cache):
        txs = row["transactions"]
        opening_qty, _opening_cost = cls._position_at(txs, opening_date, row["kind"])
        closing_qty, closing_cost = cls._position_at(txs, as_of, row["kind"])

        if row["kind"] == "asset":
            asset_ids = row["asset_ids"]
            opening_prices = [
                cls._market_price(asset_id, opening_date, price_cache)
                for asset_id in asset_ids
            ]
            closing_prices = [
                cls._market_price(asset_id, as_of, price_cache)
                for asset_id in asset_ids
            ]
        else:
            opening_prices = [
                cls._mf_nav(scheme_id, opening_date, nav_cache)
                for scheme_id in row["scheme_ids"]
            ]
            closing_prices = [
                cls._mf_nav(scheme_id, as_of, nav_cache)
                for scheme_id in row["scheme_ids"]
            ]

        opening_prices = [Decimal(str(value)) for value in opening_prices if value is not None]
        closing_prices = [Decimal(str(value)) for value in closing_prices if value is not None]

        opening_rate = opening_prices[0] if len(set(opening_prices)) == 1 and opening_prices else (
            sum(opening_prices) / len(opening_prices) if opening_prices else None
        )
        closing_rate = closing_prices[0] if len(set(closing_prices)) == 1 and closing_prices else (
            sum(closing_prices) / len(closing_prices) if closing_prices else None
        )

        opening_mtm = opening_qty * opening_rate if opening_rate is not None else cls.ZERO
        closing_mtm = closing_qty * closing_rate if closing_rate is not None else closing_cost
        average_cost = closing_cost / closing_qty if closing_qty else cls.ZERO

        period_transactions = [tx for tx in txs if period_start <= tx.transaction_date <= as_of]
        transaction_units = cls.ZERO
        transaction_amount = cls.ZERO
        transaction_units_abs = cls.ZERO
        transaction_amount_abs = cls.ZERO

        for tx in period_transactions:
            if row["kind"] == "asset":
                qty, amount = cls._signed_transaction(tx)
            else:
                if tx.transaction_type == "REDEMPTION":
                    qty = -Decimal(str(tx.units or 0))
                    amount = -Decimal(str(tx.amount or 0))
                elif tx.transaction_type in ("PURCHASE", "SIP"):
                    qty = Decimal(str(tx.units or 0))
                    amount = Decimal(str(tx.amount or 0))
                else:
                    qty = Decimal("0")
                    amount = Decimal("0")
            transaction_units += qty
            transaction_amount += amount
            transaction_units_abs += abs(qty)
            transaction_amount_abs += abs(amount)

        transaction_rate = (
            transaction_amount_abs / transaction_units_abs
            if transaction_units_abs else None
        )

        return {
            "asset_name": row["asset_name"],
            "family_name": row["family_name"],
            "asset_class": row["asset_class"],
            "sub_class": row["sub_class"],
            "advisor": row["advisor"],
            "qty_units": closing_qty,
            "rate": average_cost,
            "total_cost": closing_cost,
            "opening_units": opening_qty,
            "opening_nav": opening_rate,
            "opening_amount": opening_mtm,
            "transaction_units": transaction_units,
            "transaction_nav": transaction_rate,
            "transaction_amount": transaction_amount,
            "closing_units": closing_qty,
            "closing_nav": closing_rate,
            "closing_amount": closing_mtm,
        }

    @staticmethod
    def _holding_months(acquired_on, disposed_on):
        months = (disposed_on.year - acquired_on.year) * 12 + (disposed_on.month - acquired_on.month)
        if disposed_on.day < acquired_on.day:
            months -= 1
        return max(months, 0)

    @classmethod
    def _tax_rate_for_lot(cls, lot, tax_setting, as_of):
        if tax_setting is None or tax_setting.tenure_months is None:
            return cls.ZERO
        holding_months = cls._holding_months(lot["acquired_on"], as_of)
        rate = (
            tax_setting.short_term_tax_rate
            if holding_months <= tax_setting.tenure_months
            else tax_setting.long_term_tax_rate
        )
        return Decimal(str(rate or 0)) / Decimal("100")

    @classmethod
    def _fifo_tax_metrics(cls, transactions, kind, as_of, period_start, tax_setting, market_rate):
        lots = []
        realized_pnl = cls.ZERO
        realized_tax = cls.ZERO

        ordered = sorted(
            (tx for tx in transactions if tx.transaction_date <= as_of),
            key=lambda item: (item.transaction_date, item.created_at, item.id),
        )

        for tx in ordered:
            qty = Decimal(str(getattr(tx, "quantity", getattr(tx, "units", 0)) or 0))
            amount = Decimal(str(tx.amount or 0))

            if kind == "mutual_fund":
                is_buy = tx.transaction_type in ("PURCHASE", "SIP")
                is_sell = tx.transaction_type == "REDEMPTION"
            else:
                is_buy = tx.transaction_type in (TransactionType.BUY, TransactionType.SIP)
                is_sell = tx.transaction_type == TransactionType.SELL

            if is_buy and qty > 0:
                lots.append({
                    "remaining_qty": qty,
                    "unit_cost": amount / qty if amount else cls.ZERO,
                    "acquired_on": tx.transaction_date,
                })
                continue

            if kind == "asset" and tx.transaction_type in (TransactionType.BONUS, TransactionType.SPLIT) and qty > 0:
                lots.append({
                    "remaining_qty": qty,
                    "unit_cost": cls.ZERO,
                    "acquired_on": tx.transaction_date,
                })
                continue

            if not is_sell or qty <= 0:
                continue

            sale_unit_value = amount / qty if amount else Decimal(
                str(getattr(tx, "price_per_unit", getattr(tx, "nav", 0)) or 0)
            )
            remaining_to_sell = qty
            while remaining_to_sell > 0 and lots:
                lot = lots[0]
                matched_qty = min(remaining_to_sell, lot["remaining_qty"])
                gain = (sale_unit_value - lot["unit_cost"]) * matched_qty

                if period_start <= tx.transaction_date <= as_of:
                    realized_pnl += gain
                    if gain > 0:
                        realized_tax += gain * cls._tax_rate_for_lot(
                            lot, tax_setting, tx.transaction_date
                        )

                lot["remaining_qty"] -= matched_qty
                remaining_to_sell -= matched_qty
                if lot["remaining_qty"] <= 0:
                    lots.pop(0)

        unrealized_pnl = cls.ZERO
        unrealized_tax = cls.ZERO
        if market_rate is not None:
            market_rate = Decimal(str(market_rate))
            for lot in lots:
                gain = (market_rate - lot["unit_cost"]) * lot["remaining_qty"]
                unrealized_pnl += gain
                if gain > 0:
                    unrealized_tax += gain * cls._tax_rate_for_lot(
                        lot, tax_setting, as_of
                    )

        return {
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "realized_tax": realized_tax,
            "unrealized_tax": unrealized_tax,
        }

    @classmethod
    def _tax_settings(cls, family, rows):
        asset_ids = {
            asset_id
            for row in rows
            for asset_id in row.get("asset_ids", [])
        }
        asset_names = {row["asset_name"] for row in rows}

        filters = Q(asset_id__in=asset_ids) | Q(asset__name__in=asset_names)
        settings = TaxRateSetting.objects.filter(
            family=family
        ).filter(filters).select_related("asset").order_by("-updated_at", "-id")

        by_asset_id = {}
        by_asset_name = {}

        # Tax settings are driven by the report/display Asset Name, not by
        # the internal Asset row. Multiple internal Asset records can represent
        # the same displayed asset name (for example, separate Direct Equity
        # positions). A setting saved against any one of those records must
        # therefore apply to every matching report row.
        display_names_by_asset_id = defaultdict(set)
        for row in rows:
            for asset_id in row.get("asset_ids", []):
                display_names_by_asset_id[asset_id].add(row["asset_name"])

        for setting in settings:
            by_asset_id.setdefault(setting.asset_id, setting)
            by_asset_name.setdefault(setting.asset.name, setting)
            for display_name in display_names_by_asset_id.get(setting.asset_id, set()):
                by_asset_name.setdefault(display_name, setting)

        return by_asset_id, by_asset_name

    @classmethod
    def _build_tax_row(cls, row, data_row, as_of, period_start, tax_setting):
        metrics = cls._fifo_tax_metrics(
            row["transactions"],
            row["kind"],
            as_of,
            period_start,
            tax_setting,
            data_row["closing_nav"],
        )
        return {
            **data_row,
            "realized_pnl": metrics["realized_pnl"],
            "unrealized_pnl": metrics["unrealized_pnl"],
            "realized_tax": metrics["realized_tax"],
            "unrealized_tax": metrics["unrealized_tax"],
        }

    @classmethod
    def refresh_reference_prices(cls, lookback_days=7):
        """Refresh the shared Yahoo history used by MIS Notes."""
        today = date.today()
        refreshed = 0
        failed = 0
        records = 0

        for name, symbols in cls.REFERENCE_SYMBOLS.items():
            candidates = symbols if isinstance(symbols, (tuple, list)) else (symbols,)
            success = False
            for symbol in candidates:
                try:
                    asset = (
                        Asset.objects
                        .filter(family__isnull=True, symbol=symbol)
                        .order_by("id")
                        .first()
                    )
                    if asset is None:
                        asset = Asset.objects.create(
                            owner=None,
                            family=None,
                            name=name,
                            category="OTHER",
                            symbol=symbol,
                            currency="INR",
                            is_active=True,
                        )

                    latest_date = (
                        MarketPrice.objects
                        .filter(asset=asset)
                        .order_by("-date", "-id")
                        .values_list("date", flat=True)
                        .first()
                    )
                    start = max(today - timedelta(days=lookback_days), latest_date) if latest_date else today - timedelta(days=lookback_days)
                    saved = YahooFinanceService.save_history(
                        asset=asset,
                        symbol=symbol,
                        start=start,
                        end=today + timedelta(days=1),
                    )
                    records += saved
                    refreshed += 1
                    success = True
                    break
                except Exception:
                    continue

            if not success:
                failed += 1

        return {
            "references": len(cls.REFERENCE_SYMBOLS),
            "refreshed": refreshed,
            "failed": failed,
            "records": records,
        }

    @classmethod
    def _ensure_reference_history(cls, name, symbol, opening_date, as_of, cache):
        """
        Resolve a standard MIS reference instrument to a global Asset and make
        sure its Yahoo historical prices are available for the requested dates.
        Reference assets are not family holdings and therefore never affect
        portfolio ownership or valuation.
        """
        cache_key = (symbol, opening_date, as_of)
        if cache_key in cache:
            return cache[cache_key]

        asset = (
            Asset.objects
            .filter(family__isnull=True, symbol=symbol)
            .order_by("id")
            .first()
        )
        if asset is None:
            asset = Asset.objects.create(
                owner=None,
                family=None,
                name=name,
                category="OTHER",
                symbol=symbol,
                currency="INR",
                is_active=True,
            )

        has_opening = MarketPrice.objects.filter(
            asset=asset,
            date__lte=opening_date,
        ).exists()
        has_closing = MarketPrice.objects.filter(
            asset=asset,
            date__lte=as_of,
        ).exists()

        if not (has_opening and has_closing):
            try:
                # REIT/InvIT units can be thinly traded. Fetch a look-back
                # window so a prior trading day is available when the requested
                # opening date itself has no trade.
                history_start = opening_date - timedelta(days=30)
                YahooFinanceService.save_history(
                    asset=asset,
                    symbol=symbol,
                    start=history_start,
                    end=as_of + timedelta(days=1),
                )
            except Exception:
                # Existing stored history remains usable even when an external
                # source is temporarily unavailable.
                pass

        cache[cache_key] = asset
        return asset

    @classmethod
    def _bse500_rate(cls, target_date, cache):
        """
        Read the BSE 500 price-return index from the official BSE Indices
        chart page. The page exposes recent daily chart points as
        'Date: DD Mon YYYY - Value: ...'. Values are never inferred from
        another index.
        """
        cache_key = ("BSE500", target_date)
        if cache_key in cache:
            return cache[cache_key]

        try:
            response = requests.get(
                "https://www.bseindices.com/indices-details/code/17/",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=10,
            )
            response.raise_for_status()
            html = response.text

            patterns = [
                r"Date\s*:\s*(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\s*[–-]\s*Value\s*:\s*([\d,]+(?:\.\d+)?)",
                r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4}).{0,80}?Value\s*:\s*([\d,]+(?:\.\d+)?)",
            ]
            month_map = {
                "jan": 1, "feb": 2, "mar": 3, "apr": 4,
                "may": 5, "jun": 6, "jul": 7, "aug": 8,
                "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
            }

            found = {}
            for pattern in patterns:
                for day, month_text, year, value in re.findall(pattern, html, flags=re.IGNORECASE):
                    month = month_map.get(month_text[:3].lower())
                    if not month:
                        continue
                    try:
                        point_date = date(int(year), month, int(day))
                        found[point_date] = Decimal(value.replace(",", ""))
                    except (TypeError, ValueError, ArithmeticError):
                        continue

                if found:
                    break

            if found:
                eligible = [
                    (point_date, value)
                    for point_date, value in found.items()
                    if point_date <= target_date
                ]
                result = max(eligible, key=lambda item: item[0])[1] if eligible else None
            else:
                result = None
        except Exception:
            result = None

        cache[cache_key] = result
        return result

    @classmethod
    def _reference_rate(cls, name, symbol, as_of, opening_date, cache):
        symbols = symbol if isinstance(symbol, (tuple, list)) else (symbol,)
        for candidate_symbol in symbols:
            asset = cls._ensure_reference_history(
                name=name,
                symbol=candidate_symbol,
                opening_date=opening_date,
                as_of=as_of,
                cache=cache,
            )

            def value_for(target_date):
                return (
                    MarketPrice.objects
                    .filter(asset=asset, date__lte=target_date)
                    .order_by("-date", "-id")
                    .values_list("close_price", flat=True)
                    .first()
                )

            opening = value_for(opening_date)
            closing = value_for(as_of)
            if opening is not None or closing is not None:
                return opening, closing

        return None, None

    @staticmethod
    def _normalize_note_name(value):
        return "".join(
            character
            for character in str(value or "").lower()
            if character.isalnum()
        )

    @classmethod
    def _family_note_rate(cls, family, aliases, opening_date, as_of):
        """
        Look up a standard note item against the family's actual assets first.
        This fixes cases where the uploaded/transaction asset name differs from
        the display name in the standard MIS template.
        """
        aliases = [cls._normalize_note_name(alias) for alias in aliases]
        assets = Asset.objects.filter(family=family, is_active=True)

        candidates = []
        for asset in assets:
            name = cls._normalize_note_name(asset.name)
            symbol = cls._normalize_note_name(asset.symbol)
            if any(
                alias and (name == alias or (len(alias) > 4 and alias in name))
                for alias in aliases
            ) or any(
                alias and symbol == alias
                for alias in aliases
            ):
                candidates.append(asset)

        if not candidates:
            return None, None

        def price_for(target_date):
            values = []
            for asset in candidates:
                value = (
                    MarketPrice.objects
                    .filter(asset=asset, date__lte=target_date)
                    .order_by("-date", "-id")
                    .values_list("close_price", flat=True)
                    .first()
                )
                if value is not None:
                    values.append(Decimal(str(value)))
            if not values:
                return None
            if len(set(values)) == 1:
                return values[0]
            return sum(values) / Decimal(len(values))

        return price_for(opening_date), price_for(as_of)

    @classmethod
    def _build_notes(cls, family, data_rows, opening_date, as_of):
        """
        Build the fixed six-section Notes to MIS format.

        Listed reference instruments use the application's historical Yahoo
        Finance price store when the family does not already contain the
        instrument. Family-owned/unlisted instruments use their stored
        MarketPrice history. The fixed display list is never expanded.
        """
        standard_sections = [
            {
                "section": "reits",
                "section_number": 1,
                "title": "REITS Rate movement are as below:",
                "unit_label": "Unit Rate",
                "change_label": "Change In Rate",
                "particulars": [
                    ("Mindspace Business Parks", ("mindspace business parks", "MINDSPACE.NS")),
                    ("Embassy Office Parks", ("embassy office parks", "EMBASSY.NS")),
                    ("Brookfield India Real Estate Trust", ("brookfield india real estate trust", "brookfield india reit", "BIRET.NS")),
                    ("National Highways Infra Trust", ("national highways infra trust", "NHIT", "NHIT.NS")),
                    ("Nexus Select Trust", ("nexus select trust", "NXST.NS")),
                    ("Knowledge Realty Trust", ("knowledge realty trust", "KRT", "KRT.NS")),
                    ("Bagmane Prime Office Reit", ("bagmane prime office reit", "BAGMANE", "BAGMANERR.NS")),
                    ("NDR InvIT", ("ndr invit", "NDRINVIT", "NDRINVIT.NS")),
                    ("Cube InvIT", ("cube invit", "cube highways trust", "CUBEINVIT", "CUBEINVIT.NS")),
                ],
            },
            {
                "section": "sgb",
                "section_number": 2,
                "title": "Sovereign Gold Bonds rate movement are as below:",
                "unit_label": "Rate/grm",
                "change_label": "Change In Rate",
                "particulars": [
                    ("Sovereign Gold Bonds - 48Kg", ("sovereign gold bonds", "sovereign gold bond", "sgb")),
                ],
            },
            {
                "section": "silver",
                "section_number": 3,
                "title": "Silver ETF",
                "unit_label": "Rate/Unit",
                "change_label": "Change in Level",
                "particulars": [
                    ("ICICI Prudential Silver ETF Rate/Unit", ("icici prudential silver etf", "icici prudential silver etf rate/unit")),
                ],
            },
            {
                "section": "indices",
                "section_number": 4,
                "title": "Nifty 50 & BSE 500 Level",
                "unit_label": "Level",
                "change_label": "Change in Level",
                "particulars": [
                    ("Nifty 50", ("nifty 50", "^NSEI")),
                    ("BSE 500", ("bse 500", "BSE500")),
                ],
            },
            {
                "section": "unlisted",
                "section_number": 5,
                "title": "Unlisted Shares - Price considered below for MIS",
                "unit_label": "Unit Rate",
                "change_label": "Change in Level",
                "particulars": [
                    ("NSE", ("nse", "national stock exchange")),
                    ("Sterlite Electrical Ltd (Power Transmission)", ("sterlite electrical ltd", "sterlite electrical", "power transmission")),
                    ("Sterlite Grid 5 Ltd Unlisted Shares", ("sterlite grid 5", "sterlite grid 5 ltd")),
                ],
            },
            {
                "section": "dollar",
                "section_number": 6,
                "title": "Dollar Rate",
                "unit_label": "Rate",
                "change_label": "Change in Level",
                "particulars": [
                    ("$ Rate", ("usd/inr", "usd inr", "usd inr=x", "USDINR=X")),
                ],
            },
        ]

        reference_symbols = cls.REFERENCE_SYMBOLS
        history_cache = {}

        notes = []

        for section in standard_sections:
            items = []
            for name, aliases in section["particulars"]:
                opening_rate, closing_rate = cls._family_note_rate(
                    family,
                    aliases,
                    opening_date,
                    as_of,
                )

                # SGBs and other manually valued instruments may have a
                # ManualAssetPrice but no daily MarketPrice history. If the
                # stored manual valuation predates the requested closing date,
                # use it as the MIS reference valuation for both endpoints.
                if (
                    (opening_rate is None or closing_rate is None)
                    and name == "Sovereign Gold Bonds - 48Kg"
                ):
                    aliases_normalized = [
                        cls._normalize_note_name(alias)
                        for alias in aliases
                    ]
                    manual_assets = []
                    for asset in Asset.objects.filter(
                        family=family,
                        is_active=True,
                    ):
                        normalized_name = cls._normalize_note_name(asset.name)
                        if any(
                            alias
                            and (
                                normalized_name == alias
                                or (len(alias) > 4 and alias in normalized_name)
                            )
                            for alias in aliases_normalized
                        ):
                            manual_assets.append(asset)

                    manual_values = [
                        (
                            asset.manual_price.price,
                            asset.manual_price.price_date,
                        )
                        for asset in manual_assets
                        if hasattr(asset, "manual_price")
                        and asset.manual_price.price_date <= as_of
                    ]
                    if manual_values:
                        manual_values.sort(
                            key=lambda item: item[1],
                            reverse=True,
                        )
                        manual_price, manual_date = manual_values[0]
                        # A static MIS valuation is valid for the opening
                        # endpoint only when it was already effective then.
                        if manual_date <= opening_date:
                            opening_rate = opening_rate or manual_price
                        closing_rate = closing_rate or manual_price

                if opening_rate is None or closing_rate is None:
                    symbol = reference_symbols.get(name)
                    if symbol:
                        try:
                            ref_opening, ref_closing = cls._reference_rate(
                                name=name,
                                symbol=symbol,
                                opening_date=opening_date,
                                as_of=as_of,
                                cache=history_cache,
                            )
                            opening_rate = opening_rate or ref_opening
                            closing_rate = closing_rate or ref_closing
                        except Exception:
                            pass

                if name == "BSE 500" and (opening_rate is None or closing_rate is None):
                    opening_rate = opening_rate or cls._bse500_rate(opening_date, history_cache)
                    closing_rate = closing_rate or cls._bse500_rate(as_of, history_cache)

                # Silver ETF can also be resolved from the existing MIS rows,
                # preserving the historical value already used by the report.
                if name.startswith("ICICI Prudential Silver ETF") and (
                    opening_rate is None or closing_rate is None
                ):
                    normalized_aliases = [cls._normalize_note_name(alias) for alias in aliases]
                    for row in data_rows:
                        row_name = cls._normalize_note_name(row.get("asset_name"))
                        if any(alias in row_name for alias in normalized_aliases):
                            opening_rate = opening_rate or row.get("opening_nav")
                            closing_rate = closing_rate or row.get("closing_nav")
                            break

                change = None
                percent_change = None
                if opening_rate is not None and closing_rate is not None:
                    opening_rate = Decimal(str(opening_rate))
                    closing_rate = Decimal(str(closing_rate))
                    change = closing_rate - opening_rate
                    if opening_rate != 0:
                        percent_change = (change / opening_rate) * Decimal("100")

                items.append({
                    "name": name,
                    "opening_rate": opening_rate,
                    "closing_rate": closing_rate,
                    "change": change,
                    "percent_change": percent_change,
                })

            notes.append({
                "section": section["section"],
                "section_number": section["section_number"],
                "title": section["title"],
                "unit_label": section["unit_label"],
                "change_label": section["change_label"],
                "items": items,
            })

        silver_item = notes[2]["items"][0]
        silver_row = None
        for row in data_rows:
            if "icici prudential silver etf" in cls._normalize_note_name(row.get("asset_name")):
                silver_row = row
                break

        if silver_row and silver_row.get("rate") is not None and silver_item["closing_rate"] is not None:
            invested_rate = Decimal(str(silver_row["rate"]))
            closing_rate = Decimal(str(silver_item["closing_rate"]))
            if invested_rate != 0:
                notes[2]["note"] = (
                    f"We have invested at rate of {invested_rate:.2f}/Unit. "
                    f"Up from our buying {(closing_rate / invested_rate):.2f}X."
                )
            else:
                notes[2]["note"] = None
        else:
            notes[2]["note"] = None

        return {
            "title": f"Notes to MIS {as_of.strftime('%B-%Y').upper()}",
            "opening_label": opening_date.strftime("%b-%y").upper(),
            "closing_label": as_of.strftime("%b-%y").upper(),
            "sections": notes,
        }

    @staticmethod
    def _editable_notes_from_report(notes):
        columns = [
            {"id": "sr_no", "label": "Sr. No", "type": "number"},
            {"id": "particulars", "label": "Particulars", "type": "text"},
            {"id": "opening_rate", "label": "", "type": "number"},
            {"id": "closing_rate", "label": "", "type": "number"},
            {"id": "change", "label": "", "type": "number"},
            {"id": "percent_change", "label": "% Change", "type": "number"},
        ]
        sections = []
        for section in notes["sections"]:
            section_columns = [dict(column) for column in columns]
            section_columns[2]["label"] = f"{section['unit_label']} {notes['opening_label']}"
            section_columns[3]["label"] = f"{section['unit_label']} {notes['closing_label']}"
            section_columns[4]["label"] = section["change_label"]
            rows = []
            for index, item in enumerate(section["items"], 1):
                rows.append({
                    "id": f"row-{section['section']}-{index}",
                    "cells": {
                        "sr_no": index,
                        "particulars": item["name"],
                        "opening_rate": item["opening_rate"],
                        "closing_rate": item["closing_rate"],
                        "change": item["change"],
                        "percent_change": item["percent_change"],
                    },
                })
            sections.append({
                "id": section["section"],
                "section_number": section["section_number"],
                "title": section["title"],
                "note": section.get("note"),
                "columns": section_columns,
                "rows": rows,
            })
        return {
            "title": notes["title"],
            "opening_label": notes["opening_label"],
            "closing_label": notes["closing_label"],
            "sections": sections,
        }

    @staticmethod
    def _clean_editable_notes(document):
        if not isinstance(document, dict):
            raise ValueError("Notes document must be an object.")
        sections = document.get("sections")
        if not isinstance(sections, list):
            raise ValueError("Notes sections must be a list.")
        if len(sections) > 100:
            raise ValueError("Notes cannot contain more than 100 sections.")

        cleaned_sections = []
        for section_index, section in enumerate(sections, 1):
            if not isinstance(section, dict):
                raise ValueError("Each Notes section must be an object.")
            section_id = str(section.get("id") or f"section-{section_index}")[:100]
            title = str(section.get("title") or f"Section {section_index}")[:500]
            columns = section.get("columns") or []
            rows = section.get("rows") or []
            if not isinstance(columns, list) or not isinstance(rows, list):
                raise ValueError("Notes columns and rows must be lists.")
            if len(columns) > 50:
                raise ValueError("A Notes section cannot contain more than 50 columns.")
            if len(rows) > 1000:
                raise ValueError("A Notes section cannot contain more than 1000 rows.")

            cleaned_columns = []
            seen_column_ids = set()
            for column_index, column in enumerate(columns, 1):
                if not isinstance(column, dict):
                    raise ValueError("Each Notes column must be an object.")
                column_id = str(column.get("id") or f"column-{column_index}")[:100]
                if column_id in seen_column_ids:
                    raise ValueError("Notes column IDs must be unique within a section.")
                seen_column_ids.add(column_id)
                fixed_labels = {
                    "sr_no": "Sr. No",
                    "change": "Change In Rate",
                    "percent_change": "% Change",
                }
                if column_id in fixed_labels:
                    cleaned_columns.append({
                        "id": column_id,
                        "label": fixed_labels[column_id],
                        "type": "number",
                    })
                else:
                    cleaned_columns.append({
                        "id": column_id,
                        "label": str(column.get("label") or f"Column {column_index}")[:300],
                        "type": str(column.get("type") or "text")[:30],
                    })

            cleaned_rows = []
            valid_column_ids = {column["id"] for column in cleaned_columns}
            for row_index, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    raise ValueError("Each Notes row must be an object.")
                cells = row.get("cells") or {}
                if not isinstance(cells, dict):
                    raise ValueError("Notes row cells must be an object.")
                cleaned_cells = {
                    key: value
                    for key, value in cells.items()
                    if key in valid_column_ids
                }
                # These fields are report-derived and can never be persisted from
                # user input. Sr. No is regenerated below and rate changes are
                # calculated from the opening/closing rates.
                cleaned_cells.pop("sr_no", None)
                cleaned_cells.pop("change", None)
                cleaned_cells.pop("percent_change", None)
                cleaned_rows.append({
                    "id": str(row.get("id") or f"row-{section_id}-{row_index}")[:120],
                    "cells": cleaned_cells,
                })

            # Rebuild calculated Notes fields so clients cannot override them.
            for row_number, cleaned_row in enumerate(cleaned_rows, 1):
                cells = cleaned_row["cells"]
                cells["sr_no"] = row_number
                opening = cells.get("opening_rate")
                closing = cells.get("closing_rate")
                try:
                    opening_value = Decimal(str(opening)) if opening not in (None, "") else None
                    closing_value = Decimal(str(closing)) if closing not in (None, "") else None
                except Exception:
                    opening_value = None
                    closing_value = None
                if opening_value is not None and closing_value is not None:
                    change = closing_value - opening_value
                    cells["change"] = float(change)
                    cells["percent_change"] = (
                        float((change / opening_value) * Decimal("100"))
                        if opening_value != 0 else None
                    )
                else:
                    cells["change"] = None
                    cells["percent_change"] = None

            cleaned_sections.append({
                "id": section_id,
                "section_number": section.get("section_number", section_index),
                "title": title,
                "note": section.get("note"),
                "columns": cleaned_columns,
                "rows": cleaned_rows,
            })

        return {
            "title": str(document.get("title") or "Notes to MIS")[:500],
            "opening_label": str(document.get("opening_label") or "")[:100],
            "closing_label": str(document.get("closing_label") or "")[:100],
            "sections": cleaned_sections,
        }

    @staticmethod
    def _notes_change_summary(before, after):
        if before == after:
            return []
        changes = []
        before_sections = {str(item.get("id")): item for item in before.get("sections", [])}
        after_sections = {str(item.get("id")): item for item in after.get("sections", [])}

        for section_id in sorted(set(before_sections) | set(after_sections)):
            old = before_sections.get(section_id)
            new = after_sections.get(section_id)
            if old is None:
                changes.append({
                    "type": "section_added",
                    "section_id": section_id,
                    "new": new.get("title", ""),
                })
                continue
            if new is None:
                changes.append({
                    "type": "section_removed",
                    "section_id": section_id,
                    "old": old.get("title", ""),
                })
                continue

            section_name = new.get("title") or old.get("title") or section_id
            if old.get("title") != new.get("title"):
                changes.append({
                    "type": "section_renamed",
                    "section_id": section_id,
                    "section": section_name,
                    "old": old.get("title"),
                    "new": new.get("title"),
                })

            old_cols = {str(c.get("id")): c for c in old.get("columns", [])}
            new_cols = {str(c.get("id")): c for c in new.get("columns", [])}
            for column_id in sorted(set(old_cols) | set(new_cols)):
                if column_id not in old_cols:
                    changes.append({
                        "type": "column_added",
                        "section_id": section_id,
                        "section": section_name,
                        "column_id": column_id,
                        "new": new_cols[column_id].get("label"),
                    })
                elif column_id not in new_cols:
                    changes.append({
                        "type": "column_removed",
                        "section_id": section_id,
                        "section": section_name,
                        "column_id": column_id,
                        "old": old_cols[column_id].get("label"),
                    })
                elif old_cols[column_id].get("label") != new_cols[column_id].get("label"):
                    changes.append({
                        "type": "column_renamed",
                        "section_id": section_id,
                        "section": section_name,
                        "column_id": column_id,
                        "old": old_cols[column_id].get("label"),
                        "new": new_cols[column_id].get("label"),
                    })

            old_rows = {str(row.get("id")): row for row in old.get("rows", [])}
            new_rows = {str(row.get("id")): row for row in new.get("rows", [])}
            column_labels = {
                str(column.get("id")): column.get("label", "")
                for column in new.get("columns", [])
            }
            for row_id in sorted(set(old_rows) | set(new_rows)):
                if row_id not in old_rows:
                    cells = new_rows[row_id].get("cells", {})
                    changes.append({
                        "type": "row_added",
                        "section_id": section_id,
                        "section": section_name,
                        "row_id": row_id,
                        "new": cells.get("particulars") or row_id,
                    })
                elif row_id not in new_rows:
                    cells = old_rows[row_id].get("cells", {})
                    changes.append({
                        "type": "row_removed",
                        "section_id": section_id,
                        "section": section_name,
                        "row_id": row_id,
                        "old": cells.get("particulars") or row_id,
                    })
                else:
                    old_cells = old_rows[row_id].get("cells", {})
                    new_cells = new_rows[row_id].get("cells", {})
                    row_name = (
                        new_cells.get("particulars")
                        or old_cells.get("particulars")
                        or row_id
                    )
                    for field in sorted(set(old_cells) | set(new_cells)):
                        if old_cells.get(field) != new_cells.get(field):
                            changes.append({
                                "type": "cell_edited",
                                "section_id": section_id,
                                "section": section_name,
                                "row_id": row_id,
                                "row": row_name,
                                "column_id": field,
                                "column": column_labels.get(field, field),
                                "old": old_cells.get(field),
                                "new": new_cells.get(field),
                            })
        return changes

    @classmethod
    def _apply_saved_notes(cls, report_notes, document):
        editable = cls._clean_editable_notes(document)
        sections = []
        for section in editable["sections"]:
            columns = section["columns"]
            items = []
            for row in section["rows"]:
                cells = row["cells"]
                items.append({
                    "name": cells.get("particulars", ""),
                    "opening_rate": cells.get("opening_rate"),
                    "closing_rate": cells.get("closing_rate"),
                    "change": cells.get("change"),
                    "percent_change": cells.get("percent_change"),
                })
            sections.append({
                "section": section["id"],
                "section_number": section["section_number"],
                "title": section["title"],
                "unit_label": next((c["label"] for c in columns if c["id"] == "opening_rate"), ""),
                "change_label": next((c["label"] for c in columns if c["id"] == "change"), ""),
                "items": items,
                "note": section.get("note"),
            })
        report_notes["title"] = editable["title"]
        report_notes["opening_label"] = editable["opening_label"]
        report_notes["closing_label"] = editable["closing_label"]
        report_notes["sections"] = sections
        report_notes["editable"] = editable
        return report_notes

    @classmethod
    def editable_notes(cls, family, base_notes):
        saved = FamilyMISNotes.objects.filter(family=family).first()
        if saved and saved.document:
            return cls._apply_saved_notes(base_notes, saved.document)
        editable = cls._editable_notes_from_report(base_notes)
        base_notes["editable"] = editable
        return base_notes

    @classmethod
    def save_editable_notes(cls, family, user, document, base_notes):
        cleaned = cls._clean_editable_notes(document)
        saved = FamilyMISNotes.objects.filter(family=family).first()
        before = saved.document if saved else cls._editable_notes_from_report(base_notes)
        changes = cls._notes_change_summary(before, cleaned)
        if not changes:
            return cls._apply_saved_notes(base_notes, before), False

        if saved is None:
            saved = FamilyMISNotes(family=family)
        saved.document = cleaned
        saved.updated_by = user
        saved.save()
        from .models import FamilyMISNotesChangeLog
        FamilyMISNotesChangeLog.objects.create(
            family=family,
            user=user,
            changes=changes,
        )
        return cls._apply_saved_notes(base_notes, cleaned), True

    @classmethod
    def build(cls, family, from_date=None, to_date=None):
        if from_date is None and to_date is None:
            to_date = cls._latest_reporting_date(family)
            from_date = cls._period_start(to_date)
        elif from_date is None or to_date is None:
            raise ValueError("Both from_date and to_date are required.")
        elif from_date > to_date:
            raise ValueError("From date cannot be after to date.")
        elif to_date > date.today():
            raise ValueError("To date cannot be in the future.")

        as_of = to_date
        opening_date = cls._prior_month_end(as_of) if from_date == cls._period_start(as_of) else from_date - timedelta(days=1)
        prior_month_end = cls._prior_month_end(as_of)
        period_start = from_date

        rows = cls._base_rows(family)
        price_cache = {}
        nav_cache = {}
        data_rows = [
            cls._build_data_row(
                row,
                opening_date,
                as_of,
                period_start,
                price_cache,
                nav_cache,
            )
            for row in rows
        ]
        # Keep the complete set of report rows before applying the Data Sheet
        # display filter. A Tax Report must also retain positions that were fully
        # sold during the selected period because those rows can have realized
        # P/L/tax even when their closing units and market value are zero.
        all_data_rows = [
            cls._build_data_row(
                row,
                opening_date,
                as_of,
                period_start,
                price_cache,
                nav_cache,
            )
            for row in rows
        ]
        data_rows = [
            row for row in all_data_rows
            if row["closing_units"] > 0 or row["total_cost"] > 0 or row["closing_amount"] > 0
        ]
        data_rows.sort(key=lambda row: (row["asset_class"].casefold(), row["asset_name"].casefold(), row["family_name"].casefold()))

        # Tax Report uses the same valuation rows as the Data Sheet, but it also
        # includes rows with a transaction in the selected period when the
        # position was fully exited. This preserves realized FIFO P/L/tax.
        tax_settings_by_id, tax_settings_by_name = cls._tax_settings(family, rows)
        data_row_by_key = {
            (row["family_name"], row["sub_class"], row["asset_name"]): row
            for row in all_data_rows
        }
        tax_rows = []
        for base_row in rows:
            data_row = data_row_by_key.get(base_row["key"])
            if data_row is None:
                continue

            has_period_transaction = any(
                period_start <= tx.transaction_date <= as_of
                and (
                    (
                        base_row["kind"] == "asset"
                        and tx.transaction_type in (
                            TransactionType.BUY,
                            TransactionType.SIP,
                            TransactionType.SELL,
                        )
                    )
                    or (
                        base_row["kind"] == "mutual_fund"
                        and tx.transaction_type in (
                            "PURCHASE",
                            "SIP",
                            "REDEMPTION",
                        )
                    )
                )
                for tx in base_row["transactions"]
            )
            has_closing_position = (
                data_row["closing_units"] > 0
                or data_row["total_cost"] > 0
                or data_row["closing_amount"] > 0
            )
            if not has_closing_position and not has_period_transaction:
                continue

            tax_setting = None
            for asset_id in base_row.get("asset_ids", []):
                tax_setting = tax_settings_by_id.get(asset_id)
                if tax_setting is not None:
                    break
            if tax_setting is None:
                tax_setting = tax_settings_by_name.get(base_row["asset_name"])
            tax_rows.append(
                cls._build_tax_row(
                    base_row,
                    data_row,
                    as_of,
                    period_start,
                    tax_setting,
                )
            )
        tax_rows.sort(key=lambda row: (row["asset_class"].casefold(), row["asset_name"].casefold(), row["family_name"].casefold()))

        # IPS is a direct aggregation of the Data Sheet closing MTM, with the
        # previous-month closing MTM reconstructed using the same valuation logic.
        current_by_family_asset_class = defaultdict(Decimal)
        prior_by_family_asset_class = defaultdict(Decimal)
        family_names = sorted({row["family_name"] for row in data_rows}, key=str.casefold)

        for row in data_rows:
            current_by_family_asset_class[(row["asset_class"], row["family_name"])] += Decimal(str(row["closing_amount"] or 0))

        for row in rows:
            prior = cls._build_data_row(
                row,
                prior_month_end,
                prior_month_end,
                period_start,
                price_cache,
                nav_cache,
            )
            if prior["closing_units"] > 0 or prior["closing_amount"] > 0:
                prior_by_family_asset_class[(prior["asset_class"], prior["family_name"])] += Decimal(str(prior["closing_amount"] or 0))

        asset_classes = sorted({row["asset_class"] for row in data_rows}, key=str.casefold)
        ips_rows = []
        for asset_class in asset_classes:
            values = {}
            prior_values = {}
            for family_name in family_names:
                values[family_name] = current_by_family_asset_class.get((asset_class, family_name), Decimal("0"))
                prior_values[family_name] = prior_by_family_asset_class.get((asset_class, family_name), Decimal("0"))
            grand_total = sum(values.values(), Decimal("0"))
            prior_total = sum(prior_values.values(), Decimal("0"))
            ips_rows.append({
                "asset_class": asset_class,
                "family_values": values,
                "grand_total": grand_total,
                "prior_family_values": prior_values,
                "prior_total": prior_total,
                "difference": grand_total - prior_total,
            })

        # Fund Type.V2 in the workbook maps to the report's asset-class hierarchy.
        # Each asset class contains the individual asset names and their current
        # market value at the reporting date.
        fund_groups = defaultdict(list)
        for row in data_rows:
            fund_groups[row["asset_class"]].append(row)

        fund_type_summary = []
        for fund_type in sorted(fund_groups, key=str.casefold):
            fund_rows = sorted(
                fund_groups[fund_type],
                key=lambda row: row["asset_name"].casefold(),
            )
            subtotal = sum(
                (Decimal(str(row["closing_amount"] or 0)) for row in fund_rows),
                Decimal("0"),
            )
            fund_type_summary.append({
                "fund_type": fund_type,
                "rows": [
                    {
                        "fund_name": row["asset_name"],
                        "total": row["closing_amount"],
                    }
                    for row in fund_rows
                ],
                "subtotal": subtotal,
            })

        return {
            "family_name": family.name,
            "reporting_date": as_of,
            "opening_date": opening_date,
            "prior_month_date": prior_month_end,
            "period_start": period_start,
            "period_end": as_of,
            "family_names": family_names,
            "ips": ips_rows,
            "data_sheet": data_rows,
            "tax_report": tax_rows,
            "fund_type_summary": fund_type_summary,
            "notes": cls.editable_notes(family, cls._build_notes(family, data_rows, opening_date, as_of)),
            "summary": {
                "total_current_value": sum((Decimal(str(row["closing_amount"] or 0)) for row in data_rows), Decimal("0")),
                "number_of_rows": len(data_rows),
            },
        }
