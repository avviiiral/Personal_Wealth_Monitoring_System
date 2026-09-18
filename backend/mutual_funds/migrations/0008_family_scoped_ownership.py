from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_family(apps, schema_editor):
    User = apps.get_model("auth", "User")
    model_names = [
        "MutualFundScheme",
        "MutualFundTransaction",
        "SIP",
        "MutualFundHolding",
    ]
    for model_name in model_names:
        Model = apps.get_model("mutual_funds", model_name)
        for obj in Model.objects.filter(family__isnull=True, owner__isnull=False).iterator():
            profile = getattr(obj.owner, "profile", None)
            if profile is None:
                continue
            family_id = getattr(profile, "active_family_group_id", None)
            if family_id is None:
                ids = list(profile.family_groups.values_list("id", flat=True))
                if len(ids) == 1:
                    family_id = ids[0]
            if family_id:
                obj.family_id = family_id
                obj.save(update_fields=["family"])


class Migration(migrations.Migration):
    dependencies = [
        ("mutual_funds", "0007_mutualfundunderlying_sector"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mutualfundscheme",
            name="family",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="mutual_fund_schemes", to="users.familygroup"),
        ),
        migrations.AddField(
            model_name="mutualfundtransaction",
            name="family",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="mutual_fund_transactions", to="users.familygroup"),
        ),
        migrations.AddField(
            model_name="sip",
            name="family",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sips", to="users.familygroup"),
        ),
        migrations.AddField(
            model_name="mutualfundholding",
            name="family",
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="mutual_fund_holdings", to="users.familygroup"),
        ),
        migrations.AlterField(
            model_name="mutualfundscheme",
            name="owner",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="mutual_fund_schemes", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="mutualfundtransaction",
            name="owner",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="mutual_fund_transactions", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="sip",
            name="owner",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sips", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="mutualfundholding",
            name="owner",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="mutual_fund_holdings", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(backfill_family, migrations.RunPython.noop),
    ]
