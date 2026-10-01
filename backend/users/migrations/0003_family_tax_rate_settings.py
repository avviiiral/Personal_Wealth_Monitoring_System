from django.db import migrations, models
import django.db.models.deletion


def migrate_legacy_tax_settings(apps, schema_editor):
    TaxRateSetting = apps.get_model("users", "TaxRateSetting")
    Asset = apps.get_model("investments", "Asset")
    FamilyGroup = apps.get_model("users", "FamilyGroup")

    for row in TaxRateSetting.objects.all().select_related("user"):
        profile = getattr(row.user, "profile", None)
        family = None

        if profile is not None:
            family_id = getattr(profile, "active_family_group_id", None)
            if family_id:
                family = FamilyGroup.objects.filter(pk=family_id).first()

            if family is None:
                family = (
                    FamilyGroup.objects
                    .filter(members__user=row.user)
                    .order_by("id")
                    .first()
                )

        if family is None:
            row.delete()
            continue

        asset = (
            Asset.objects
            .filter(family=family, name=row.asset_name)
            .order_by("id")
            .first()
        )

        if asset is None:
            row.delete()
            continue

        row.family_id = family.id
        row.asset_id = asset.id
        row.tenure_months = None
        row.short_term_tax_rate = row.tax_rate
        row.long_term_tax_rate = row.tax_rate
        row.save(
            update_fields=[
                "family",
                "asset",
                "tenure_months",
                "short_term_tax_rate",
                "long_term_tax_rate",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0001_initial"),
        ("users", "0002_tax_rate_setting"),
    ]

    operations = [
        migrations.AddField(
            model_name="taxratesetting",
            name="asset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tax_rate_settings",
                to="investments.asset",
            ),
        ),
        migrations.AddField(
            model_name="taxratesetting",
            name="family",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tax_rate_settings",
                to="users.familygroup",
            ),
        ),
        migrations.AddField(
            model_name="taxratesetting",
            name="tenure_months",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Holding tenure in months used to classify short-term vs long-term gains.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="taxratesetting",
            name="short_term_tax_rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=7,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="taxratesetting",
            name="long_term_tax_rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=7,
                null=True,
            ),
        ),
        migrations.RunPython(migrate_legacy_tax_settings, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="taxratesetting",
            name="user",
        ),
        migrations.RemoveField(
            model_name="taxratesetting",
            name="asset_name",
        ),
        migrations.RemoveField(
            model_name="taxratesetting",
            name="tax_rate",
        ),
        migrations.AlterField(
            model_name="taxratesetting",
            name="asset",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tax_rate_settings",
                to="investments.asset",
            ),
        ),
        migrations.AlterField(
            model_name="taxratesetting",
            name="family",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tax_rate_settings",
                to="users.familygroup",
            ),
        ),
        migrations.AddConstraint(
            model_name="taxratesetting",
            constraint=models.UniqueConstraint(
                fields=("family", "asset"),
                name="unique_family_tax_rate_asset",
            ),
        ),
    ]
