import logging
import re
from pathlib import Path

from django.conf import settings

from config.pwms_config import section as get_pwms_config

logger = logging.getLogger(__name__)


class SecurityResolver:
    """
    Resolve Indian securities to Yahoo Finance symbols.

    Resolution priority:
        1. Security Master Excel ISIN mapping
        2. Existing hard-coded ISIN mapping
        3. Explicit Yahoo/exchange symbol
        4. Security Master Excel name mapping
        5. Existing hard-coded name mapping
        6. Generic symbol + exchange suffix

    The Security Master is optional. If it is unavailable, the existing
    resolver behaviour remains available as a fallback.
    """

    CONFIG = get_pwms_config("security_resolver")
    NSE_SUFFIX = CONFIG.get("nse_suffix", ".NS")
    BSE_SUFFIX = CONFIG.get("bse_suffix", ".BO")

    SECURITY_MASTER_FILENAME = "security_master.xlsx"

    ISIN_TO_YAHOO = CONFIG.get("isin_to_yahoo", {})
    NAME_TO_YAHOO = CONFIG.get("name_to_yahoo", {})

    _security_master_loaded = False
    _security_master_isin = {}
    _security_master_name = {}
    _security_master_name_to_isin = {}
    _security_master_isin_to_name = {}
    _security_master_ambiguous_isin = set()
    _security_master_ambiguous_name = set()
    _security_master_ambiguous_name_to_isin = set()
    _security_master_ambiguous_isin_to_name = set()

    @staticmethod
    def clean_symbol(symbol):
        if not symbol:
            return ""

        value = str(symbol).strip().upper()

        value = re.sub(
            r"\s*\(NSE\)\s*$",
            "",
            value,
        )

        value = re.sub(
            r"\s*\(BSE\)\s*$",
            "",
            value,
        )

        value = value.replace(" ", "")

        return value

    @staticmethod
    def clean_isin(isin):
        if not isin:
            return ""

        return str(isin).strip().upper()

    @staticmethod
    def clean_name(name):
        if not name:
            return ""

        value = str(name).strip().upper()

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value

    @staticmethod
    def normalize_security_name(name):
        """Normalize conservative legal-name variants for exact name lookup.

        This intentionally removes only a leading legal/article prefix and
        standardizes LTD/LIMITED. It does not use substring or fuzzy matching;
        collisions are quarantined by the Security Master loader.
        """
        tokens = re.findall(r"[A-Z0-9]+", str(name or "").upper())
        if tokens and tokens[0] == "THE":
            tokens = tokens[1:]
        tokens = ["LIMITED" if token == "LTD" else token for token in tokens]
        # Treat a terminal Ltd/Limited as an optional legal suffix. Keep all
        # other tokens exact; this is normalization, not fuzzy matching.
        if tokens and tokens[-1] == "LIMITED":
            tokens.pop()
        return " ".join(tokens)

    @classmethod
    def _security_master_path(cls):
        """
        Locate security_master.xlsx.

        Preferred location:
            backend/data/security_master.xlsx

        settings.BASE_DIR points to the backend directory in the
        current Django project.
        """

        base_dir = Path(settings.BASE_DIR)

        candidates = [
            base_dir / "data" / cls.SECURITY_MASTER_FILENAME,
            base_dir.parent / "data" / cls.SECURITY_MASTER_FILENAME,
        ]

        for path in candidates:
            if path.exists():
                return path

        return None

    @classmethod
    def _load_security_master(cls):
        """
        Load Security Master Excel once per process.

        If the workbook does not exist yet, it is generated
        automatically from transactions.xlsx before loading.

        The workbook remains optional: if generation or loading
        fails, the resolver continues using its existing hard-coded
        mappings.
        """

        if cls._security_master_loaded:
            return

        cls._security_master_loaded = True

        path = cls._security_master_path()

        if path is None:
            path = cls._generate_security_master()

        if path is None:
            return

        try:
            import openpyxl

            workbook = openpyxl.load_workbook(
                path,
                read_only=True,
                data_only=True,
            )

            worksheet = workbook.active

            if worksheet is None:
                workbook.close()
                return

            headers = next(
                worksheet.iter_rows(
                    min_row=1,
                    max_row=1,
                    values_only=True,
                ),
                (),
            )

            normalized_headers = {}

            for index, header in enumerate(headers):
                if header is None:
                    continue

                normalized_headers[
                    str(header).strip().upper()
                ] = index

            isin_index = normalized_headers.get("ISIN")
            yahoo_index = normalized_headers.get("YAHOO SYMBOL")
            name_index = normalized_headers.get("SECURITY NAME")

            if isin_index is None:
                workbook.close()
                return

            cls._security_master_isin = {}
            cls._security_master_name = {}
            cls._security_master_name_to_isin = {}
            cls._security_master_isin_to_name = {}
            cls._security_master_ambiguous_isin = set()
            cls._security_master_ambiguous_name = set()
            cls._security_master_ambiguous_name_to_isin = set()
            cls._security_master_ambiguous_isin_to_name = set()

            for row in worksheet.iter_rows(
                min_row=2,
                values_only=True,
            ):
                if not row:
                    continue

                isin_value = (
                    row[isin_index]
                    if isin_index < len(row)
                    else None
                )

                yahoo_value = (
                    row[yahoo_index]
                    if yahoo_index is not None
                    and yahoo_index < len(row)
                    else None
                )

                name_value = (
                    row[name_index]
                    if name_index is not None
                    and name_index < len(row)
                    else None
                )

                cleaned_isin = cls.clean_isin(isin_value)
                cleaned_yahoo = cls.clean_symbol(yahoo_value)
                cleaned_name = cls.clean_name(name_value)
                normalized_name = cls.normalize_security_name(cleaned_name)

                if (
                    cleaned_isin
                    and cleaned_yahoo
                ):
                    cls._record_master_mapping(
                        mapping=cls._security_master_isin,
                        ambiguous=cls._security_master_ambiguous_isin,
                        key=cleaned_isin,
                        value=cleaned_yahoo,
                        identifier_type="ISIN",
                    )

                if normalized_name and cleaned_isin:
                    cls._record_master_mapping(
                        mapping=cls._security_master_name_to_isin,
                        ambiguous=cls._security_master_ambiguous_name_to_isin,
                        key=normalized_name,
                        value=cleaned_isin,
                        identifier_type="security name to ISIN",
                    )

                if normalized_name and cleaned_isin:
                    cls._record_master_mapping(
                        mapping=cls._security_master_isin_to_name,
                        ambiguous=cls._security_master_ambiguous_isin_to_name,
                        key=cleaned_isin,
                        value=str(name_value).strip(),
                        identifier_type="ISIN to canonical security name",
                    )

                if (
                    normalized_name
                    and cleaned_yahoo
                ):
                    cls._record_master_mapping(
                        mapping=cls._security_master_name,
                        ambiguous=cls._security_master_ambiguous_name,
                        key=normalized_name,
                        value=cleaned_yahoo,
                        identifier_type="security name",
                    )

            workbook.close()

            logger.info(
                "Loaded %s ISIN mappings and %s name mappings "
                "from %s",
                len(cls._security_master_isin),
                len(cls._security_master_name),
                path,
            )

        except Exception as exc:
            # Security Master must not break the existing price
            # resolution system if the Excel file is unavailable,
            # malformed, or openpyxl is not installed.
            logger.warning(
                "Failed to load %s: %s. Falling back to built-in "
                "mappings.",
                path,
                exc,
            )

            cls._security_master_isin = {}
            cls._security_master_name = {}
            cls._security_master_name_to_isin = {}
            cls._security_master_isin_to_name = {}
            cls._security_master_ambiguous_isin = set()
            cls._security_master_ambiguous_name = set()
            cls._security_master_ambiguous_name_to_isin = set()
            cls._security_master_ambiguous_isin_to_name = set()

    @classmethod
    def _record_master_mapping(cls, *, mapping, ambiguous, key, value, identifier_type):
        """Keep only unambiguous master mappings; never let row order win."""
        if key in ambiguous:
            return
        existing = mapping.get(key)
        if existing is None:
            mapping[key] = value
        elif existing != value:
            mapping.pop(key, None)
            ambiguous.add(key)
            logger.error(
                "Conflicting Security Master %s mapping for %s; mappings quarantined.",
                identifier_type, key,
            )

    @classmethod
    def _generate_security_master(cls):
        """
        Auto-generate security_master.xlsx from transactions.xlsx
        when it is missing on disk.

        Returns the generated file's path, or None if generation
        was not possible (e.g. transactions.xlsx is also missing,
        or a network call to NSE/Yahoo Finance failed).
        """

        try:
            from market_data.services.security_master_generator import (
                SecurityMasterGenerator,
            )

            logger.info(
                "security_master.xlsx not found. Generating it "
                "from transactions.xlsx..."
            )

            result = SecurityMasterGenerator.generate()

            logger.info(
                "Generated %s securities (resolved=%s, "
                "unresolved=%s, non_yahoo=%s).",
                result['total'],
                result['resolved'],
                result['unresolved'],
                result['non_yahoo'],
            )

            return cls._security_master_path()

        except Exception as exc:
            logger.warning(
                "Auto-generation failed: %s. Falling back to "
                "built-in mappings.",
                exc,
            )

            return None

    @classmethod
    def reload_security_master(cls):
        """
        Force the Security Master to be loaded again.

        Useful after security_master.xlsx has been edited while
        the Django process is still running.
        """

        cls._security_master_loaded = False
        cls._security_master_isin = {}
        cls._security_master_name = {}
        cls._security_master_name_to_isin = {}
        cls._security_master_isin_to_name = {}
        cls._security_master_ambiguous_isin = set()
        cls._security_master_ambiguous_name = set()
        cls._security_master_ambiguous_name_to_isin = set()
        cls._security_master_ambiguous_isin_to_name = set()

        cls._load_security_master()

    @classmethod
    def known_isins(cls):
        """
        Return the set of cleaned ISINs currently mapped to a Yahoo
        symbol in the loaded Security Master.

        Used by callers (e.g. update_market_prices) to detect assets
        that have never gone through Security Master resolution, so
        the workbook can be refreshed automatically instead of
        silently leaving those assets unresolved forever.
        """

        cls._load_security_master()

        return set(cls._security_master_isin.keys())

    @classmethod
    def resolve_from_isin(cls, isin):
        """
        Resolve Yahoo symbol directly from ISIN.

        Security Master is checked first, followed by the existing
        hard-coded mapping.
        """

        cleaned_isin = cls.clean_isin(isin)

        if not cleaned_isin:
            return None

        cls._load_security_master()

        if cleaned_isin in cls._security_master_ambiguous_isin:
            logger.error("Cannot resolve ISIN %s because the Security Master has conflicting mappings.", cleaned_isin)
            return None

        yahoo_from_master = cls._security_master_isin.get(
            cleaned_isin
        )

        if yahoo_from_master:
            return yahoo_from_master

        return cls.ISIN_TO_YAHOO.get(
            cleaned_isin
        )

    @classmethod
    def resolve_isin_from_name(cls, name):
        """Return an ISIN only for an exact normalized, unambiguous master name."""
        cls._load_security_master()
        normalized_name = cls.normalize_security_name(name)
        if not normalized_name or normalized_name in cls._security_master_ambiguous_name_to_isin:
            return ""
        return cls._security_master_name_to_isin.get(normalized_name, "")

    @classmethod
    def resolve_security_from_master(cls, *, name=None, isin=None):
        """Return canonical Security Master identity by exact normalized name or ISIN.

        No fuzzy matching is performed. Ambiguous or conflicting master records
        fail closed so a similar-looking security is never silently selected.
        """
        cls._load_security_master()
        cleaned_isin = cls.clean_isin(isin)
        normalized_name = cls.normalize_security_name(name)

        name_isin = ""
        if normalized_name:
            if normalized_name in cls._security_master_ambiguous_name_to_isin:
                return {"status": "AMBIGUOUS", "canonical_name": "", "isin": "", "yahoo_symbol": ""}
            name_isin = cls._security_master_name_to_isin.get(normalized_name, "")

        if cleaned_isin:
            if cleaned_isin in cls._security_master_ambiguous_isin_to_name:
                return {"status": "AMBIGUOUS", "canonical_name": "", "isin": "", "yahoo_symbol": ""}
            canonical_name = cls._security_master_isin_to_name.get(cleaned_isin, "")
            if not canonical_name:
                if name_isin and name_isin != cleaned_isin:
                    return {"status": "CONFLICT", "canonical_name": "", "isin": cleaned_isin, "yahoo_symbol": cls.resolve_from_isin(cleaned_isin) or ""}
                return {"status": "NOT_FOUND", "canonical_name": "", "isin": "", "yahoo_symbol": ""}
            if name_isin and name_isin != cleaned_isin:
                return {"status": "CONFLICT", "canonical_name": canonical_name, "isin": cleaned_isin, "yahoo_symbol": cls.resolve_from_isin(cleaned_isin) or ""}
            return {"status": "MATCHED", "canonical_name": canonical_name, "isin": cleaned_isin, "yahoo_symbol": cls.resolve_from_isin(cleaned_isin) or ""}

        if normalized_name and name_isin:
            if name_isin in cls._security_master_ambiguous_isin_to_name:
                return {"status": "AMBIGUOUS", "canonical_name": "", "isin": "", "yahoo_symbol": ""}
            canonical_name = cls._security_master_isin_to_name.get(name_isin, "")
            if not canonical_name:
                return {"status": "NOT_FOUND", "canonical_name": "", "isin": "", "yahoo_symbol": ""}
            return {"status": "MATCHED", "canonical_name": canonical_name, "isin": name_isin, "yahoo_symbol": cls.resolve_from_isin(name_isin) or ""}

        return {"status": "NOT_FOUND", "canonical_name": "", "isin": "", "yahoo_symbol": ""}

    @classmethod
    def resolve_from_name(cls, name):
        """
        Resolve Yahoo symbol from a known asset name.

        Security Master is checked first, followed by the existing
        hard-coded mapping.
        """

        cleaned_name = cls.clean_name(name)
        normalized_name = cls.normalize_security_name(cleaned_name)

        if not normalized_name:
            return None

        cls._load_security_master()

        if normalized_name in cls._security_master_ambiguous_name:
            logger.warning("Ignoring ambiguous Security Master name mapping for %s.", cleaned_name)
            return None

        yahoo_from_master = cls._security_master_name.get(
            normalized_name
        )

        if yahoo_from_master:
            return yahoo_from_master

        return (
            cls.NAME_TO_YAHOO.get(cleaned_name)
            or cls.NAME_TO_YAHOO.get(normalized_name)
        )

    @classmethod
    def resolve_yahoo_symbol(
        cls,
        symbol=None,
        exchange="NSE",
        isin=None,
        name=None,
    ):
        """
        Resolve the Yahoo Finance symbol.

        Priority:

            1. ISIN through Security Master
            2. Existing ISIN mapping
            3. Explicit symbol
            4. Security Master name mapping
            5. Existing name mapping
            6. Generic symbol + exchange suffix
        """

        cleaned_isin = cls.clean_isin(isin)
        cleaned_symbol = cls.clean_symbol(symbol)
        cleaned_name = cls.clean_name(name)

        yahoo_from_isin = cls.resolve_from_isin(cleaned_isin)
        if cleaned_isin in cls._security_master_ambiguous_isin:
            raise ValueError(
                f"Cannot resolve ISIN {cleaned_isin}: conflicting Security Master mappings."
            )

        if yahoo_from_isin:
            explicit_symbol = cleaned_symbol
            if explicit_symbol and not explicit_symbol.endswith(
                (cls.NSE_SUFFIX, cls.BSE_SUFFIX)
            ):
                requested_exchange = (exchange or "NSE").strip().upper()
                suffix = cls.BSE_SUFFIX if requested_exchange == "BSE" else cls.NSE_SUFFIX
                explicit_symbol = f"{explicit_symbol}{suffix}"
            if explicit_symbol and explicit_symbol != yahoo_from_isin:
                raise ValueError(
                    f"Conflicting security identifiers: ISIN {cleaned_isin} "
                    f"maps to {yahoo_from_isin}, but the supplied symbol/exchange "
                    f"resolves to {explicit_symbol}."
                )
            name_symbol = cls.resolve_from_name(cleaned_name)
            if name_symbol and name_symbol != yahoo_from_isin:
                raise ValueError(
                    f"Conflicting security identifiers: ISIN {cleaned_isin} "
                    f"maps to {yahoo_from_isin}, but name mapping points to {name_symbol}."
                )
            return yahoo_from_isin

        if cleaned_symbol:

            if cleaned_symbol.endswith(
                cls.NSE_SUFFIX
            ):
                return cleaned_symbol

            if cleaned_symbol.endswith(
                cls.BSE_SUFFIX
            ):
                return cleaned_symbol

        yahoo_from_name = cls.resolve_from_name(
            name
        )

        if yahoo_from_name:
            return yahoo_from_name

        if not cleaned_symbol:
            raise ValueError(
                "Unable to resolve security symbol. "
                "ISIN, symbol and asset name are all missing "
                "or unmapped."
            )

        exchange = (
            exchange or "NSE"
        ).strip().upper()

        if exchange == "BSE":
            return (
                f"{cleaned_symbol}"
                f"{cls.BSE_SUFFIX}"
            )

        return (
            f"{cleaned_symbol}"
            f"{cls.NSE_SUFFIX}"
        )

    @classmethod
    def candidate_symbols(
        cls,
        symbol=None,
        exchange=None,
        isin=None,
        name=None,
    ):
        """
        Return possible Yahoo symbols.

        Security Master and ISIN mappings are preferred.
        """

        candidates = []

        cleaned_isin = cls.clean_isin(isin)
        yahoo_from_isin = cls.resolve_from_isin(cleaned_isin)
        if cleaned_isin in cls._security_master_ambiguous_isin:
            raise ValueError(
                f"Cannot generate candidates for ISIN {cleaned_isin}: "
                "conflicting Security Master mappings."
            )

        if yahoo_from_isin:
            candidates.append(
                yahoo_from_isin
            )

        yahoo_from_name = cls.resolve_from_name(
            name
        )

        if (
            yahoo_from_name
            and yahoo_from_name not in candidates
        ):
            candidates.append(
                yahoo_from_name
            )

        cleaned = cls.clean_symbol(
            symbol
        )

        if cleaned:

            if cleaned.endswith(
                cls.NSE_SUFFIX
            ):

                if cleaned not in candidates:
                    candidates.append(cleaned)

            elif cleaned.endswith(
                cls.BSE_SUFFIX
            ):

                if cleaned not in candidates:
                    candidates.append(cleaned)

            else:

                exchange = (
                    exchange or "NSE"
                ).strip().upper()

                if exchange == "BSE":
                    candidate = (
                        f"{cleaned}"
                        f"{cls.BSE_SUFFIX}"
                    )
                else:
                    candidate = (
                        f"{cleaned}"
                        f"{cls.NSE_SUFFIX}"
                    )

                if candidate not in candidates:
                    candidates.append(candidate)

        return candidates