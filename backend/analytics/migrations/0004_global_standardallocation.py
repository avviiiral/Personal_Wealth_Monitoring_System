from django.db import migrations, models


def collapse_family_allocations_to_global(apps, schema_editor):
    StandardAllocation = apps.get_model("analytics", "StandardAllocation")

    # Keep the most recently updated target for each asset category. This
    # deterministically converts legacy family-specific settings into the
    # single global setting requested by the application.
    rows = list(
        StandardAllocation.objects
        .order_by("asset_category", "-updated_at", "-id")
    )

    seen_categories = set()
    for row in rows:
        if row.asset_category in seen_categories:
            row.delete()
            continue

        seen_categories.add(row.asset_category)
        row.family_id = None
        row.save(update_fields=["family"])


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0003_standardallocation_amount"),
    ]

    operations = [
        migrations.AlterField(
            model_name="standardallocation",
            name="family",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.CASCADE,
                related_name="standard_allocations",
                to="users.familygroup",
            ),
        ),
        migrations.RunPython(
            collapse_family_allocations_to_global,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="standardallocation",
            name="unique_family_standard_allocation",
        ),
        migrations.RemoveField(
            model_name="standardallocation",
            name="family",
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                fields=("asset_category",),
                name="unique_global_standard_allocation",
            ),
        ),
    ]
