from django.db import migrations, models
from django.db.models import Q



def backfill_family_group(apps, schema_editor):
    PortfolioNewsAlert = apps.get_model("portfolio_news", "PortfolioNewsAlert")
    Asset = apps.get_model("investments", "Asset")
    MutualFundScheme = apps.get_model("mutual_funds", "MutualFundScheme")

    unresolved = []

    for alert in PortfolioNewsAlert.objects.all().iterator():
        if alert.holding_type == "EQUITY":
            holding = Asset.objects.filter(pk=alert.holding_id).first()
        elif alert.holding_type == "MUTUAL_FUND":
            holding = MutualFundScheme.objects.filter(pk=alert.holding_id).first()
        else:
            holding = None

        family_group_id = getattr(holding, "family_group_id", None)

        if family_group_id is None:
            unresolved.append(
                f"alert_id={alert.pk}, holding_type={alert.holding_type}, "
                f"holding_id={alert.holding_id}"
            )
            continue

        alert.family_group_id = family_group_id
        alert.save(update_fields=["family_group"])

    if unresolved:
        sample = "; ".join(unresolved[:20])
        raise RuntimeError(
            "PortfolioNewsAlert family ownership backfill failed. "
            "Every alert must resolve to exactly one family through its "
            f"holding. Unresolved alerts: {sample}"
        )


def reverse_backfill_family_group(apps, schema_editor):
    PortfolioNewsAlert = apps.get_model("portfolio_news", "PortfolioNewsAlert")
    PortfolioNewsAlert.objects.update(family_group=None)


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0006_portfolionewsalert_interpretation_and_more"),
        ("users", "0001_initial"),
        ("investments", "0008_asset_family_group_holding_family_group_and_more"),
        ("mutual_funds", "0006_mutualfundholding_family_group_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfolionewsalert",
            name="family_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="portfolio_news_alerts",
                to="users.familygroup",
            ),
        ),
        migrations.RunPython(
            backfill_family_group,
            reverse_backfill_family_group,
        ),
        migrations.RemoveConstraint(
            model_name="portfolionewsalert",
            name="unique_alert_per_user_article_holding",
        ),
        migrations.AddConstraint(
            model_name="portfolionewsalert",
            constraint=models.UniqueConstraint(
                fields=[
                    "user",
                    "family_group",
                    "article",
                    "holding_type",
                    "holding_id",
                ],
                name="unique_alert_per_user_family_article_holding",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=["user", "family_group", "-created_at"],
                name="news_alert_user_family_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=["user", "family_group", "is_read"],
                name="news_alert_user_family_unread_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=["user", "family_group", "notification_tier"],
                name="news_alert_user_family_tier_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=["family_group", "-created_at"],
                name="news_alert_family_created_idx",
            ),
        ),
    ]
