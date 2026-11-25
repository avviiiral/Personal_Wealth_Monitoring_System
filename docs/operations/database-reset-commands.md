# PWMS Database Reset Commands

This runbook documents the three custom Django management commands available in PWMS. Run every command from the `backend/` directory with the project's virtual environment active.

> **Destructive operations:** These commands permanently delete database rows. Review the dry-run output and confirm the database alias/name before proceeding. A database backup or verified restore point is strongly recommended. Never use `--noinput` until the target and deletion scope have been independently verified.

## Supported databases and safety model

- Supported database engines: PostgreSQL and SQLite.
- The database schema and `django_migrations` table are preserved.
- The commands enumerate physical tables, including unmanaged/legacy tables that Django's built-in flush may omit.
- Full-table resets perform a foreign-key scope preflight and do not use `TRUNCATE ... CASCADE`.
- PostgreSQL reset statements run inside a transaction. SQLite deletes are performed in a transaction with deferred foreign-key checking.
- If the reset fails, the transaction is rolled back. This does not replace a backup.

## Command summary

| Command | Deletes | Preserves |
| --- | --- | --- |
| `python manage.py flush` | All application data in physical application tables, including users, families, financial records, transactions, market data, MF reference data, watchlists, logs and sessions | Database schema and `django_migrations` |
| `python manage.py safe_flush1` | Financial, portfolio, market, mutual-fund, watchlist, tax-setting and other non-account application data | User accounts, Django groups/permissions/content types, user auth M2M tables, profiles, family groups/memberships, preferences and user audit logs |
| `python manage.py safe_flush2` | Investment and mutual-fund transaction ledger rows | User accounts, assets, holdings, portfolio positions, SIP instructions/installments, market data, MF master/reference data and other non-transaction records |

### 1. Full reset — `flush`

This command overrides Django's built-in `flush` to include all physical application tables, including unmanaged tables.

Dry-run first:

```powershell
python manage.py flush --dry-run
```

After reviewing the complete table list and confirming the target database:

```powershell
python manage.py flush
```

The command asks for the literal confirmation `yes`. It removes user and family data as well as financial and reference data. Django's `post_migrate` signal is emitted after a successful reset unless `--inhibit-post-migrate` is supplied, allowing standard post-migration data such as permissions to be recreated.

### 2. Preserve account data — `safe_flush1`

This option keeps account identity and access-related data while clearing application/financial data.

Dry-run first:

```powershell
python manage.py safe_flush1 --dry-run
```

Review both the `KEEP` and `DELETE` lists. Then execute only after confirming the scope:

```powershell
python manage.py safe_flush1
```

Preserved data includes the configured Django user model, auth groups and permissions, content types, user/group authentication M2M tables, user profiles, family groups and memberships, user preferences, and user audit logs.

Not preserved: Django sessions (users must log in again), Django admin log entries, tax-rate settings/history, and all portfolio, transaction, holdings, SIP, market and mutual-fund data.

### 3. Delete transaction ledgers only — `safe_flush2`

This command uses Django ORM deletion for:

- `investments.Transaction`
- `mutual_funds.MutualFundTransaction`

Dry-run first:

```powershell
python manage.py safe_flush2 --dry-run
```

Then execute after reviewing the transaction counts:

```powershell
python manage.py safe_flush2
```

Django's deletion collector cascades transaction edit-history rows and applies the configured `SET_NULL` behaviour to SIP-installment transaction links. It does not directly delete assets, holdings, portfolio positions, SIP instructions/installments, transaction upload/import metadata, market data or MF reference data. Derived holdings/positions may therefore need a separate, explicitly reviewed rebuild or reconciliation after the ledger is cleared.

## Options

| Option | `flush` / `safe_flush1` | `safe_flush2` |
| --- | --- | --- |
| `--dry-run` | Print selected tables; no deletion | Print transaction counts; no deletion |
| `--database <alias>` | Select Django database alias; defaults to `default` | Same |
| `--noinput` | Skip confirmation prompt | Skip confirmation prompt |
| `--reset-sequences` | Restart supported table identity sequences | Not available |
| `--inhibit-post-migrate` | Suppress the post-migrate signal for `flush` | Not available |
| `--allow-cascade` | Rejected deliberately; cascade truncation is not supported | Not available |

Use `python manage.py <command> --help` for the command's complete options.

## Recommended verification workflow

Run from `backend/`:

```powershell
python manage.py check
python manage.py flush --dry-run
python manage.py safe_flush1 --dry-run
python manage.py safe_flush2 --dry-run
python manage.py test
```

The dry runs are read-only. CI success and dry-run success do not prove that a destructive operation has been executed against a particular populated database. For production or any database with valuable data, verify the backup/restore procedure and review the actual dry-run output immediately before execution.

## Windows PowerShell location

If the repository root is `D:\\PWMS\\Personal_Wealth_Monitoring_System`, use:

```powershell
cd D:\\PWMS\\Personal_Wealth_Monitoring_System\\backend
.\\venv\\Scripts\\Activate.ps1
python manage.py safe_flush1 --dry-run
```
