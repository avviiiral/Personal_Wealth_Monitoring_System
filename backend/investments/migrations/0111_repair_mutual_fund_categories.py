"""Data migration: set category MUTUAL_FUND on funds imported as BOND/STOCK/CASH.

NEEDS APPROVAL BEFORE RUNNING ON A REAL DATABASE. Review the list first:

    python manage.py report_fund_categories

Only Asset.category changes; no transaction is created, modified or deleted.
Reverse is a no-op on purpose: the old categories were wrong, and restoring
them would reintroduce the bond-provider stall.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    from investments.services.fund_category_repair import apply_fund_category_repair, find_mislabelled_funds

    Asset = apps.get_model("investments", "Asset")
    Transaction = apps.get_model("investments", "Transaction")
    report = find_mislabelled_funds(Asset, Transaction)
    changed = apply_fund_category_repair(Asset, Transaction)
    if changed or report["conflicts"]:
        print(f"\n  Fund category repair: {changed} asset(s) set to MUTUAL_FUND; "
              f"{len(report['conflicts'])} left unchanged (see report_fund_categories).")


class Migration(migrations.Migration):
    dependencies = [
        ("investments", "0110_temporal_security_identity"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
