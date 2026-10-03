from django.db import migrations, models
from django.db.models import Q


def create_global_defaults_from_family_rows(apps, schema_editor):
    StandardAllocation = apps.get_model("analytics", "StandardAllocation")

    latest_by_category = {}
    rows = StandardAllocation.objects.filter(family__isnull=False).order_by(
        "asset_category",
        "-updated_at",
        "-id",
    )

    for row in rows:
        if row.asset_category in latest_by_category:
            continue

        latest_by_category[row.asset_category] = StandardAllocation(
            family=None,
            asset_category=row.asset_category,
            allocation_percent=row.allocation_percent,
            # Global Standard Allocation is percentage-based. Amount is retained
            # only for API compatibility and is recalculated on future saves.
            allocation_amount=0,
        )

    if latest_by_category:
        StandardAllocation.objects.bulk_create(latest_by_category.values())


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0005_family_standardallocation"),
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
                help_text="Null means this is the global Standard Allocation shared by all families.",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="standardallocation",
            name="unique_family_standard_allocation",
        ),
        migrations.RunPython(
            create_global_defaults_from_family_rows,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                condition=Q(family__isnull=False),
                fields=("family", "asset_category"),
                name="unique_family_standard_allocation",
            ),
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                condition=Q(family__isnull=True),
                fields=("asset_category",),
                name="unique_global_standard_allocation",
            ),
        ),
    ]
