from django.db import migrations, models


def deduplicate_family_transactions(apps, schema_editor):
    Transaction = apps.get_model("investments", "Transaction")
    PortfolioPosition = apps.get_model("investments", "PortfolioPosition")

    # Remove repeated Excel transaction rows within each family.
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

    # Existing positions are already derived data. Remove them here and let
    # the normal post-migration import/position rebuild path recreate them.
    # This avoids calculating positions in a migration against historical
    # model state and avoids unique-key collisions with existing rows.
    PortfolioPosition.objects.filter(
        family_id__in=Transaction.objects
        .filter(family__isnull=False)
        .values_list("family_id", flat=True)
        .distinct()
    ).delete()

