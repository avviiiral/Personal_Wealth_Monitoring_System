"""
Read-only coverage audit for MIS tax settings on the underlying assets.

Run from backend:
    python manage.py shell -c "exec(open('audit_tax_configuration_coverage.py', encoding='utf-8').read())"

Optional environment variables:
    MIS_TAX_AUDIT_FAMILY_ID=1
    MIS_TAX_AUDIT_OUTPUT=C:/path/to/report.csv

This script never creates, updates, or deletes database records. It writes a CSV
report to the filesystem and prints a short summary.
"""
import csv
import os
from collections import defaultdict
from datetime import date
from pathlib import Path

from django.db.models import Count, Min, Max, Sum

from investments.models import Transaction
from users.models import TaxRateSetting


TARGET_DISPLAY_ROWS = {
    "Buoyant Opportunities PMS",
    "Carnelian Capital Compounder Strategy",
    "Direct Equity",
}

family_id_env = os.environ.get("MIS_TAX_AUDIT_FAMILY_ID", "").strip()
output_env = os.environ.get("MIS_TAX_AUDIT_OUTPUT", "").strip()
today = date.today().isoformat()
default_output = Path.cwd() / f"mis_tax_configuration_coverage_{today}.csv"
output_path = Path(output_env) if output_env else default_output

transactions = Transaction.objects.filter(
    asset_name__in=TARGET_DISPLAY_ROWS,
    family__isnull=False,
).select_related("family", "asset")

if family_id_env:
    transactions = transactions.filter(family_id=int(family_id_env))

# Aggregate at report-row + family + actual underlying Asset ID. Do not group by
# display name alone: distinct securities can share the same display label.
groups = {}
for tx in transactions.iterator():
    key = (tx.family_id, tx.family.name, tx.family_name or "", tx.asset_name or "", tx.asset_id)
    if key not in groups:
        groups[key] = {
            "family_id": tx.family_id,
            "family": tx.family.name,
            "member_label": tx.family_name or "",
            "report_display_row": tx.asset_name or "",
            "asset_id": tx.asset_id,
            "underlying_asset": tx.asset.name,
            "asset_category": tx.asset.category,
            "symbol": tx.asset.symbol or "",
            "isin": tx.asset.isin or "",
            "portfolio_labels": set(),
            "transaction_types": set(),
            "transaction_count": 0,
            "first_transaction_date": tx.transaction_date,
            "last_transaction_date": tx.transaction_date,
            "net_quantity_sum": 0,
            "net_amount_sum": 0,
        }
    row = groups[key]
    row["transaction_count"] += 1
    row["portfolio_labels"].add(tx.portfolio or "")
    row["transaction_types"].add(tx.transaction_type or "")
    row["first_transaction_date"] = min(row["first_transaction_date"], tx.transaction_date)
    row["last_transaction_date"] = max(row["last_transaction_date"], tx.transaction_date)
    row["net_quantity_sum"] += tx.quantity or 0
    row["net_amount_sum"] += tx.amount or 0

family_ids = {row["family_id"] for row in groups.values()}
asset_ids = {row["asset_id"] for row in groups.values()}
settings = TaxRateSetting.objects.filter(
    family_id__in=family_ids,
    asset_id__in=asset_ids,
).select_related("family", "asset")
settings_by_key = {(s.family_id, s.asset_id): s for s in settings}

fieldnames = [
    "family_id", "family", "member_label", "report_display_row",
    "asset_id", "underlying_asset", "asset_category", "symbol", "isin",
    "portfolio_labels", "transaction_types", "transaction_count",
    "first_transaction_date", "last_transaction_date",
    "net_quantity_sum", "net_amount_sum", "setting_status",
    "tenure_months", "short_term_tax_rate_pct", "long_term_tax_rate_pct",
    "missing_setting_fields", "recommended_action",
]

output_path.parent.mkdir(parents=True, exist_ok=True)
status_counts = defaultdict(int)
display_row_counts = defaultdict(lambda: defaultdict(int))

with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()

    for key, row in sorted(
        groups.items(),
        key=lambda item: (
            item[1]["report_display_row"].casefold(),
            item[1]["member_label"].casefold(),
            item[1]["underlying_asset"].casefold(),
            item[1]["asset_id"],
        ),
    ):
        setting = settings_by_key.get((row["family_id"], row["asset_id"]))
        if setting is None:
            status = "MISSING"
            tenure = short_rate = long_rate = ""
            missing_fields = ["setting_record", "tenure_months", "short_term_tax_rate", "long_term_tax_rate"]
            action = "Confirm approved tax treatment, then configure this exact underlying asset."
        else:
            missing_fields = [
                name for name, value in (
                    ("tenure_months", setting.tenure_months),
                    ("short_term_tax_rate", setting.short_term_tax_rate),
                    ("long_term_tax_rate", setting.long_term_tax_rate),
                ) if value is None
            ]
            status = "INCOMPLETE" if missing_fields else "CONFIGURED"
            tenure = setting.tenure_months if setting.tenure_months is not None else ""
            short_rate = setting.short_term_tax_rate if setting.short_term_tax_rate is not None else ""
            long_rate = setting.long_term_tax_rate if setting.long_term_tax_rate is not None else ""
            action = (
                "Complete only the missing fields after confirming the approved tax policy."
                if missing_fields else
                "No configuration action indicated; validate policy/rates as part of review."
            )

        status_counts[status] += 1
        display_row_counts[row["report_display_row"]][status] += 1
        writer.writerow({
            "family_id": row["family_id"],
            "family": row["family"],
            "member_label": row["member_label"],
            "report_display_row": row["report_display_row"],
            "asset_id": row["asset_id"],
            "underlying_asset": row["underlying_asset"],
            "asset_category": row["asset_category"],
            "symbol": row["symbol"],
            "isin": row["isin"],
            "portfolio_labels": " | ".join(sorted(filter(None, row["portfolio_labels"]))),
            "transaction_types": " | ".join(sorted(filter(None, row["transaction_types"]))),
            "transaction_count": row["transaction_count"],
            "first_transaction_date": row["first_transaction_date"].isoformat(),
            "last_transaction_date": row["last_transaction_date"].isoformat(),
            "net_quantity_sum": row["net_quantity_sum"],
            "net_amount_sum": row["net_amount_sum"],
            "setting_status": status,
            "tenure_months": tenure,
            "short_term_tax_rate_pct": short_rate,
            "long_term_tax_rate_pct": long_rate,
            "missing_setting_fields": ", ".join(missing_fields),
            "recommended_action": action,
        })

print("\n=== Read-only MIS underlying tax-configuration coverage ===")
print(f"Database records changed: NO")
print(f"Underlying positions audited: {len(groups)}")
print(f"CSV report: {output_path.resolve()}")
for status in ("MISSING", "INCOMPLETE", "CONFIGURED"):
    print(f"{status}: {status_counts[status]}")
print("\nBy displayed MIS row:")
for display_row in sorted(display_row_counts):
    counts = display_row_counts[display_row]
    print(
        f"- {display_row}: "
        f"MISSING={counts['MISSING']}, "
        f"INCOMPLETE={counts['INCOMPLETE']}, "
        f"CONFIGURED={counts['CONFIGURED']}"
    )
if not groups:
    print(
        "WARNING: No matching transactions found. Check the active database, "
        "target display labels, or MIS_TAX_AUDIT_FAMILY_ID."
    )
