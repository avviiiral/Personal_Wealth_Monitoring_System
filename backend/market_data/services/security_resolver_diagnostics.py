"""Read-only diagnostics for SecurityResolver Yahoo symbol resolution.

This module explains how each Yahoo-routed asset would resolve, using the
same call the market refresh makes::

    SecurityResolver.resolve_yahoo_symbol(
        symbol=asset.symbol,
        isin=asset.isin,
        name=asset.name,
    )

It never writes: it does not call MarketDataManager.resolve_asset_symbol()
(which persists to the workbook and Asset.symbol), never triggers Security
Master auto-generation, opens the workbook with openpyxl read_only=True and
does not contact Yahoo Finance or NSE.
"""

from dataclasses import asdict, dataclass

from django.db.models import Q

from investments.models import Asset
from market_data.services.market_price_scope import market_price_assets
from market_data.services.security_resolver import SecurityResolver

STATUS_MATCHED = "MATCHED"
STATUS_NOT_IN_MASTER = "NOT_IN_MASTER"
STATUS_MISSING_ISIN = "MISSING_ISIN"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_CONFLICT = "CONFLICT"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_ERROR = "ERROR"

STATUS_ORDER = (
    STATUS_MATCHED,
    STATUS_NOT_IN_MASTER,
    STATUS_MISSING_ISIN,
    STATUS_AMBIGUOUS,
    STATUS_CONFLICT,
    STATUS_UNRESOLVED,
    STATUS_ERROR,
)

# Messages raised by SecurityResolver.resolve_yahoo_symbol(). Tests pin each
# one so a wording change cannot silently misclassify a failure.
_AMBIGUOUS_MESSAGE = "conflicting Security Master mappings"
_CONFLICT_MESSAGE = "Conflicting security identifiers"
_UNRESOLVED_MESSAGE = "Unable to resolve security symbol"


@dataclass(frozen=True)
class ResolverDiagnosticRow:
    asset_id: int
    asset_name: str
    isin: str
    database_symbol: str
    security_master_symbol: str
    resolved_symbol: str
    status: str
    reason: str

    def as_dict(self):
        return asdict(self)


def read_security_master_isin_symbols(path):
    """Return {ISIN: [Yahoo Symbol of every row]} from the workbook.

    Reads the same sheet as SecurityResolver ("Security Master", else the
    active sheet), so duplicate and conflicting rows can be reported. The
    file is opened read-only.
    """
    if path is None:
        return {}

    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = (
            workbook["Security Master"] if "Security Master" in workbook.sheetnames else workbook.active
        )
        if worksheet is None:
            return {}
        headers = next(
            worksheet.iter_rows(min_row=1, max_row=1, values_only=True),
            (),
        )
        indexes = {
            str(header).strip().upper(): index
            for index, header in enumerate(headers)
            if header is not None
        }
        isin_index = indexes.get("ISIN")
        yahoo_index = indexes.get("YAHOO SYMBOL")
        if isin_index is None:
            return {}

        rows = {}
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            if not row or isin_index >= len(row):
                continue
            isin = SecurityResolver.clean_isin(row[isin_index])
            if not isin:
                continue
            symbol = ""
            if yahoo_index is not None and yahoo_index < len(row):
                symbol = SecurityResolver.clean_symbol(row[yahoo_index])
            rows.setdefault(isin, []).append(symbol)
        return rows
    finally:
        workbook.close()


def diagnostic_assets(*, user_id=None, asset_ids=None, isins=None):
    """Return the assets to diagnose, ordered by id.

    Default scope is the Yahoo-routed part of the market refresh universe:
    STOCK, ETF and legacy Direct Equity assets. Bonds and SGBs are excluded
    because they are priced by their own services, not by Yahoo. Explicit
    asset ids or ISINs bypass the scope so any asset can be inspected; an
    asset matching either selector is included.
    """
    if asset_ids or isins:
        selector = Q(pk__in=asset_ids or [])
        for value in isins or []:
            cleaned = SecurityResolver.clean_isin(value)
            if cleaned:
                selector |= Q(isin__iexact=cleaned)
        queryset = Asset.objects.filter(selector)
    else:
        queryset = (
            market_price_assets(include_mutual_funds=False)
            .exclude(category="BOND")
            .exclude(name__icontains="SOVEREIGN GOLD BOND")
        )

    if user_id:
        queryset = queryset.filter(owner_id=user_id)

    return queryset.order_by("id")


SECURITY_MASTER_NAME_SOURCE = "Security Master exact-name row"


def _from_security_master(asset, resolved_symbol):
    """True when the workbook (ISIN or exact-name row) supplied the symbol."""
    try:
        return SecurityResolver.lookup_workbook(isin=asset.isin, name=asset.name) == resolved_symbol
    except ValueError:
        return False


