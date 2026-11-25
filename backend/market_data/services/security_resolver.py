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

        Equivalences: ``&`` = ``AND``, ``LTD`` = ``LIMITED``, ``(I)`` = ``INDIA``;
        punctuation, spacing and case are ignored, and a leading ``THE`` and a
        trailing ``LIMITED`` are optional. This is normalization, not fuzzy
        matching: collisions are quarantined by the Security Master loader.
        """
        text = str(name or "").upper().replace("&", " AND ")
        text = re.sub(r"\(\s*I\s*\)", " INDIA ", text)
        tokens = re.findall(r"[A-Z0-9]+", text)
        if tokens and tokens[0] == "THE":
            tokens = tokens[1:]
        tokens = ["LIMITED" if token == "LTD" else token for token in tokens]
        if tokens and tokens[-1] == "LIMITED":
            tokens.pop()
        return " ".join(tokens)

    # ------------------------------------------------------------------
    # Workbook loading: once per process, again only when the file's
    # modification time changes. Every read and write of the workbook holds
    # WORKBOOK_LOCK (see security_master_generator).
    # ------------------------------------------------------------------

    _loaded_path = None
    _loaded_mtime = None
    _load_conflicts = []
    _pending_writes = {}
    _pending_retry_at = 0.0
    _write_warned = False
    _read_warned = False
    _unresolved = {}
    _nse_cache = None
    _nse_cache_expires = 0.0
    # Names already warned about as ambiguous; each is warned once per process
    # (the full list is in reports/security_master_report.csv).
    _ambiguous_name_warned = set()

    @classmethod
    def _security_master_path(cls):
        """Locate security_master.xlsx (backend/data by default).

        ``settings.PWMS_DATA_DIR`` replaces backend/data when set. The
        repository-root ``data/`` folder is still accepted as a legacy
        location when no data directory is configured.
        """
        from config.scheduling.files import data_dir

        candidates = [data_dir() / cls.SECURITY_MASTER_FILENAME]
        if not getattr(settings, "PWMS_DATA_DIR", None):
            candidates.append(Path(settings.BASE_DIR).parent / "data" / cls.SECURITY_MASTER_FILENAME)
        for path in candidates:
            if path.exists():
                return path
        return None

    @classmethod
    def _empty_maps(cls):
        cls._security_master_isin = {}
        cls._security_master_name = {}
        cls._security_master_name_to_isin = {}
        cls._security_master_isin_to_name = {}
        cls._security_master_ambiguous_isin = set()
        cls._security_master_ambiguous_name = set()
        cls._security_master_ambiguous_name_to_isin = set()
        cls._security_master_ambiguous_isin_to_name = set()

    @classmethod
    def reset_cache(cls):
        """Forget everything loaded or pending (tests and diagnostics)."""
        cls._empty_maps()
        cls._security_master_loaded = False
        cls._loaded_path = None
        cls._loaded_mtime = None
        cls._load_conflicts = []
        cls._pending_writes = {}
        cls._pending_retry_at = 0.0
        cls._write_warned = False
        cls._read_warned = False
        cls._ambiguous_name_warned = set()
        cls._unresolved = {}
        cls._nse_cache = None
        cls._nse_cache_expires = 0.0

    @staticmethod
    def _mtime(path):
        try:
            return path.stat().st_mtime_ns if path is not None else None
        except OSError:
            return None

    @classmethod
    def _read_workbook(cls, path):
        """Return [(isin, yahoo_symbol, security_name), ...] from the workbook."""
        import openpyxl

        from market_data.services.security_master_generator import WORKBOOK_LOCK

        with WORKBOOK_LOCK:
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                worksheet = (
                    workbook["Security Master"] if "Security Master" in workbook.sheetnames else workbook.active
                )
                if worksheet is None:
                    return []
                rows = worksheet.iter_rows(values_only=True)
                headers = next(rows, ())
                indexes = {str(header).strip().upper(): index for index, header in enumerate(headers) if header is not None}
                isin_index = indexes.get("ISIN")
                yahoo_index = indexes.get("YAHOO SYMBOL")
                name_index = indexes.get("SECURITY NAME")
                if isin_index is None:
                    return []

                def cell(row, index):
                    return row[index] if index is not None and index < len(row) else None

                return [
                    (cell(row, isin_index), cell(row, yahoo_index), cell(row, name_index))
                    for row in rows if row
                ]
            finally:
                workbook.close()

    _MAP_NAMES = (
        "_security_master_isin", "_security_master_name", "_security_master_name_to_isin",
        "_security_master_isin_to_name",
    )
    _SET_NAMES = (
        "_security_master_ambiguous_isin", "_security_master_ambiguous_name",
        "_security_master_ambiguous_name_to_isin", "_security_master_ambiguous_isin_to_name",
    )

    @classmethod
    def _index_rows(cls, rows):
        """Build the lookup maps aside, then swap them in.

        Another thread (the price worker, a nightly job) may be reading the
        maps during a reload; it sees either the old or the new mappings,
        never a half-built set.
        """
        fresh = {name: {} for name in cls._MAP_NAMES}
        fresh.update({name: set() for name in cls._SET_NAMES})
        cls._load_conflicts = []
        for isin_value, yahoo_value, name_value in rows:
            cls._index_mapping(isin_value, yahoo_value, name_value, target=fresh)
        for name, value in fresh.items():
            setattr(cls, name, value)

    @classmethod
    def _index_mapping(cls, isin_value, yahoo_value, name_value, *, target=None):
        maps = target if target is not None else {
            name: getattr(cls, name) for name in cls._MAP_NAMES + cls._SET_NAMES
        }
        cleaned_isin = cls.clean_isin(isin_value)
        cleaned_yahoo = cls.clean_symbol(yahoo_value)
        normalized_name = cls.normalize_security_name(cls.clean_name(name_value))
        if cleaned_isin and cleaned_yahoo:
            cls._record_master_mapping(
                mapping=maps["_security_master_isin"], ambiguous=maps["_security_master_ambiguous_isin"],
                key=cleaned_isin, value=cleaned_yahoo, identifier_type="ISIN",
            )
        if normalized_name and cleaned_isin:
            cls._record_master_mapping(
                mapping=maps["_security_master_name_to_isin"],
                ambiguous=maps["_security_master_ambiguous_name_to_isin"],
                key=normalized_name, value=cleaned_isin, identifier_type="security name to ISIN",
            )
            cls._record_master_mapping(
                mapping=maps["_security_master_isin_to_name"],
                ambiguous=maps["_security_master_ambiguous_isin_to_name"],
                key=cleaned_isin, value=str(name_value).strip(), identifier_type="ISIN to canonical security name",
            )
        if normalized_name and cleaned_yahoo:
            cls._record_master_mapping(
                mapping=maps["_security_master_name"], ambiguous=maps["_security_master_ambiguous_name"],
                key=normalized_name, value=cleaned_yahoo, identifier_type="security name",
            )

    @classmethod
    def _load_security_master(cls, *, generate_if_missing=False, write_report=True):
        """Load the workbook once; reload only if its modification time changed.

        A missing workbook is no longer generated here (that blocked price
        lookups on NSE/Yahoo downloads): the nightly ``security_master`` job
        builds it, and resolve-and-save adds rows as symbols are resolved.
        ``generate_if_missing=True`` keeps the old behaviour for explicit
        callers.
        """
        path = cls._security_master_path()
        if path is None and generate_if_missing and not cls._security_master_loaded:
            path = cls._generate_security_master()
        mtime = cls._mtime(path)
        if cls._security_master_loaded and path == cls._loaded_path and mtime == cls._loaded_mtime:
            return
        first_load = not cls._security_master_loaded
        cls._security_master_loaded = True
        cls._loaded_path = path
        cls._loaded_mtime = mtime
        if path is None:
            cls._empty_maps()
            cls._apply_pending_to_memory()
            return
        try:
            rows = cls._read_workbook(path)
        except Exception as exc:  # noqa: BLE001 - never break price resolution
            # Keep the previous mappings (if any); a file open in Excel or
            # mid-save is retried when its modification time changes again.
            if first_load:
                cls._empty_maps()
            if not cls._read_warned:
                logger.warning("Could not read %s (%s); keeping the mappings already loaded.", path, exc)
                cls._read_warned = True
            cls._apply_pending_to_memory()
            return
        cls._read_warned = False
        cls._index_rows(rows)
        cls._apply_pending_to_memory()
        cls._log_load_summary(path, write_report=write_report)

    @classmethod
    def _log_load_summary(cls, path, *, write_report=True):
        sample_size = int(cls.CONFIG.get("conflict_log_sample_size", 10))
        conflicts = cls._load_conflicts
        message = (
            f"Security Master loaded from {path}: {len(cls._security_master_isin)} ISIN and "
            f"{len(cls._security_master_name)} name mappings"
        )
        if conflicts:
            keys = []
            for _, key in conflicts:
                if key not in keys:
                    keys.append(key)
            isin_keys = {key for kind, key in conflicts if kind == "ISIN"}
            sample = ", ".join(keys[:sample_size])
            message += (
                f"; conflicting rows quarantined for {len(isin_keys)} ISIN(s) and "
                f"{len(keys) - len(isin_keys)} name(s) (first {min(sample_size, len(keys))}: {sample}); "
                "full list in reports/security_master_report.csv"
            )
        logger.info(message)
        if write_report:
            cls._write_report()

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
            conflicts = cls.__dict__.get("_load_conflicts")
            if conflicts is None:
                cls._load_conflicts = conflicts = []
            conflicts.append((identifier_type, key))
            logger.debug(
                "Conflicting Security Master %s mapping for %s (%s vs %s); mappings quarantined.",
                identifier_type, key, existing, value,
            )

    @classmethod
    def _write_report(cls):
        """Write data/reports/security_master_report.csv (conflicts + needs ISIN)."""
        import csv
        import io

        from config.scheduling.files import atomic_write_text, reports_dir

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["type", "identifier", "key", "name", "isin", "reason"])
        writer.writeheader()
        seen = set()
        for identifier_type, key in cls._load_conflicts:
            if (identifier_type, key) in seen:
                continue
            seen.add((identifier_type, key))
            writer.writerow({
                "type": "conflict", "identifier": identifier_type.split(" ")[0] if identifier_type == "ISIN" else identifier_type,
                "key": key, "name": "", "isin": "", "reason": "rows disagree; mapping ignored until the workbook is fixed",
            })
        for name, item in sorted(cls._unresolved.items()):
            writer.writerow({
                "type": item["type"], "identifier": "", "key": "", "name": name, "isin": item["isin"],
                "reason": item["reason"],
            })
        if not seen and not cls._unresolved:
            return
        try:
            atomic_write_text(reports_dir() / "security_master_report.csv", buffer.getvalue())
        except OSError as exc:
            logger.debug("Could not write the Security Master report: %s", exc)

    @classmethod
    def _generate_security_master(cls):
        """
        Generate security_master.xlsx from the database when it is missing.

        Only used when a caller explicitly asks for it
        (generate_if_missing=True); the nightly security_master job is the
        normal way the workbook is built.
        """

        try:
            from market_data.services.security_master_generator import (
                SecurityMasterGenerator,
            )

            logger.info("security_master.xlsx not found. Generating it from the database...")
            result = SecurityMasterGenerator.generate()
            logger.info(
                "Generated %s securities (resolved=%s, unresolved=%s, non_yahoo=%s).",
                result["total"], result["resolved"], result["unresolved"], result["non_yahoo"],
            )
            return cls._security_master_path()

        except Exception as exc:
            logger.warning("Auto-generation failed: %s. Falling back to built-in mappings.", exc)
            return None

    @classmethod
    def reload_security_master(cls, *, generate_if_missing=False, write_report=True):
        """Force the Security Master to be loaded again.

        Read-only callers (diagnostics) pass write_report=False so nothing
        is written to data/reports.
        """
        cls._security_master_loaded = False
        cls._loaded_mtime = None
        cls._empty_maps()
        cls._load_security_master(generate_if_missing=generate_if_missing, write_report=write_report)

    @classmethod
    def reload_if_changed(cls):
        """Reload the workbook only if it changed on disk since the last load."""
        cls._load_security_master()

    # ------------------------------------------------------------------
    # Excel-first lookup and resolve-and-save
    # ------------------------------------------------------------------

    @classmethod
    def lookup_workbook(cls, *, isin=None, name=None):
        """Yahoo symbol from the workbook: by ISIN, then by exact normalized name.

        A name hit counts only when that workbook row has no ISIN or the
        same ISIN as the asset. Returns None on a miss; raises ValueError if
        the ISIN has conflicting rows.
        """
        cls._load_security_master()
        cleaned_isin = cls.clean_isin(isin)
        if cleaned_isin:
            if cleaned_isin in cls._security_master_ambiguous_isin:
                raise ValueError(f"Cannot resolve ISIN {cleaned_isin}: conflicting Security Master mappings.")
            symbol = cls._security_master_isin.get(cleaned_isin)
            if symbol:
                return symbol
        normalized_name = cls.normalize_security_name(cls.clean_name(name))
        if normalized_name and normalized_name not in cls._security_master_ambiguous_name:
            symbol = cls._security_master_name.get(normalized_name)
            row_isin = cls._security_master_name_to_isin.get(normalized_name, "")
            if symbol and (not cleaned_isin or not row_isin or row_isin == cleaned_isin):
                return symbol
        return None

    @classmethod
    def nse_equity_list(cls):
        """ISIN -> {symbol, name} from NSE EQUITY_L.csv / ETF list, cached.

        Successful downloads are cached for 24 hours, failures for 1 hour, so
        a blocked NSE site costs one timeout per hour, not one per asset.
        """
        import time as _time

        if cls._nse_cache is not None and _time.monotonic() < cls._nse_cache_expires:
            return cls._nse_cache
        from market_data.services.security_master_generator import SecurityMasterGenerator

        try:
            data = SecurityMasterGenerator._load_nse_master()
        except Exception as exc:  # noqa: BLE001
            logger.debug("NSE equity list download failed: %s", exc)
            data = {}
        cls._nse_cache = data
        cls._nse_cache_expires = _time.monotonic() + (86400 if data else 3600)
        return data

    @classmethod
    def _search_yahoo(cls, name):
        """Raw Yahoo Finance search quotes for ``name``."""
        import yfinance as yf

        return list(getattr(yf.Search(name, max_results=10), "quotes", []) or [])

    @classmethod
    def search_yahoo_exact_name(cls, name):
        """Accept a Yahoo search result only when its normalized name equals ``name``."""
        target = cls.normalize_security_name(name)
        if not target:
            return None
        try:
            quotes = cls._search_yahoo(name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Yahoo search failed for %s: %s", name, exc)
            return None
        matches = []
        for quote in quotes:
            symbol = cls.clean_symbol(quote.get("symbol"))
            if not symbol.endswith((cls.NSE_SUFFIX, cls.BSE_SUFFIX)):
                continue
            quote_names = {quote.get("longname"), quote.get("shortname"), quote.get("name")}
            if any(cls.normalize_security_name(value) == target for value in quote_names if value):
                matches.append(symbol)
        # Prefer NSE over BSE; never pick between two different NSE symbols.
        nse = sorted({symbol for symbol in matches if symbol.endswith(cls.NSE_SUFFIX)})
        if len(nse) == 1:
            return nse[0]
        bse = sorted({symbol for symbol in matches if symbol.endswith(cls.BSE_SUFFIX)})
        if not nse and len(bse) == 1:
            return bse[0]
        return None

    @classmethod
    def resolve_for_asset(cls, *, symbol=None, isin=None, name=None):
        """Return ``(yahoo_symbol, from_workbook)``.

        1. security_master.xlsx (ISIN, then exact normalized name) - a hit is
           used as is and nothing is written.
        2. Otherwise: hard-coded maps / explicit symbol, then the NSE symbol
           for the ISIN (or exact company name) from EQUITY_L.csv, then a
           Yahoo search accepted only on an exact normalized name match.
        Raises ValueError when nothing resolves; the asset is then listed in
        the report as "needs ISIN" (no ISIN) or "unresolved". Never guesses.
        """
        workbook_symbol = cls.lookup_workbook(isin=isin, name=name)
        if workbook_symbol:
            # Same consistency checks as before (explicit symbol vs ISIN).
            return cls.resolve_yahoo_symbol(symbol=symbol, isin=isin, name=name), True

        try:
            return cls.resolve_yahoo_symbol(symbol=symbol, isin=isin, name=name), False
        except ValueError as exc:
            if "conflict" in str(exc).lower():
                raise

        cleaned_isin = cls.clean_isin(isin)
        nse = cls.nse_equity_list()
        if cleaned_isin and nse.get(cleaned_isin, {}).get("symbol"):
            return f"{nse[cleaned_isin]['symbol']}{cls.NSE_SUFFIX}", False
        if not cleaned_isin and name:
            target = cls.normalize_security_name(name)
            by_name = {
                record.get("symbol") for record in nse.values()
                if record.get("symbol") and cls.normalize_security_name(record.get("name")) == target
            }
            if len(by_name) == 1:
                return f"{next(iter(by_name))}{cls.NSE_SUFFIX}", False

        searched = cls.search_yahoo_exact_name(name)
        if searched:
            return searched, False

        cls.record_unresolved(name=name, isin=cleaned_isin)
        raise ValueError(
            f"Unable to resolve a Yahoo Finance symbol for {name or symbol or cleaned_isin}"
            + ("" if cleaned_isin else " (no ISIN; add it to the asset or the Security Master)")
        )

    @classmethod
    def record_unresolved(cls, *, name, isin):
        key = str(name or isin or "").strip()
        if not key or key in cls._unresolved:
            return
        cls._unresolved[key] = {
            "type": "unresolved" if isin else "needs_isin",
            "isin": isin or "",
            "reason": "no exact ISIN/name match in the Security Master, NSE list or Yahoo search",
        }
        cls._write_report()

    @classmethod
    def remember(cls, *, isin, name, symbol):
        """Save a newly resolved mapping: memory now, workbook now or later.

        The in-memory cache is updated directly (no reload). If the workbook
        is locked (open in Excel), the write is queued and retried; one
        warning is logged, not one per asset.
        """
        cleaned_symbol = cls.clean_symbol(symbol)
        if not cleaned_symbol:
            return False
        cls._load_security_master()
        key = cls.clean_isin(isin) or f"NAME:{cls.normalize_security_name(name)}"
        cls._pending_writes[key] = (cls.clean_isin(isin), str(name or "").strip(), cleaned_symbol)
        cls._apply_pending_to_memory()
        cls._unresolved.pop(str(name or "").strip(), None)
        return cls.flush_pending_writes(force=True) > 0

    @classmethod
    def _apply_pending_to_memory(cls):
        for isin, name, symbol in list(cls._pending_writes.values()):
            normalized = cls.normalize_security_name(name)
            if isin and isin not in cls._security_master_ambiguous_isin:
                cls._security_master_isin.setdefault(isin, symbol)
                if normalized:
                    cls._security_master_isin_to_name.setdefault(isin, name)
                    cls._security_master_name_to_isin.setdefault(normalized, isin)
            if normalized and normalized not in cls._security_master_ambiguous_name:
                cls._security_master_name.setdefault(normalized, symbol)

    @classmethod
    def pending_write_count(cls):
        return len(cls._pending_writes)

    @classmethod
    def flush_pending_writes(cls, *, force=False):
        """Write queued mappings to the workbook. Returns how many were written."""
        import time as _time

        from market_data.services.security_master_generator import SecurityMasterGenerator

        if not cls._pending_writes:
            return 0
        if not force and _time.monotonic() < cls._pending_retry_at:
            return 0
        written = 0
        for key, (isin, name, symbol) in list(cls._pending_writes.items()):
            try:
                SecurityMasterGenerator.persist_resolved_symbol(isin=isin, security_name=name, yahoo_symbol=symbol)
            except OSError as exc:
                cls._pending_retry_at = _time.monotonic() + int(cls.CONFIG.get("queued_write_retry_seconds", 300))
                if not cls._write_warned:
                    logger.warning(
                        "security_master.xlsx is locked (%s); %s new mapping(s) kept in memory and queued. "
                        "They are written automatically once the file is closed.",
                        exc, len(cls._pending_writes),
                    )
                    cls._write_warned = True
                break
            except ValueError as exc:
                # The workbook already holds a different mapping: the
                # workbook wins; drop the queued value.
                logger.warning("Not saving %s for %s: %s", symbol, name or isin, exc)
                cls._pending_writes.pop(key, None)
                continue
            cls._pending_writes.pop(key, None)
            written += 1
        if not cls._pending_writes:
            cls._write_warned = False
        # Our own write changed the file: record its new mtime so it is not
        # re-read.
        cls._loaded_path = cls._security_master_path()
        cls._loaded_mtime = cls._mtime(cls._loaded_path)
        return written

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
            if normalized_name in cls._ambiguous_name_warned:
                logger.debug("Ignoring ambiguous Security Master name mapping for %s.", cleaned_name)
            else:
                cls._ambiguous_name_warned.add(normalized_name)
                logger.warning(
                    "Ignoring ambiguous Security Master name mapping for %s (several rows, different symbols); "
                    "set the asset's ISIN or fix the rows in security_master.xlsx. Not repeated this run.",
                    cleaned_name,
                )
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