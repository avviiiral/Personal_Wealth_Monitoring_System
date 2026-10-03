from django.db import migrations, models
import django.db.models.deletion


def expand_global_allocations_to_each_family(apps, schema_editor):
    StandardAllocation = apps.get_model("analytics", "StandardAllocation")
    FamilyGroup = apps.get_model("users", "FamilyGroup")

    global_rows = list(StandardAllocation.objects.filter(family__isnull=True))

    for row in global_rows:
        for family in FamilyGroup.objects.all().iterator():
            StandardAllocation.objects.create(
                family_id=family.id,
                asset_category=row.asset_category,
                allocation_percent=row.allocation_percent,
                allocation_amount=row.allocation_amount,
            )

        row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0004_global_standardallocation"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="standardallocation",
            name="family",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="standard_allocations",
                to="users.familygroup",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="standardallocation",
            name="unique_global_standard_allocation",
        ),
        migrations.RunPython(
            expand_global_allocations_to_each_family,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="standardallocation",
            name="family",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="standard_allocations",
                to="users.familygroup",
            ),
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                fields=("family", "asset_category"),
                name="unique_family_standard_allocation",
            ),
        ),
    ]