def _fallback_source(asset, resolved_symbol):
    """Describe which rule produced the symbol when the ISIN has no workbook row."""
    isin = SecurityResolver.clean_isin(asset.isin)
    explicit = SecurityResolver.clean_symbol(asset.symbol)

    if _from_security_master(asset, resolved_symbol):
        return SECURITY_MASTER_NAME_SOURCE
    if isin and SecurityResolver.ISIN_TO_YAHOO.get(isin) == resolved_symbol:
        return "built-in ISIN mapping"
    if explicit == resolved_symbol and explicit.endswith(
        (SecurityResolver.NSE_SUFFIX, SecurityResolver.BSE_SUFFIX)
    ):
        return "explicit Asset.symbol"
    if SecurityResolver.resolve_from_name(asset.name) == resolved_symbol:
        return "name mapping"
    return "Asset.symbol + exchange suffix (not provider-validated)"


def diagnose_asset(asset, master_symbols_by_isin):
    """Diagnose one asset without modifying anything."""
    isin = SecurityResolver.clean_isin(asset.isin)
    database_symbol = (asset.symbol or "").strip()
    master_symbols = master_symbols_by_isin.get(isin, []) if isin else []
    distinct_master = sorted({symbol for symbol in master_symbols if symbol})
    master_display = " | ".join(distinct_master)

    def row(status, resolved_symbol, reason):
        return ResolverDiagnosticRow(
            asset_id=asset.pk,
            asset_name=asset.name or "",
            isin=isin,
            database_symbol=database_symbol,
            security_master_symbol=master_display,
            resolved_symbol=resolved_symbol or "",
            status=status,
            reason=reason,
        )

    try:
        resolved = SecurityResolver.resolve_yahoo_symbol(
            symbol=asset.symbol,
            isin=asset.isin,
            name=asset.name,
        )
    except ValueError as exc:
        message = str(exc)
        if _AMBIGUOUS_MESSAGE in message:
            return row(
                STATUS_AMBIGUOUS,
                "",
                f"{message} {len(master_symbols)} rows carry "
                f"{len(distinct_master)} different symbols; resolution "
                "fails closed until the workbook is corrected.",
            )
        if message.startswith(_CONFLICT_MESSAGE):
            return row(STATUS_CONFLICT, "", message)
        if message.startswith(_UNRESOLVED_MESSAGE):
            prefix = "Asset has no ISIN. " if not isin else ""
            return row(STATUS_UNRESOLVED, "", prefix + message)
        return row(STATUS_ERROR, "", message)
    except Exception as exc:  # report, never abort the whole diagnostic
        return row(STATUS_ERROR, "", f"{type(exc).__name__}: {exc}")

    resolved = SecurityResolver.clean_symbol(resolved)
    from_master = _from_security_master(asset, resolved)
    notes = []
    if not database_symbol:
        notes.append(f"Asset.symbol is blank; a refresh would set it to {resolved}.")
    elif from_master and SecurityResolver.with_exchange_suffix(database_symbol) != resolved:
        # The workbook always wins; a different Asset.symbol is stale, not a
        # conflict. MarketDataManager.resolve_asset_symbol() corrects it.
        notes.append(
            f"Security Master symbol {resolved} overrides Asset.symbol {database_symbol}; "
            f"a refresh would update Asset.symbol to {resolved}."
        )

    if not isin:
        source = _fallback_source(asset, resolved)
        notes.insert(
            0,
            f"Resolved via {source}. No ISIN, so nothing is written "
            "to the Security Master.",
        )
        return row(STATUS_MISSING_ISIN, resolved, " ".join(notes))

    if not distinct_master:
        source = _fallback_source(asset, resolved)
        detail = (
            "Security Master row has no Yahoo symbol"
            if master_symbols
            else "ISIN not in Security Master"
        )
        outcome = (
            "It is used as is; nothing is written to the Security Master."
            if source == SECURITY_MASTER_NAME_SOURCE
            else "A refresh would add this ISIN mapping to the Security Master."
        )
        notes.insert(0, f"{detail}; resolved via {source}. {outcome}")
        return row(STATUS_NOT_IN_MASTER, resolved, " ".join(notes))

    if distinct_master != [resolved]:
        # Should not happen: the resolver reads the same workbook. Report it
        # rather than hide it (e.g. the file changed while running).
        return row(
            STATUS_CONFLICT,
            resolved,
            f"Resolver returned {resolved} but the Security Master row says "
            f"{master_display}.",
        )

    if len(master_symbols) > 1:
        notes.insert(
            0,
            f"{len(master_symbols)} identical Security Master rows for this "
            "ISIN; refresh write-back for it is refused (and logged) until "
            "the duplicates are removed.",
        )
    return row(STATUS_MATCHED, resolved, " ".join(notes))


def run_diagnostics(assets, *, workbook_path):
    """Diagnose every asset.

    Returns (rows, workbook_error). An unreadable workbook (corrupt, or locked
    by Excel) is reported instead of raised, mirroring SecurityResolver, which
    then falls back to its built-in mappings.
    """
    try:
        master_symbols_by_isin = read_security_master_isin_symbols(workbook_path)
        workbook_error = None
    except Exception as exc:
        master_symbols_by_isin = {}
        workbook_error = f"{type(exc).__name__}: {exc}"
    rows = [diagnose_asset(asset, master_symbols_by_isin) for asset in assets]
    return rows, workbook_error


def summarize(rows):
    counts = {status: 0 for status in STATUS_ORDER}
    for item in rows:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts
