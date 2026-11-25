from io import StringIO
import logging
import threading
from pathlib import Path

import pandas as pd
from typing import Any
import requests
from django.conf import settings

from config.scheduling.files import data_dir, replace_with_retry
from market_data.services.security_resolver import SecurityResolver
from market_data.services.yahoo_finance import YahooFinanceService

logger = logging.getLogger(__name__)

# One process-wide lock for every read and write of security_master.xlsx, so a
# reader never sees a half-written file and two writers never interleave.
WORKBOOK_LOCK = threading.RLock()
WORKBOOK_REPLACE_RETRIES = 3
WORKBOOK_REPLACE_DELAY_SECONDS = 0.3


def is_blank_value(value):
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and not value.strip()


class SecurityMasterGenerator:
    """
    Builds/refreshes PWMS security_master.xlsx from the database
    (Asset/Transaction) - no transactions.xlsx file required.

    Resolution order:
        1. NSE security master
        2. Existing SecurityResolver ISIN mapping
        3. Existing SecurityResolver name mapping
        4. Yahoo Finance search
        5. Validated Yahoo fallback

    Yahoo symbols are validated by actually requesting market data.
    """

    TRANSACTION_FILE = "transactions.xlsx"
    OUTPUT_FILE = "security_master.xlsx"

    NSE_EQUITY_URL = (
        "https://nsearchives.nseindia.com/"
        "content/equities/EQUITY_L.csv"
    )

    NSE_ETF_URL = (
        "https://nsearchives.nseindia.com/"
        "content/equities/eq_etfseclist.csv"
    )

    OUTPUT_COLUMNS = [
        "ISIN",
        "Security Name",
        "NSE Symbol",
        "BSE Symbol",
        "Yahoo Symbol",
        "Exchange",
        "Asset Type",
        "Asset Class",
        "Sub Class",
        "Underlying",
        "Sector",
        "Cap Type",
        "Manual NAV Enabled",
        "Manual NAV",
        "Active",
        "Resolution Status",
        "Price Source",
    ]

    # Explicitly validated Yahoo mappings for securities that
    # require a mapping outside the normal NSE equity CSV.
    VALIDATED_YAHOO_FALLBACKS = {
        "INF109KC1Y56": "SILVERBEES.NS",
        "INE0NR623014": "CUBEINVIT.NS",
    }

    @classmethod
    def _backend_dir(cls):
        return Path(settings.BASE_DIR)

    @classmethod
    def _data_dir(cls):
        return data_dir()

    @classmethod
    def _transaction_file(cls):
        return cls._data_dir() / cls.TRANSACTION_FILE

    @classmethod
    def _output_file(cls):
        return cls._data_dir() / cls.OUTPUT_FILE

    @staticmethod
    def _clean(value):
        if value is None:
            return ""

        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass

        return str(value).strip()

    @staticmethod
    def _normalize(value):
        return SecurityMasterGenerator._clean(value).upper()

    @staticmethod
    def _normalize_isin(value):
        return (
            SecurityMasterGenerator
            ._clean(value)
            .upper()
            .replace(" ", "")
        )

    @staticmethod
    def _normalize_column_name(value):
        return str(value).strip().upper()

    @classmethod
    def _find_column(cls, dataframe, *names):
        normalized = {
            cls._normalize_column_name(column): column
            for column in dataframe.columns
        }

        for name in names:
            key = cls._normalize_column_name(name)

            if key in normalized:
                return normalized[key]

        return None

    @classmethod
    def _read_transactions(cls):
        path = cls._transaction_file()

        if not path.exists():
            raise FileNotFoundError(
                f"Transaction file not found: {path}"
            )

        dataframe = pd.read_excel(
            path,
            sheet_name="Transactions",
            header=0,
        )

        if dataframe.empty:
            raise ValueError(
                "The Transactions sheet contains no data."
            )

        return dataframe

    @classmethod
    def _extract_transaction_securities(cls, dataframe):
        isin_column = cls._find_column(
            dataframe,
            "ISIN",
        )

        asset_name_column = cls._find_column(
            dataframe,
            "Asset Name",
        )

        asset_class_column = cls._find_column(
            dataframe,
            "Asset Class",
        )

        sub_class_column = cls._find_column(
            dataframe,
            "Sub Class",
        )

        underlying_column = cls._find_column(
            dataframe,
            "Underlying",
        )

        if isin_column is None:
            raise ValueError(
                "The Transactions sheet does not contain "
                "an ISIN column."
            )

        records = {}

        for _, row in dataframe.iterrows():
            isin = cls._normalize_isin(
                row.get(isin_column)
            )

            if not isin:
                continue

            asset_name = (
                cls._clean(row.get(asset_name_column))
                if asset_name_column
                else ""
            )

            asset_class = (
                cls._clean(row.get(asset_class_column))
                if asset_class_column
                else ""
            )

            sub_class = (
                cls._clean(row.get(sub_class_column))
                if sub_class_column
                else ""
            )

            underlying = (
                cls._clean(row.get(underlying_column))
                if underlying_column
                else ""
            )

            if isin not in records:
                records[isin] = {
                    "ISIN": isin,
                    "Security Name": asset_name,
                    "Asset Class": asset_class,
                    "Sub Class": sub_class,
                    "Underlying": underlying,
                }
                continue

            record = records[isin]

            if not record["Security Name"] and asset_name:
                record["Security Name"] = asset_name

            if not record["Asset Class"] and asset_class:
                record["Asset Class"] = asset_class

            if not record["Sub Class"] and sub_class:
                record["Sub Class"] = sub_class

            if not record["Underlying"] and underlying:
                record["Underlying"] = underlying

        return records

    # ==========================================================
    # DB-SOURCED SECURITIES (preferred - no transactions.xlsx
    # file required)
    # ==========================================================

    @classmethod
    def _extract_securities_from_db(cls):
        """
        Build the same {ISIN: {...}} record shape as
        _extract_transaction_securities(), but sourced directly
        from the database instead of transactions.xlsx.

        Transaction already carries the original Excel portfolio
        classification (asset_class / sub_class / asset_name /
        underlying) on every row, and Asset carries the ISIN - so
        the database has everything transactions.xlsx used to
        provide, with no separate file required.

        First non-empty value wins per ISIN, same as the xlsx
        path, by walking transactions oldest-first.
        """

        from investments.models import Asset, Transaction

        records = {}

        transactions = (
            Transaction.objects
            .select_related("asset")
            .exclude(asset__isin__isnull=True)
            .exclude(asset__isin__exact="")
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        for transaction in transactions:

            isin = cls._normalize_isin(
                transaction.asset.isin
            )

            if not isin:
                continue

            asset_name = cls._clean(
                # Asset.name is the canonical security name and is
                # preferred over transaction.asset_name, which in a
                # PMS/AIF/scheme-wrapped portfolio is the scheme's
                # name (e.g. "Buoyant Opportunities PMS"), not the
                # underlying security - using it here would feed
                # the wrong name into Yahoo Finance search below.
                # transaction.underlying is the same true security
                # name and is the fallback if Asset.name is ever
                # blank.
                transaction.asset.name
                or transaction.underlying
                or transaction.asset_name
            )

            asset_class = cls._clean(
                transaction.asset_class
            )

            sub_class = cls._clean(
                transaction.sub_class
            )

            underlying = cls._clean(
                transaction.underlying
            )

            if isin not in records:
                records[isin] = {
                    "ISIN": isin,
                    "Security Name": asset_name,
                    "Asset Class": asset_class,
                    "Sub Class": sub_class,
                    "Underlying": underlying,
                }
                continue

            record = records[isin]

            if not record["Security Name"] and asset_name:
                record["Security Name"] = asset_name

            if not record["Asset Class"] and asset_class:
                record["Asset Class"] = asset_class

            if not record["Sub Class"] and sub_class:
                record["Sub Class"] = sub_class

            if not record["Underlying"] and underlying:
                record["Underlying"] = underlying

        # ------------------------------------------------------
        # Defensive: an asset can in principle have an ISIN but
        # no transactions yet (e.g. created directly, not via
        # import). Include it too so it still gets resolved,
        # using Asset fields since there's no Transaction row to
        # pull classification from.
        # ------------------------------------------------------

        assets_with_isin = (
            Asset.objects
            .exclude(isin__isnull=True)
            .exclude(isin__exact="")
        )

        for asset in assets_with_isin:

            isin = cls._normalize_isin(asset.isin)

            if not isin or isin in records:
                continue

            records[isin] = {
                "ISIN": isin,
                "Security Name": cls._clean(asset.name),
                "Asset Class": cls._clean(asset.category),
                "Sub Class": "",
                "Underlying": "",
            }

        return records

    @classmethod
    def _download_nse_csv(cls, url):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151.0 Safari/537.36"
            ),
            "Accept": (
                "text/csv,text/plain,"
                "application/octet-stream,*/*"
            ),
            "Referer": "https://www.nseindia.com/",
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        return pd.read_csv(
            StringIO(
                response.content.decode(
                    "utf-8-sig",
                    errors="replace",
                )
            )
        )

    @classmethod
    def _prepare_nse_dataframe(cls, dataframe):
        dataframe = dataframe.copy()

        dataframe.columns = [
            cls._normalize_column_name(column)
            for column in dataframe.columns
        ]

        isin_column = cls._find_column(
            dataframe,
            "ISIN NUMBER",
            "ISIN",
        )

        symbol_column = cls._find_column(
            dataframe,
            "SYMBOL",
        )

        name_column = cls._find_column(
            dataframe,
            "NAME OF COMPANY",
            "NAME",
        )

        if isin_column is None:
            return pd.DataFrame()

        output = pd.DataFrame()

        output["ISIN"] = (
            dataframe[isin_column]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
            .str.replace(
                " ",
                "",
                regex=False,
            )
        )

        output["SYMBOL"] = (
            dataframe[symbol_column]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
            if symbol_column is not None
            else ""
        )

        output["NAME"] = (
            dataframe[name_column]
            .fillna("")
            .astype(str)
            .str.strip()
            if name_column is not None
            else ""
        )

        filtered_output: Any = output[
            output["ISIN"] != ""
        ]
        return filtered_output.drop_duplicates(subset=["ISIN"])

    @classmethod
    def _load_nse_master(cls):
        frames = []

        for url in (
            cls.NSE_EQUITY_URL,
            cls.NSE_ETF_URL,
        ):
            try:
                dataframe = cls._download_nse_csv(url)

                prepared = cls._prepare_nse_dataframe(
                    dataframe
                )

                if not prepared.empty:
                    frames.append(prepared)

            except Exception:
                continue

        if not frames:
            return {}

        combined = pd.concat(
            frames,
            ignore_index=True,
        )

        combined = combined.drop_duplicates(
            subset=["ISIN"],
            keep="first",
        )

        return {
            row["ISIN"]: {
                "symbol": row["SYMBOL"],
                "name": row["NAME"],
            }
            for _, row in combined.iterrows()
        }

    @classmethod
    def _asset_type(cls, asset_class, sub_class):
        combined = (
            f"{cls._normalize(asset_class)} "
            f"{cls._normalize(sub_class)}"
        )

        if "ETF" in combined:
            return "ETF"

        if "MUTUAL" in combined:
            return "MUTUAL_FUND"

        if "BOND" in combined:
            return "BOND"

        if "DEBT" in combined:
            return "BOND"

        if "GOLD" in combined:
            return "GOLD"

        if "REIT" in combined:
            return "ETF"

        if "INVIT" in combined:
            return "ETF"

        if "EQUITY" in combined:
            return "STOCK"

        if "STOCK" in combined:
            return "STOCK"

        return "OTHER"

    @classmethod
    def _validate_yahoo_symbol(cls, symbol):
        if not symbol:
            return False

        try:
            dataframe = (
                YahooFinanceService.fetch_history(
                    symbol=symbol,
                    period="5d",
                )
            )

            return (
                dataframe is not None
                and not dataframe.empty
                and "Close" in dataframe.columns
            )

        except Exception:
            return False

    @classmethod
    def _search_yahoo_symbol(
        cls,
        security_name,
    ):
        """Yahoo search, accepted only on an exact normalized name match.

        Same rule as the price resolver: "&" = "AND", "LTD" = "LIMITED",
        "(I)" = "INDIA", punctuation/spacing/case ignored. A different
        company with a similar name is never picked.
        """
        if not security_name:
            return None
        symbol = SecurityResolver.search_yahoo_exact_name(security_name)
        if symbol and cls._validate_yahoo_symbol(symbol):
            return symbol
        return None

    @classmethod
    def _resolve_yahoo_symbol(
        cls,
        isin,
        security_name,
        nse_symbol,
    ):
        # 1. Explicitly validated mappings.
        fallback = cls.VALIDATED_YAHOO_FALLBACKS.get(
            isin
        )

        if fallback and cls._validate_yahoo_symbol(
            fallback
        ):
            return fallback

        # 2. NSE symbol.
        if nse_symbol:
            candidate = f"{nse_symbol}.NS"

            if cls._validate_yahoo_symbol(candidate):
                return candidate

        # 3. Existing ISIN mapping.
        candidate = (
            SecurityResolver.resolve_from_isin(isin)
        )

        if candidate and cls._validate_yahoo_symbol(
            candidate
        ):
            return candidate

        # 4. Existing name mapping.
        candidate = (
            SecurityResolver.resolve_from_name(
                security_name
            )
        )

        if candidate and cls._validate_yahoo_symbol(
            candidate
        ):
            return candidate

        # 5. Yahoo Finance search.
        return cls._search_yahoo_symbol(
            security_name
        )

    @classmethod
    def _build_record(
        cls,
        transaction_record,
        nse_master,
    ):
        isin = transaction_record["ISIN"]
        security_name = transaction_record["Security Name"]
        asset_class = transaction_record["Asset Class"]
        sub_class = transaction_record["Sub Class"]
        underlying = transaction_record["Underlying"]

        asset_type = cls._asset_type(
            asset_class,
            sub_class,
        )

        nse_record = nse_master.get(
            isin,
            {},
        )

        nse_symbol = cls._clean(
            nse_record.get("symbol")
        )

        nse_name = cls._clean(
            nse_record.get("name")
        )

        if not security_name:
            security_name = nse_name

        yahoo_symbol = cls._resolve_yahoo_symbol(
            isin=isin,
            security_name=security_name,
            nse_symbol=nse_symbol,
        )

        if yahoo_symbol:
            resolution_status = "RESOLVED"
            price_source = "YAHOO_FINANCE"

        elif asset_type in {
            "MUTUAL_FUND",
            "BOND",
            "GOLD",
            "OTHER",
        }:
            resolution_status = "NON_YAHOO_ASSET"
            price_source = "MANUAL_OR_ASSET_SPECIFIC"

        else:
            resolution_status = "UNRESOLVED"
            price_source = ""

        return {
            "ISIN": isin,
            "Security Name": security_name,
            "NSE Symbol": nse_symbol,
            "BSE Symbol": "",
            "Yahoo Symbol": yahoo_symbol or "",
            "Exchange": "NSE" if nse_symbol else "",
            "Asset Type": asset_type,
            "Asset Class": asset_class,
            "Sub Class": sub_class,
            "Underlying": underlying,
            "Sector": "",
            "Cap Type": "",
            "Manual NAV Enabled": (
                asset_type
                in {
                    "MUTUAL_FUND",
                    "BOND",
                    "GOLD",
                    "OTHER",
                }
            ),
            "Manual NAV": None,
            "Active": True,
            "Resolution Status": resolution_status,
            "Price Source": price_source,
        }

    @classmethod
    def persist_resolved_symbol(cls, *, isin, security_name, yahoo_symbol):
        """Add or update one row for a newly resolved symbol.

        Rows are matched by ISIN; a name-only security (blank ISIN) is
        matched by exact normalized name among rows without an ISIN. An
        existing different symbol is never replaced (ValueError). A locked
        workbook raises OSError so the caller can queue the write. The
        resolver cache is updated by the caller; the workbook is not
        re-read.
        """
        cleaned_isin = cls._normalize_isin(isin)
        cleaned_symbol = SecurityResolver.clean_symbol(yahoo_symbol)
        cleaned_name = cls._clean(security_name)
        if not cleaned_symbol or not (cleaned_isin or cleaned_name):
            return False

        import openpyxl

        output_path = cls._output_file()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(f".{output_path.stem}.resolved.tmp.xlsx")

        with WORKBOOK_LOCK:
            if output_path.exists():
                workbook = openpyxl.load_workbook(output_path)
                worksheet = workbook["Security Master"] if "Security Master" in workbook.sheetnames else workbook.active
                headers = [cell.value for cell in worksheet[1]]
                header_indexes = {
                    str(value).strip().upper(): index + 1
                    for index, value in enumerate(headers) if value is not None
                }
                isin_column = header_indexes.get("ISIN")
                symbol_column = header_indexes.get("YAHOO SYMBOL")
                name_column = header_indexes.get("SECURITY NAME")
                if isin_column is None or symbol_column is None:
                    workbook.close()
                    raise ValueError("Security Master must contain ISIN and Yahoo Symbol columns.")

                if cleaned_isin:
                    matches = [
                        row_number for row_number in range(2, worksheet.max_row + 1)
                        if cls._normalize_isin(worksheet.cell(row=row_number, column=isin_column).value) == cleaned_isin
                    ]
                else:
                    target = SecurityResolver.normalize_security_name(cleaned_name)
                    matches = [
                        row_number for row_number in range(2, worksheet.max_row + 1)
                        if not cls._normalize_isin(worksheet.cell(row=row_number, column=isin_column).value)
                        and name_column
                        and SecurityResolver.normalize_security_name(
                            worksheet.cell(row=row_number, column=name_column).value
                        ) == target
                    ]
                if len(matches) > 1:
                    workbook.close()
                    raise ValueError(f"Multiple Security Master rows found for {cleaned_isin or cleaned_name}.")

                row_number = matches[0] if matches else worksheet.max_row + 1
                if not matches:
                    worksheet.cell(row=row_number, column=isin_column).value = cleaned_isin or None
                    if name_column:
                        worksheet.cell(row=row_number, column=name_column).value = cleaned_name

                existing_symbol = SecurityResolver.clean_symbol(worksheet.cell(row=row_number, column=symbol_column).value)
                if existing_symbol and existing_symbol != cleaned_symbol:
                    workbook.close()
                    raise ValueError(
                        f"Security Master already maps {cleaned_isin or cleaned_name} to {existing_symbol}; "
                        f"refusing to replace it with {cleaned_symbol}."
                    )
                if existing_symbol == cleaned_symbol:
                    workbook.close()
                    return False

                worksheet.cell(row=row_number, column=symbol_column).value = cleaned_symbol
                for header, value in (
                    ("RESOLUTION STATUS", "RESOLVED"),
                    ("PRICE SOURCE", "YAHOO_FINANCE"),
                    ("ACTIVE", True),
                ):
                    column = header_indexes.get(header)
                    if column:
                        # The persisted symbol returned price data for this
                        # security; status/source reflect that mapping.
                        worksheet.cell(row=row_number, column=column).value = value
            else:
                workbook = openpyxl.Workbook()
                worksheet = workbook.active
                worksheet.title = "Security Master"
                worksheet.append(cls.OUTPUT_COLUMNS)
                record = {column: None for column in cls.OUTPUT_COLUMNS}
                record.update({
                    "ISIN": cleaned_isin or None,
                    "Security Name": cleaned_name,
                    "Yahoo Symbol": cleaned_symbol,
                    "Active": True,
                    "Resolution Status": "RESOLVED",
                    "Price Source": "YAHOO_FINANCE",
                })
                worksheet.append([record[column] for column in cls.OUTPUT_COLUMNS])
                worksheet.freeze_panes = "A2"
                worksheet.auto_filter.ref = worksheet.dimensions

            try:
                workbook.save(temp_path)
                workbook.close()
                replace_with_retry(
                    temp_path, output_path,
                    retries=WORKBOOK_REPLACE_RETRIES, retry_delay=WORKBOOK_REPLACE_DELAY_SECONDS,
                )
            except Exception:
                try:
                    workbook.close()
                except Exception:
                    pass
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise
        return True

    @classmethod
    def _merge_existing_records(cls, records):
        """
        Merge freshly resolved records with the current workbook.

        Existing rows and trusted mappings are retained. A refresh must not
        erase manual NAV settings, metadata, or a previously stored Yahoo
        symbol just because a provider is temporarily unavailable.
        """
        existing_path = cls._output_file()
        if not existing_path.exists():
            return records

        name_only_rows = []
        try:
            import openpyxl

            workbook = openpyxl.load_workbook(
                existing_path,
                read_only=True,
                data_only=True,
            )
            worksheet = workbook["Security Master"] if "Security Master" in workbook.sheetnames else workbook.active
            rows = worksheet.iter_rows(values_only=True)
            headers = next(rows, ())
            indexes = {
                str(header).strip(): index
                for index, header in enumerate(headers)
                if header is not None
            }
            existing_by_isin = {}

            for row in rows:
                isin_index = indexes.get("ISIN")
                if isin_index is None or isin_index >= len(row):
                    continue
                isin = cls._normalize_isin(row[isin_index])
                if not isin:
                    # Name-only rows (added by resolve-and-save for assets
                    # without an ISIN) are kept as they are.
                    values = {
                        column: row[indexes[column]]
                        for column in cls.OUTPUT_COLUMNS
                        if column in indexes and indexes[column] < len(row)
                    }
                    if not is_blank_value(values.get("Yahoo Symbol")) or not is_blank_value(values.get("Security Name")):
                        name_only_rows.append(values)
                    continue
                existing_by_isin[isin] = {
                    column: row[indexes[column]]
                    for column in cls.OUTPUT_COLUMNS
                    if column in indexes and indexes[column] < len(row)
                }

            workbook.close()
        except Exception:
            # If the existing workbook is unreadable, fail closed rather
            # than silently overwrite it with a potentially incomplete file.
            raise RuntimeError(
                f"Unable to safely merge existing security master: {existing_path}"
            )

        is_blank = is_blank_value

        merged_records = []
        seen_isins = set()

        for fresh in records:
            isin = cls._normalize_isin(fresh.get("ISIN"))
            previous = existing_by_isin.get(isin, {})
            merged = dict(previous)

            for column in cls.OUTPUT_COLUMNS:
                new_value = fresh.get(column)
                old_value = previous.get(column)

                if column == "Yahoo Symbol":
                    # Existing mapping wins. Fresh symbols are already
                    # provider-validated by _resolve_yahoo_symbol().
                    merged[column] = old_value if not is_blank(old_value) else new_value
                elif column in {"Manual NAV Enabled", "Active"}:
                    # These are user/operational settings, not resolver output.
                    merged[column] = old_value if not is_blank(old_value) else new_value
                elif column == "Manual NAV":
                    merged[column] = old_value if not is_blank(old_value) else new_value
                else:
                    merged[column] = new_value if not is_blank(new_value) else old_value

            if not is_blank(merged.get("Yahoo Symbol")):
                merged["Resolution Status"] = "RESOLVED"
                if is_blank(merged.get("Price Source")):
                    merged["Price Source"] = "YAHOO_FINANCE"

            merged_records.append(merged)
            if isin:
                seen_isins.add(isin)

        # Preserve workbook-only/manual records even if they are not present
        # in today's database extraction.
        for isin, previous in existing_by_isin.items():
            if isin not in seen_isins:
                preserved = {column: previous.get(column) for column in cls.OUTPUT_COLUMNS}
                merged_records.append(preserved)
        for previous in name_only_rows:
            merged_records.append({column: previous.get(column) for column in cls.OUTPUT_COLUMNS})

        return merged_records

    @classmethod
    def _write_excel(cls, records):
        output_path = cls._output_file()
        temp_path = output_path.with_name(
            f".{output_path.stem}.candidate.xlsx"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with WORKBOOK_LOCK:
            return cls._write_excel_locked(records, output_path, temp_path)

    @classmethod
    def _write_excel_locked(cls, records, output_path, temp_path):
        records = cls._merge_existing_records(records)

        dataframe = pd.DataFrame(
            records,
            columns=cls.OUTPUT_COLUMNS,
        )

        dataframe = dataframe.sort_values(
            by=[
                "Asset Type",
                "Security Name",
                "ISIN",
            ],
            na_position="last",
        )

        with pd.ExcelWriter(
            temp_path,
            engine="openpyxl",
        ) as writer:
            dataframe.to_excel(
                writer,
                sheet_name="Security Master",
                index=False,
            )

            worksheet = (
                writer.book["Security Master"]
            )

            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = (
                worksheet.dimensions
            )

            widths = {
                "A": 18,
                "B": 42,
                "C": 18,
                "D": 18,
                "E": 20,
                "F": 12,
                "G": 18,
                "H": 18,
                "I": 24,
                "J": 32,
                "K": 22,
                "L": 16,
                "M": 22,
                "N": 16,
                "O": 10,
                "P": 20,
                "Q": 28,
            }

            for column, width in widths.items():
                worksheet.column_dimensions[
                    column
                ].width = width

            for cell in worksheet[1]:
                cell.font = cell.font.copy(
                    bold=True
                )

            readme = writer.book.create_sheet(
                "README"
            )

            readme.append(
                [
                    "Field",
                    "Value",
                ]
            )

            readme_rows = [
                (
                    "Purpose",
                    "Central ISIN-based security mapping "
                    "for PWMS market-data resolution.",
                ),
                (
                    "Security source",
                    "Database (Asset/Transaction) - "
                    "transactions.xlsx is only used as a "
                    "fallback if present and the database has "
                    "no ISIN-based securities.",
                ),
                (
                    "NSE equity source",
                    cls.NSE_EQUITY_URL,
                ),
                (
                    "NSE ETF source",
                    cls.NSE_ETF_URL,
                ),
                (
                    "Yahoo resolution",
                    "NSE symbol, existing resolver mappings, "
                    "validated fallbacks, then Yahoo search.",
                ),
                (
                    "Validation",
                    "Yahoo symbols are only marked RESOLVED "
                    "after market data is returned.",
                ),
                (
                    "UNRESOLVED",
                    "No validated Yahoo Finance symbol was found.",
                ),
                (
                    "Important",
                    "This generator does not modify transactions "
                    "or database records.",
                ),
            ]

            for row in readme_rows:
                readme.append(row)

            readme.column_dimensions["A"].width = 28
            readme.column_dimensions["B"].width = 110

            for cell in readme[1]:
                cell.font = cell.font.copy(
                    bold=True
                )

        # Replace the active workbook only after the candidate was written
        # successfully. This prevents a partial write from corrupting it;
        # retries cover a brief Windows sharing violation. A workbook open in
        # Excel makes this raise, so the nightly job fails and retries later.
        try:
            replace_with_retry(temp_path, output_path)
        except OSError:
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise

        return output_path

    @classmethod
    def generate(cls):
        """
        Build/refresh security_master.xlsx.

        Sources securities from the database first (Asset/
        Transaction - no file required). Falls back to
        transactions.xlsx only if the database has no ISIN-based
        securities at all and the file happens to exist, so
        existing setups that still rely on the file keep working.
        """

        transaction_records = (
            cls._extract_securities_from_db()
        )

        if not transaction_records and cls._transaction_file().exists():

            transactions = cls._read_transactions()

            transaction_records = (
                cls._extract_transaction_securities(
                    transactions
                )
            )

        if not transaction_records:
            raise ValueError(
                "No ISIN-based securities were found "
                "in the database (and no transactions.xlsx "
                "fallback was available)."
            )

        nse_master = cls._load_nse_master()

        records = []

        for record in transaction_records.values():
            records.append(
                cls._build_record(
                    transaction_record=record,
                    nse_master=nse_master,
                )
            )

        output_path = cls._write_excel(records)

        dataframe = pd.DataFrame(records)

        resolved = int(
            (
                dataframe["Resolution Status"]
                == "RESOLVED"
            ).sum()
        )

        unresolved = int(
            (
                dataframe["Resolution Status"]
                == "UNRESOLVED"
            ).sum()
        )

        non_yahoo = int(
            (
                dataframe["Resolution Status"]
                == "NON_YAHOO_ASSET"
            ).sum()
        )

        return {
            "output": str(output_path),
            "total": len(records),
            "resolved": resolved,
            "unresolved": unresolved,
            "non_yahoo": non_yahoo,
        }