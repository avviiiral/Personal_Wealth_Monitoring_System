from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


def _signature(setting):
    return (
        setting.tenure_months,
        setting.short_term_tax_rate,
        setting.long_term_tax_rate,
    )


def promote_unanimous_legacy_policies(apps, schema_editor):
    alias = schema_editor.connection.alias
    TaxRateSetting = apps.get_model("users", "TaxRateSetting")
    Transaction = apps.get_model("investments", "Transaction")

    groups = defaultdict(list)
    legacy_rows = list(
        TaxRateSetting.objects.using(alias)
        .filter(family__isnull=False, asset__isnull=False)
        .select_related("asset")
        .order_by("id")
    )

    for row in legacy_rows:
        name = (
            Transaction.objects.using(alias)
            .filter(
                family_id=row.family_id,
                asset_id=row.asset_id,
                asset_name__isnull=False,
            )
            .exclude(asset_name="")
            .order_by("id")
            .values_list("asset_name", flat=True)
            .first()
        )
        if not name:
            # Legacy rows without a matching transaction retain their
            # underlying Asset label and remain available as fallback.
            name = row.asset.name

        row.asset_name = name
        row.needs_review = False
        row.review_reason = ""
        row.save(update_fields=["asset_name", "needs_review", "review_reason"])
        groups[name].append(row)

    for display_name, rows in groups.items():
        signatures = {_signature(row) for row in rows}
        if len(signatures) != 1:
            reason = (
                "Conflicting legacy family/asset tax settings map to this "
                "displayed Asset Name. No global policy was selected; review "
                "the retained scoped records and configure a global policy."
            )
            TaxRateSetting.objects.using(alias).filter(
                pk__in=[row.pk for row in rows]
            ).update(needs_review=True, review_reason=reason)
            continue

        policy = rows[0]
        TaxRateSetting.objects.using(alias).get_or_create(
            asset_name=display_name,
            family__isnull=True,
            asset__isnull=True,
            defaults={
                "tenure_months": policy.tenure_months,
                "short_term_tax_rate": policy.short_term_tax_rate,
                "long_term_tax_rate": policy.long_term_tax_rate,
                "needs_review": False,
                "review_reason": "",
            },
        )


def reverse_global_policy_promotion(apps, schema_editor):
    alias = schema_editor.connection.alias
    TaxRateSetting = apps.get_model("users", "TaxRateSetting")

    globals_to_remove = list(
        TaxRateSetting.objects.using(alias)
        .filter(family__isnull=True, asset__isnull=True)
        .order_by("asset_name")
    )
    for policy in globals_to_remove:
        scoped = list(
            TaxRateSetting.objects.using(alias)
            .filter(
                family__isnull=False,
                asset__isnull=False,
                asset_name=policy.asset_name,
            )
        )
        expected = _signature(policy)
        if not scoped or any(_signature(row) != expected for row in scoped):
            raise migrations.IrreversibleError(
                "Cannot reverse global tax settings safely: "
                f"'{policy.asset_name}' has no identical retained legacy policy set. "
                "Export/reconcile the global settings before reversing this migration."
            )

    # Only delete policies that can be reconstructed entirely from retained
    # scoped rows. A changed/new global policy makes reversal intentionally fail.
    TaxRateSetting.objects.using(alias).filter(
        family__isnull=True, asset__isnull=True
    ).delete()
    TaxRateSetting.objects.using(alias).filter(
        family__isnull=False, asset__isnull=False
    ).update(asset_name="", needs_review=False, review_reason="")


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_sync_tax_rate_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="taxratesetting",
            name="asset_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Canonical displayed Asset Name for global policies or legacy mapping.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="taxratesetting",
            name="needs_review",
            field=models.BooleanField(
                default=False,
                help_text="True when migrated family-scoped settings conflict for this displayed name.",
            ),
        ),
        migrations.AddField(
            model_name="taxratesetting",
            name="review_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
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
        migrations.AlterField(
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
        migrations.RunPython(
            promote_unanimous_legacy_policies,
            reverse_global_policy_promotion,
        ),
        migrations.AddConstraint(
            model_name="taxratesetting",
            constraint=models.UniqueConstraint(
                fields=("asset_name",),
                condition=models.Q(family__isnull=True, asset__isnull=True),
                name="unique_global_tax_rate_asset_name",
            ),
        ),
        migrations.AlterModelOptions(
            name="taxratesetting",
            options={"ordering": ["asset_name"]},
        ),
    ]
