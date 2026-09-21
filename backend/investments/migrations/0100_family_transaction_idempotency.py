from django.db import migrations, models


def deduplicate_family_transactions(apps, schema_editor):
    Transaction = apps.get_model("investments", "Transaction")

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
        migrations.RemoveConstraint(
            model_name="transaction",
            name="unique_transaction_source_key",
        ),
        migrations.AddConstraint(
            model_name="transaction",
            constraint=models.UniqueConstraint(
                fields=("family", "source", "source_key"),
                condition=models.Q(("source_key__isnull", False)),
                name="unique_transaction_family_source_key",
            ),
        ),
    ]
