from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import re

import requests

from django.db.models import Max

from investments.models import Asset, Transaction, TransactionType
from market_data.models import MarketPrice, ManualAssetPrice
from mutual_funds.models import MutualFundNAV, MutualFundTransaction, MutualFundHolding
from portfolio.services.portfolio_tree_service import PortfolioTreeService
from market_data.services.yahoo_finance import YahooFinanceService


class MISReportService:
    """
    Builds the MIS in the same three logical sections as the supplied workbook:
    IPS, Data Sheet and Fund Type-wise Summary.

    Historical values are reconstructed from transaction quantities/cost basis and
    the latest available market price/NAV on or before each reporting date.
    """

    ZERO = Decimal("0")

    @staticmethod
    def _clean(value, default="Unassigned"):
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
            family_name = cls._clean(tx.family_name, family.name)
            asset_name = cls._clean(tx.asset_name, getattr(tx.asset, "name", "Unassigned"))
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
            family_name = cls._clean(tx.family_name, family.name)
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

        reference_symbols = {
            "Mindspace Business Parks": ("MINDSPACE.NS", "MINDSPACE.BO"),
            "Embassy Office Parks": ("EMBASSY.NS", "EMBASSY.BO"),
            "Brookfield India Real Estate Trust": ("BIRET.NS", "BIRET.BO"),
            "National Highways Infra Trust": ("NHIT.NS", "NHIT.BO"),
            "Nexus Select Trust": ("NXST.NS", "NXST.BO"),
            "Knowledge Realty Trust": ("KRT.NS", "KRT.BO"),
            "Bagmane Prime Office Reit": ("BAGMANE.NS", "BAGMANERR.NS", "BAGMANE.BO"),
            "NDR InvIT": ("NDRINVIT.NS", "NDRINVIT.BO"),
            "Cube InvIT": ("CUBEINVIT.NS", "CUBEINVIT.BO"),
            "Nifty 50": "^NSEI",
            "$ Rate": "USDINR=X",
            "BSE 500": "BSE-500.BO",
        }

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
        opening_date = from_date - timedelta(days=1)
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
        data_rows = [
            row for row in data_rows
            if row["closing_units"] > 0 or row["total_cost"] > 0 or row["closing_amount"] > 0
        ]
        data_rows.sort(key=lambda row: (row["asset_class"].casefold(), row["asset_name"].casefold(), row["family_name"].casefold()))

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
                opening_date,
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
            "fund_type_summary": fund_type_summary,
            "notes": cls._build_notes(family, data_rows, opening_date, as_of),
            "summary": {
                "total_current_value": sum((Decimal(str(row["closing_amount"] or 0)) for row in data_rows), Decimal("0")),
                "number_of_rows": len(data_rows),
            },
        }
