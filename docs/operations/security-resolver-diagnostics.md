# Security Resolver Diagnostics

This runbook describes the read-only diagnostic for the Yahoo Finance symbol-resolution path used by PWMS market-price refreshes.

## Command

Run from `backend/` with the project's virtual environment active:

```powershell
python manage.py diagnose_security_resolver
```

The default scope matches the Yahoo-routed market-price universe: STOCK, ETF and legacy Direct Equity assets. Bonds and Sovereign Gold Bonds are excluded because they use separate pricing services.

Useful filters:

```powershell
python manage.py diagnose_security_resolver --only-issues
python manage.py diagnose_security_resolver --asset-id 123
python manage.py diagnose_security_resolver --isin INE000A01001
python manage.py diagnose_security_resolver --user-id 7
python manage.py diagnose_security_resolver --format json
python manage.py diagnose_security_resolver --asset-id 123 --isin INE000A01001 --format json
```

Examples use placeholder identifiers. Supply IDs and ISINs from the target database.

## Read-only guarantee

The command invokes the same `SecurityResolver.resolve_yahoo_symbol()` path used by market refresh, but it does not invoke symbol write-back. It reloads the existing workbook without generating it when missing, opens the workbook read-only, does not call Yahoo Finance or NSE, and does not modify `Asset.symbol` or `security_master.xlsx`. Database inspection runs inside an atomic transaction that is explicitly rolled back.

A missing or unreadable workbook is reported. The resolver then uses configured fallback mappings; the diagnostic does not create or repair the workbook.

## Resolution priority and conflict handling

1. Security Master Excel symbol: by ISIN, else by exact normalized name (only for a row whose ISIN is blank or equals the asset's). **It always wins**: a different `Asset.symbol`, exchange, configured mapping or name mapping is ignored (logged once per process), not reported as a conflict.
2. Existing configured ISIN mapping.
3. Explicit Yahoo/exchange symbol.
4. Existing configured name mapping.
5. Generic symbol plus configured exchange suffix.

The diagnostic reports this `resolve_yahoo_symbol()` order. The price refresh (`MarketDataManager.resolve_asset_symbol`) wraps it Excel-first: a workbook hit (ISIN, then exact normalized name) is used without writing the workbook, and a blank or stale `Asset.symbol` (for example `IIFLSEC.NS` after NSE renamed the company's symbol to `IIFLCAPS`) is replaced by the workbook symbol; on a miss the order above runs, then NSE `EQUITY_L.csv` and an exact-name Yahoo search, and the new mapping is saved to the workbook after its first successful price download. See [BACKGROUND_JOBS.md](./BACKGROUND_JOBS.md#security-master-backenddatasecurity_masterxlsx).

Name normalization is conservative: it standardizes whitespace, punctuation and case, `&`/`AND`, `(I)`/`INDIA`, and legal-name variants such as a leading `THE` and terminal `LTD/LIMITED`. It is not fuzzy or substring matching.

Conflicting duplicate mappings are quarantined rather than allowing workbook row order to decide the result; an ISIN with two different workbook symbols fails closed (`AMBIGUOUS`). When the workbook has no row for the security, a configured ISIN mapping that disagrees with the explicit symbol or name mapping is still a conflict and fails closed rather than silently selecting a ticker.

## Status interpretation

| Status | Meaning | Suggested investigation |
| --- | --- | --- |
| `MATCHED` | Resolver returned the Security Master ISIN symbol. A note "overrides Asset.symbol …" means the database symbol is stale; the next price refresh replaces it | No action unless the row includes a note |
| `NOT_IN_MASTER` | ISIN is absent from the workbook or its row has no Yahoo symbol; a fallback resolved the asset | Review Security Master refresh/write-back |
| `MISSING_ISIN` | A symbol was resolved without an ISIN | Verify the authoritative ISIN |
| `AMBIGUOUS` | Conflicting Security Master mappings exist | Correct duplicate/conflicting workbook rows |
| `CONFLICT` | No workbook row, and a configured ISIN mapping disagrees with the explicit symbol or name; or the workbook changed during inspection | Verify asset ISIN, name, explicit symbol and configured mapping, or add the security to the workbook |
| `UNRESOLVED` | No safe Yahoo symbol could be resolved | Check ISIN, name, symbol and supported exchange mapping |
| `ERROR` | An unexpected per-asset diagnostic error occurred | Inspect the reason and backend logs |

The command prints a summary count for every status. `--only-issues` suppresses clean MATCHED rows but retains MATCHED rows with diagnostic notes.

## Safe repair workflow

1. Run the diagnostic and save the output.
2. Investigate `AMBIGUOUS` and `CONFLICT` first. Do not resolve these by guessing from similar company names.
3. Verify authoritative ISINs and Yahoo symbols from trusted security-master data.
4. Update the Security Master through the existing approved generation/maintenance workflow.
5. Rerun the diagnostic and confirm the intended assets resolve uniquely.
6. Only then run the market-price refresh and inspect its updated/skipped/failed counts and persisted price history.

The diagnostic itself never performs steps 3–6.

## Related implementation

- Resolver: `backend/market_data/services/security_resolver.py`
- Read-only diagnostics: `backend/market_data/services/security_resolver_diagnostics.py`
- Management command: `backend/market_data/management/commands/diagnose_security_resolver.py`
- Market-price command: `backend/market_data/management/commands/update_market_prices.py`
- Shared refresh universe: `backend/market_data/services/market_price_scope.py`
