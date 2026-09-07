from decimal import Decimal, InvalidOperation
import hashlib
import logging

import pandas as pd
from django.db import transaction as db_transaction

from investments.models import (
    Asset,
    AssetCategory,
    Transaction,
    TransactionType,
)

from investments.services.security_master import (
    SecurityMasterService,
)

from mutual_funds.models import (
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
)

logger = logging.getLogger(__name__)

TRANSACTIONS_REQUIRED_COLUMNS = [
    "Family Name",
    "Asset Class",
    "Sub Class",
    "Asset Name",
    "Underlying",
    "Advisors",
    "ISIN",
    "Date",
    "Trans. Type",
    "Quantity",
    "Price",
    "Amount",
]


SUMMARY_REQUIRED_COLUMNS = [
    "Family Name",
    "Portfolio Name",
    "Asset Class",
    "Advisors",
    "Asset Name",
    "ISIN",
]


ASSET_CLASS_MAP = {
    "EQUITY": AssetCategory.STOCK,
    "STOCK": AssetCategory.STOCK,
    "DEBT": AssetCategory.BOND,
    "BOND": AssetCategory.BOND,
    "CASH": AssetCategory.CASH,
    "COMMODITY": AssetCategory.ETF,
    "REITS/INVITS": AssetCategory.ETF,
    "REIT": AssetCategory.ETF,
    "INVIT": AssetCategory.ETF,
    "AIF": AssetCategory.OTHER,
    "ALTERNATE": AssetCategory.OTHER,
    "LRS": AssetCategory.OTHER,
    "MUTUAL FUND": AssetCategory.MUTUAL_FUND,
    "MUTUAL_FUND": AssetCategory.MUTUAL_FUND,
    "ETF": AssetCategory.ETF,
    "GOLD": AssetCategory.GOLD,
    "REAL ESTATE": AssetCategory.REAL_ESTATE,
    "CRYPTO": AssetCategory.CRYPTO,
    "OTHER": AssetCategory.OTHER,
}


INVESTMENT_TRANSACTION_MAP = {
    "BUY": TransactionType.BUY,
    "SELL": TransactionType.SELL,
    "SIP": TransactionType.SIP,
    "DIVIDEND": TransactionType.DIVIDEND,
    "INTEREST": TransactionType.INTEREST,
    "DEPOSIT": TransactionType.DEPOSIT,
    "WITHDRAWAL": TransactionType.WITHDRAWAL,
    "BONUS": TransactionType.BONUS,
    "SPLIT": TransactionType.SPLIT,
    "OTHER": TransactionType.OTHER,
    "BUYBACK": TransactionType.SELL,
    "DIVIDEND REINVESTMENT": TransactionType.BUY,
}


MUTUAL_FUND_TRANSACTION_MAP = {
    "BUY": MutualFundTransactionType.PURCHASE,
    "PURCHASE": MutualFundTransactionType.PURCHASE,
    "SIP": MutualFundTransactionType.SIP,
    "SELL": MutualFundTransactionType.REDEMPTION,
    "REDEMPTION": MutualFundTransactionType.REDEMPTION,
    "DIVIDEND": MutualFundTransactionType.DIVIDEND,
    "DIVIDEND REINVESTMENT": MutualFundTransactionType.PURCHASE,
}


class TransactionImportError(Exception):
    """Raised when an Excel/CSV transaction import is invalid."""


