from django.db import migrations, models
import django.db.models.deletion


def move_existing_allocations_to_families(apps, schema_editor):
    StandardAllocation = apps.get_model("analytics", "StandardAllocation")
    FamilyGroup = apps.get_model("users", "FamilyGroup")

    seen = set()
    rows = StandardAllocation.objects.order_by("-updated_at", "-id")

    for row in rows:
        if not row.family_name:
            row.delete()
            continue

        family = FamilyGroup.objects.filter(name=row.family_name).order_by("id").first()
        if family is None:
            row.delete()
            continue

        key = (family.id, row.asset_category)
        if key in seen:
            row.delete()
            continue

        row.family_id = family.id
        row.save(update_fields=["family"])
        seen.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0001_standardallocation"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="standardallocation",
            name="family",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="standard_allocations",
                to="users.familygroup",
            ),
        ),
        migrations.RunPython(
            move_existing_allocations_to_families,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="standardallocation",
            name="unique_standard_allocation_scope",
        ),
        migrations.RemoveField(
            model_name="standardallocation",
            name="user",
        ),
        migrations.RemoveField(
            model_name="standardallocation",
            name="family_name",
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
