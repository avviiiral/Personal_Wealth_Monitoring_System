from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_family(apps, schema_editor):
    User = apps.get_model("auth", "User")
    FamilyGroup = apps.get_model("users", "FamilyGroup")
    model_names = [
        "Asset",
        "Transaction",
        "TransactionEditHistory",
        "Holding",
        "PortfolioPosition",
        "SecurityMaster",
    ]
    for model_name in model_names:
        Model = apps.get_model("investments", model_name)
        for obj in Model.objects.filter(family__isnull=True, owner__isnull=False).iterator():
            profile = getattr(obj.owner, "profile", None)
            if profile is None:
                continue
            # Migration-time historical relations are used only to infer an unambiguous family.
            active_id = getattr(profile, "active_family_group_id", None)
            family_id = active_id
            if family_id is None:
                ids = list(profile.family_groups.values_list("id", flat=True))
                if len(ids) == 1:
                    family_id = ids[0]
            if family_id:
                obj.family_id = family_id
                obj.save(update_fields=["family"])


class Migration(migrations.Migration):
    dependencies = [
        ("investments", "0009_transactionedithistory"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="transactionedithistory",
            name="edited_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="transaction_edits_made", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="asset",
            name="family",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assets",
                to="users.familygroup",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="family",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transactions",
                to="users.familygroup",
            ),
        ),
        migrations.AddField(
            model_name="transactionedithistory",
            name="family",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transaction_edit_history",
                to="users.familygroup",
            ),
        ),
        migrations.AddField(
            model_name="holding",
            name="family",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="holdings",
                to="users.familygroup",
            ),
        ),
        migrations.AddField(
            model_name="portfolioposition",
            name="family",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="portfolio_positions",
                to="users.familygroup",
            ),
        ),
        migrations.AddField(
            model_name="securitymaster",
            name="family",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="security_masters",
                to="users.familygroup",
            ),
        ),
        migrations.AlterField(
            model_name="asset",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assets",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="transaction",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="transactionedithistory",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transaction_edit_history",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="holding",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="holdings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="portfolioposition",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="portfolio_positions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="securitymaster",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="security_masters",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="securitymaster",
            name="unique_security_master_owner_isin",
        ),
        migrations.AddConstraint(
            model_name="securitymaster",
            constraint=models.UniqueConstraint(fields=["family", "isin"], name="unique_security_master_family_isin"),
        ),
        migrations.RunPython(backfill_family, migrations.RunPython.noop),
    ]