class TransactionImporter:

    @staticmethod
    def _clean_string(value):
        if pd.isna(value):
            return ""

        return str(value).strip()

    @staticmethod
    def _normalize(value):
        return (
            TransactionImporter
            ._clean_string(value)
            .strip()
            .upper()
        )

    @staticmethod
    def _to_decimal(
        value,
        field_name,
        row_number,
    ):
        if pd.isna(value):
            return Decimal("0")

        try:
            cleaned = str(value).strip()

            if not cleaned:
                return Decimal("0")

            cleaned = (
                cleaned
                .replace(",", "")
                .replace("₹", "")
            )

            return Decimal(cleaned)

        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ) as exc:
            raise TransactionImportError(
                f"Invalid {field_name} at Excel row "
                f"{row_number}: {value}"
            ) from exc

    @staticmethod
    def _build_source_key(
        family_name,
        asset_class,
        sub_class,
        asset_name,
        underlying,
        advisors,
        isin,
        transaction_date,
        transaction_type,
        quantity,
        price,
        amount,
    ):
        values = [
            family_name,
            asset_class,
            sub_class,
            asset_name,
            underlying,
            advisors,
            isin,
            str(transaction_date),
            transaction_type,
            str(quantity),
            str(price),
            str(amount),
        ]

        normalized = "|".join(
            TransactionImporter._normalize(value)
            for value in values
        )

        return hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _read_excel(file):
        filename = getattr(
            file,
            "name",
            "",
        )

        if not filename.lower().endswith(".xlsx"):
            raise TransactionImportError(
                "The Excel importer requires an .xlsx file."
            )

        try:
            transactions = pd.read_excel(
                file,
                sheet_name="Transactions",
                header=0,
            )

        except Exception as exc:
            raise TransactionImportError(
                "Unable to read the Excel workbook. "
                "Expected a sheet named 'Transactions'."
            ) from exc

        # The "Summary" sheet is optional supplementary data (used
        # only to fill in Portfolio Name when present - see
        # _resolve_portfolio_from_summary). A workbook containing
        # only a Transactions sheet is perfectly valid and must
        # still import; only fail the whole import if the required
        # Transactions sheet itself could not be read.
        summary = None

        try:
            file.seek(0)

            summary = pd.read_excel(
                file,
                sheet_name="Summary",
                header=1,
            )

        except Exception:
            summary = None

        return transactions, summary

    @staticmethod
    def _read_csv(file):
        try:
            dataframe = pd.read_csv(file)
        except Exception as exc:
            raise TransactionImportError(
                "Unable to read the CSV transaction file."
            ) from exc

        return dataframe, None

    @staticmethod
    def _read_file(file):
        filename = getattr(
            file,
            "name",
            "",
        ).lower()

        if filename.endswith(".xlsx"):
            return TransactionImporter._read_excel(file)

        if filename.endswith(".csv"):
            return TransactionImporter._read_csv(file)

        raise TransactionImportError(
            "Only .xlsx and .csv files are supported."
        )

    @staticmethod
    def _validate_transaction_columns(dataframe):
        missing_columns = [
            column
            for column in TRANSACTIONS_REQUIRED_COLUMNS
            if column not in dataframe.columns
        ]

        if missing_columns:
            raise TransactionImportError(
                "Missing required transaction columns: "
                + ", ".join(missing_columns)
            )

    @staticmethod
    def _validate_summary_columns(dataframe):
        if dataframe is None:
            return

        missing_columns = [
            column
            for column in SUMMARY_REQUIRED_COLUMNS
            if column not in dataframe.columns
        ]

        if missing_columns:
            raise TransactionImportError(
                "Missing required Summary columns: "
                + ", ".join(missing_columns)
            )

    @staticmethod
    def _validate_dataframe(dataframe):
        if dataframe is None:
            raise TransactionImportError(
                "No transaction data was found."
            )

        if dataframe.empty:
            raise TransactionImportError(
                "The transaction file contains no data."
            )

        TransactionImporter._validate_transaction_columns(
            dataframe
        )

    @staticmethod
    def _normalize_transaction_type(
        value,
        row_number,
    ):
        transaction_type = (
            TransactionImporter
            ._clean_string(value)
            .upper()
        )

        if not transaction_type:
            raise TransactionImportError(
                "Transaction Type is empty at "
                f"Excel row {row_number}."
            )

        return transaction_type

    @staticmethod
    def _resolve_portfolio_from_summary(
        summary,
        family_name,
        asset_class,
        asset_name,
        underlying,
        advisors,
        isin,
    ):
        if summary is None or summary.empty:
            return None

        family = TransactionImporter._normalize(
            family_name
        )

        advisor = TransactionImporter._normalize(
            advisors
        )

        lookup_name = TransactionImporter._normalize(
            underlying
            if underlying
            else asset_name
        )

        normalized_isin = (
            TransactionImporter
            ._normalize(isin)
        )

        candidates = summary.copy()

        candidates["_family"] = (
            candidates["Family Name"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        candidates["_advisor"] = (
            candidates["Advisors"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        candidates["_asset_class"] = (
            candidates["Asset Class"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        candidates["_asset_name"] = (
            candidates["Asset Name"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        candidates["_isin"] = (
            candidates["ISIN"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        filtered = candidates[
            candidates["_family"] == family
        ]

        if advisor:
            filtered = filtered[
                filtered["_advisor"] == advisor
            ]

        if normalized_isin:
            isin_filtered = filtered[
                filtered["_isin"] == normalized_isin
            ]

            if not isin_filtered.empty:
                filtered = isin_filtered

        if lookup_name:
            name_filtered = filtered[
                filtered["_asset_name"] == lookup_name
            ]

            if not name_filtered.empty:
                filtered = name_filtered

        if not filtered.empty:
            portfolio = (
                TransactionImporter
                ._clean_string(
                    filtered.iloc[0]["Portfolio Name"]
                )
            )

            if portfolio:
                return portfolio

        filtered = candidates[
            candidates["_family"] == family
        ]

        if advisor:
            filtered = filtered[
                filtered["_advisor"] == advisor
            ]

        if lookup_name:
            filtered = filtered[
                filtered["_asset_name"] == lookup_name
            ]

        if not filtered.empty:
            portfolio = (
                TransactionImporter
                ._clean_string(
                    filtered.iloc[0]["Portfolio Name"]
                )
            )

            if portfolio:
                return portfolio

        if normalized_isin:
            filtered = candidates[
                (
                    candidates["_family"] == family
                )
                & (
                    candidates["_isin"]
                    == normalized_isin
                )
            ]

            if not filtered.empty:
                if advisor:
                    advisor_filtered = filtered[
                        filtered["_advisor"] == advisor
                    ]

                    if not advisor_filtered.empty:
                        filtered = advisor_filtered

                portfolio = (
                    TransactionImporter
                    ._clean_string(
                        filtered.iloc[0]["Portfolio Name"]
                    )
                )

                if portfolio:
                    return portfolio

        return None

    @staticmethod
    def resolve_security_identity_name(
        asset_name,
        underlying,
    ):
        """
        Determine the name that identifies the actual
        security/underlying instrument, as opposed to the
        Excel "Asset Name" column, which for several
        sub-classes (e.g. PMS strategies, AIFs, or a generic
        "Direct Equity" bucket) holds the name of the product
        or strategy that many DIFFERENT underlyings share.

        The Excel "Underlying" column always identifies the
        actual security when it is present. It is empty only
        for rows that genuinely have no separate underlying
        (e.g. mutual funds, cash-like entries, or a strategy's
        own cash leg), in which case the Asset Name already
        correctly identifies the instrument.

        This method contains no security-specific logic and
        works identically for every asset class/sub-class in
        the dataset.
        """

        underlying_clean = (
            TransactionImporter
            ._clean_string(underlying)
        )

        if underlying_clean:
            return underlying_clean

        return (
            TransactionImporter
            ._clean_string(asset_name)
        )

    @staticmethod
    def _fallback_portfolio(
        asset_class,
        sub_class,
        asset_name,
        underlying,
    ):
        subclass = (
            TransactionImporter
            ._normalize(sub_class)
        )

        if subclass in {
            "EQUITY PMS",
            "EQUITY AIF (CATEGORY III)",
        }:
            return (
                asset_name
                or sub_class
                or asset_class
            )

        return (
            sub_class
            or asset_class
            or "Unassigned"
        )

    @staticmethod
    def _get_or_create_asset(
        owner,
        asset_name,
        isin,
        asset_class,
        sub_class,
    ):
        normalized_isin = isin.strip()

        normalized_sub_class = (
            sub_class.strip().upper()
            if sub_class
            else ""
        )

        if "MUTUAL FUND" in normalized_sub_class:
            category = AssetCategory.MUTUAL_FUND

        elif (
            "GOLD BOND" in normalized_sub_class
            or normalized_sub_class in {
                "SGB",
                "SOVEREIGN GOLD BOND",
            }
        ):
            category = AssetCategory.BOND

        else:
            category = ASSET_CLASS_MAP.get(
                asset_class.upper()
            )

        if category is None:
            raise TransactionImportError(
                f"Unsupported Asset Class: "
                f"{asset_class}"
            )

        asset = None

        if normalized_isin:
            asset = (
                Asset.objects
                .filter(
                    owner=owner,
                    isin=normalized_isin,
                )
                .first()
            )

        if asset is None:
            asset = (
                Asset.objects
                .filter(
                    owner=owner,
                    name=asset_name,
                    category=category,
                )
                .first()
            )

        if asset is None:
            asset = Asset.objects.create(
                owner=owner,
                name=asset_name,
                category=category,
                isin=(
                    normalized_isin
                    or None
                ),
                currency="INR",
                is_active=True,
            )

        else:
            changed = False

            if asset.name != asset_name:
                asset.name = asset_name
                changed = True

            if (
                normalized_isin
                and asset.isin != normalized_isin
            ):
                asset.isin = normalized_isin
                changed = True

            if asset.category != category:
                asset.category = category
                changed = True

            if changed:
                asset.save()

        SecurityMasterService.get_or_create(
            owner=owner,
            asset=asset,
        )

        return asset

    @staticmethod
    def _get_or_create_mutual_fund_scheme(
        owner,
        asset_name,
        isin,
    ):
        normalized_isin = isin.strip()

        scheme = None

        if normalized_isin:
            scheme = (
                MutualFundScheme.objects
                .filter(
                    owner=owner,
                    isin_growth=normalized_isin,
                )
                .first()
            )

        if scheme is None:
            scheme = (
                MutualFundScheme.objects
                .filter(
                    owner=owner,
                    scheme_name=asset_name,
                )
                .first()
            )

        if scheme is None:
            scheme = (
                MutualFundScheme.objects.create(
                    owner=owner,
                    scheme_name=asset_name,
                    isin_growth=(
                        normalized_isin
                        or None
                    ),
                    is_active=True,
                )
            )

        else:
            changed = False

            if (
                normalized_isin
                and scheme.isin_growth
                != normalized_isin
            ):
                scheme.isin_growth = (
                    normalized_isin
                )
                changed = True

            if changed:
                scheme.save()

        return scheme

    @staticmethod
    def _find_existing_investment_transaction(
        owner,
        source_key,
    ):
        return (
            Transaction.objects
            .filter(
                owner=owner,
                source="EXCEL",
                source_key=source_key,
            )
            .first()
        )

    @staticmethod
    def _find_existing_mutual_fund_transaction(
        owner,
        family_name,
        portfolio,
        scheme,
        transaction_type,
        transaction_date,
        quantity,
        price,
        amount,
    ):
        return (
            MutualFundTransaction.objects
            .filter(
                owner=owner,
                family_name=family_name,
                portfolio=portfolio,
                scheme=scheme,
                transaction_type=transaction_type,
                transaction_date=transaction_date,
                units=quantity,
                nav=price,
                amount=amount,
            )
            .first()
        )

    @staticmethod
    def _validate_row(
        row,
        row_number,
    ):
        family_name = (
            TransactionImporter
            ._clean_string(
                row["Family Name"]
            )
        )

        asset_class = (
            TransactionImporter
            ._clean_string(
                row["Asset Class"]
            )
        )

        sub_class = (
            TransactionImporter
            ._clean_string(
                row["Sub Class"]
            )
        )

        asset_name = (
            TransactionImporter
            ._clean_string(
                row["Asset Name"]
            )
        )

        underlying = (
            TransactionImporter
            ._clean_string(
                row["Underlying"]
            )
        )

        advisors = (
            TransactionImporter
            ._clean_string(
                row["Advisors"]
            )
        )

        isin = (
            TransactionImporter
            ._clean_string(
                row["ISIN"]
            )
        )

        if not family_name:
            raise TransactionImportError(
                "Family Name is empty at "
                f"Excel row {row_number}."
            )

        if not asset_class:
            raise TransactionImportError(
                "Asset Class is empty at "
                f"Excel row {row_number}."
            )

        if not asset_name:
            raise TransactionImportError(
                "Asset Name is empty at "
                f"Excel row {row_number}."
            )

        if asset_class.upper() not in ASSET_CLASS_MAP:
            raise TransactionImportError(
                "Unsupported Asset Class at "
                f"Excel row {row_number}: "
                f"{asset_class}"
            )

        # ---------------------------------------------------------
        # PMS/AIF strategy rows MUST identify the actual underlying
        # security (via Underlying or ISIN). Without one, the row
        # would silently be filed under the strategy's own name,
        # merging unrelated stocks into a single fake "asset" with
        # no real ISIN/market price -- which corrupts quantity,
        # invested value, and XIRR for every stock caught in it.
        # ---------------------------------------------------------
        normalized_sub_class = sub_class.strip().upper()

        if (
            normalized_sub_class in {
                "EQUITY PMS",
                "EQUITY AIF (CATEGORY III)",
            }
            and not underlying
            and not isin
        ):
            logger.warning(
                "Row identifies neither an Underlying "
                "nor an ISIN for a PMS/AIF strategy at "
                "Excel row %s (%s). Imported using Asset "
                "Name as identity — verify this isn't a "
                "look-through holding that needs a real "
                "underlying security.",
                row_number,
                asset_name,
            )

        raw_date = row["Date"]

        if pd.isna(raw_date):
            raise TransactionImportError(
                "Date is empty at "
                f"Excel row {row_number}."
            )

        transaction_date = pd.to_datetime(
            raw_date,
            errors="coerce",
        )

        if pd.isna(transaction_date):
            raise TransactionImportError(
                "Invalid Date at "
                f"Excel row {row_number}: "
                f"{raw_date}"
            )

        transaction_date = transaction_date.date()

        transaction_type = (
            TransactionImporter
            ._normalize_transaction_type(
                row["Trans. Type"],
                row_number,
            )
        )

        quantity = (
            TransactionImporter
            ._to_decimal(
                row["Quantity"],
                "Quantity",
                row_number,
            )
        )

        price = (
            TransactionImporter
            ._to_decimal(
                row["Price"],
                "Price",
                row_number,
            )
        )

        amount = (
            TransactionImporter
            ._to_decimal(
                row["Amount"],
                "Amount",
                row_number,
            )
        )

        if quantity < 0:
            raise TransactionImportError(
                "Quantity cannot be negative at "
                f"Excel row {row_number}."
            )

        if price < 0:
            raise TransactionImportError(
                "Price cannot be negative at "
                f"Excel row {row_number}."
            )

        if amount < 0:
            raise TransactionImportError(
                "Amount cannot be negative at "
                f"Excel row {row_number}."
            )

        return {
            "family_name": family_name,
            "asset_class": asset_class,
            "sub_class": sub_class,
            "asset_name": asset_name,
            "underlying": underlying,
            "advisors": advisors,
            "isin": isin,
            "transaction_date": transaction_date,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "price": price,
            "amount": amount,
        }

    @staticmethod
    @db_transaction.atomic
    def import_file(file, owner):
        dataframe, summary = (
            TransactionImporter
            ._read_file(file)
        )

        TransactionImporter._validate_dataframe(
            dataframe
        )

        TransactionImporter._validate_summary_columns(
            summary
        )

        rows = []
        errors = []

        for row_number, (_, row) in enumerate(
            dataframe.iterrows(),
            start=2,
        ):
            try:
                parsed = (
                    TransactionImporter
                    ._validate_row(
                        row,
                        row_number,
                    )
                )

                portfolio = (
                    TransactionImporter
                    ._resolve_portfolio_from_summary(
                        summary=summary,
                        family_name=parsed["family_name"],
                        asset_class=parsed["asset_class"],
                        asset_name=parsed["asset_name"],
                        underlying=parsed["underlying"],
                        advisors=parsed["advisors"],
                        isin=parsed["isin"],
                    )
                )

                if not portfolio:
                    portfolio = (
                        TransactionImporter
                        ._fallback_portfolio(
                            asset_class=parsed["asset_class"],
                            sub_class=parsed["sub_class"],
                            asset_name=parsed["asset_name"],
                            underlying=parsed["underlying"],
                        )
                    )

                parsed["portfolio"] = portfolio

                parsed["source_key"] = (
                    TransactionImporter
                    ._build_source_key(
                        family_name=parsed["family_name"],
                        asset_class=parsed["asset_class"],
                        sub_class=parsed["sub_class"],
                        asset_name=parsed["asset_name"],
                        underlying=parsed["underlying"],
                        advisors=parsed["advisors"],
                        isin=parsed["isin"],
                        transaction_date=parsed["transaction_date"],
                        transaction_type=parsed["transaction_type"],
                        quantity=parsed["quantity"],
                        price=parsed["price"],
                        amount=parsed["amount"],
                    )
                )

                rows.append(parsed)

            except TransactionImportError as exc:
                errors.append(
                    {
                        "row": row_number,
                        "message": str(exc),
                    }
                )

        if errors:
            raise TransactionImportError(
                "Transaction import failed: "
                + "; ".join(
                    (
                        f"Row {error['row']}: "
                        f"{error['message']}"
                    )
                    for error in errors
                )
            )

        imported_investments = 0
        imported_mutual_funds = 0
        skipped_duplicates = 0

        seen_source_keys = set()
        seen_mutual_fund_keys = set()
        touched_asset_ids = set()

        for parsed in rows:
            source_key = parsed["source_key"]

            mapped_asset_class = ASSET_CLASS_MAP[
                parsed["asset_class"].upper()
            ]

            if (
                mapped_asset_class
                == AssetCategory.MUTUAL_FUND
            ):
                mapped_type = (
                    MUTUAL_FUND_TRANSACTION_MAP.get(
                        parsed["transaction_type"]
                    )
                )

                if mapped_type is None:
                    raise TransactionImportError(
                        "Unsupported Mutual Fund "
                        "transaction type: "
                        f"{parsed['transaction_type']}"
                    )

                scheme = (
                    TransactionImporter
                    ._get_or_create_mutual_fund_scheme(
                        owner=owner,
                        asset_name=parsed["asset_name"],
                        isin=parsed["isin"],
                    )
                )

                duplicate_key = (
                    parsed["family_name"],
                    parsed["portfolio"],
                    scheme.id,
                    mapped_type,
                    parsed["transaction_date"],
                    parsed["quantity"],
                    parsed["price"],
                    parsed["amount"],
                )

                if duplicate_key in seen_mutual_fund_keys:
                    skipped_duplicates += 1
                    continue

                existing = (
                    TransactionImporter
                    ._find_existing_mutual_fund_transaction(
                        owner=owner,
                        family_name=parsed["family_name"],
                        portfolio=parsed["portfolio"],
                        scheme=scheme,
                        transaction_type=mapped_type,
                        transaction_date=parsed["transaction_date"],
                        quantity=parsed["quantity"],
                        price=parsed["price"],
                        amount=parsed["amount"],
                    )
                )

                if existing is not None:
                    skipped_duplicates += 1
                    seen_mutual_fund_keys.add(
                        duplicate_key
                    )
                    continue

                MutualFundTransaction.objects.create(
                    owner=owner,
                    family_name=parsed["family_name"],
                    portfolio=parsed["portfolio"],
                    scheme=scheme,
                    transaction_type=mapped_type,
                    transaction_date=parsed["transaction_date"],
                    units=parsed["quantity"],
                    nav=parsed["price"],
                    amount=parsed["amount"],
                    fees=Decimal("0"),
                )

                seen_mutual_fund_keys.add(
                    duplicate_key
                )

                imported_mutual_funds += 1
                continue

            if source_key in seen_source_keys:
                skipped_duplicates += 1
                continue

            existing = (
                TransactionImporter
                ._find_existing_investment_transaction(
                    owner=owner,
                    source_key=source_key,
                )
            )

            if existing is not None:
                skipped_duplicates += 1
                seen_source_keys.add(source_key)
                continue

            security_identity_name = (
                TransactionImporter
                .resolve_security_identity_name(
                    asset_name=parsed["asset_name"],
                    underlying=parsed["underlying"],
                )
            )

            asset = TransactionImporter._get_or_create_asset(
                owner=owner,
                asset_name=security_identity_name,
                isin=parsed["isin"],
                asset_class=parsed["asset_class"],
                sub_class=parsed["sub_class"],
            )

            touched_asset_ids.add(asset.id)

            mapped_transaction_type = (
                INVESTMENT_TRANSACTION_MAP.get(
                    parsed["transaction_type"]
                )
            )

            if mapped_transaction_type is None:
                raise TransactionImportError(
                    "Unsupported investment "
                    "transaction type: "
                    f"{parsed['transaction_type']}"
                )

            Transaction.objects.create(
                owner=owner,
                family_name=parsed["family_name"],
                portfolio=parsed["portfolio"],
                asset_class=parsed["asset_class"],
                sub_class=parsed["sub_class"],
                asset_name=parsed["asset_name"],
                underlying=parsed["underlying"],
                advisors=parsed["advisors"],
                asset=asset,
                transaction_type=mapped_transaction_type,
                transaction_date=parsed["transaction_date"],
                quantity=parsed["quantity"],
                price_per_unit=parsed["price"],
                amount=parsed["amount"],
                fees=Decimal("0"),
                source="EXCEL",
                source_key=source_key,
            )

            seen_source_keys.add(source_key)
            imported_investments += 1

        return {
            "imported_investments": imported_investments,
            "imported_mutual_funds": imported_mutual_funds,
            "skipped_duplicates": skipped_duplicates,
            "total_imported": (
                imported_investments
                + imported_mutual_funds
            ),
            # Every investment-side Asset this import created or
            # matched (new or pre-existing) - used to trigger an
            # immediate price refresh right after import instead of
            # waiting for the next scheduled run. See
            # investments.services.auto_price_refresh.
            "touched_asset_ids": list(touched_asset_ids),
        }