from django.db import migrations, models
from django.db.models import Q


def copy_family_names(apps, schema_editor):
    StandardAllocation = apps.get_model("analytics", "StandardAllocation")

    # 0006 introduced a FamilyGroup foreign key. The Dashboard selector,
    # however, uses the Family Member label stored on transactions. Copy the
    # existing FamilyGroup name into the new label field before removing the FK.
    for row in StandardAllocation.objects.select_related("family").filter(family__isnull=False):
        family_name = (row.family.name or "").strip()
        row.family_name = family_name or None
        row.save(update_fields=["family_name"])


def deduplicate_family_names(apps, schema_editor):
    StandardAllocation = apps.get_model("analytics", "StandardAllocation")

    # FamilyGroup names are not database-unique. If multiple groups shared the
    # same name, keep the most recently updated allocation for each category.
    seen = set()
    duplicate_ids = []

    rows = StandardAllocation.objects.filter(
        family_name__isnull=False
    ).order_by("family_name", "asset_category", "-updated_at", "-id")

    for row in rows:
        key = (row.family_name, row.asset_category)
        if key in seen:
            duplicate_ids.append(row.id)
        else:
            seen.add(key)

    if duplicate_ids:
        StandardAllocation.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("analytics", "0006_global_and_family_standardallocation"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="standardallocation",
            name="unique_family_standard_allocation",
        ),
        migrations.RemoveConstraint(
            model_name="standardallocation",
            name="unique_global_standard_allocation",
        ),
        migrations.AddField(
            model_name="standardallocation",
            name="family_name",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                db_index=True,
                help_text="Null means this is the global Standard Allocation shared by all Family Members.",
            ),
        ),
        migrations.RunPython(
            copy_family_names,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            deduplicate_family_names,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="standardallocation",
            name="family",
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                condition=Q(family_name__isnull=False),
                fields=("family_name", "asset_category"),
                name="unique_family_standard_allocation",
            ),
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                condition=Q(family_name__isnull=True),
                fields=("asset_category",),
                name="unique_global_standard_allocation",
            ),
        ),
    ]
