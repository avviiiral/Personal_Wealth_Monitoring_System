from django.db import migrations


def deduplicate_family_transactions(apps, schema_editor):
    Transaction = apps.get_model("investments", "Transaction")

    # Remove repeated Excel transaction rows within each family.
    # This migration intentionally contains only data changes. Keeping
    # the data cleanup in its own migration allows PostgreSQL to commit
    # DELETE-trigger work before the following schema migration creates
    # or changes indexes/constraints.
    seen = set()
    duplicate_ids = []

    rows = (
        Transaction.objects
        .filter(
            family__isnull=False,
            source="EXCEL",
            source_key__isnull=False,
        )
        .order_by("family_id", "source_key", "created_at", "id")
        .values_list("id", "family_id", "source_key")
    )

    for tx_id, family_id, source_key in rows:
        key = (family_id, source_key)
        if key in seen:
            duplicate_ids.append(tx_id)
        else:
            seen.add(key)

    if duplicate_ids:
        Transaction.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0099_asset_underlying_holding"),
    ]

    operations = [
        migrations.RunPython(
            deduplicate_family_transactions,
            migrations.RunPython.noop,
        ),
    ]
