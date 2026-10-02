from django.db import migrations, models
import django.db.models.deletion


def backfill_family_scope(apps, schema_editor):
    PortfolioNewsMatch = apps.get_model("portfolio_news", "PortfolioNewsMatch")
    UserProfile = apps.get_model("users", "UserProfile")

    profiles = {
        profile.user_id: profile.active_family_group_id
        for profile in UserProfile.objects.all().only(
            "user_id",
            "active_family_group",
        )
    }

    # Existing matches were generated from a user's active portfolio view.
    # Preserve that scope as the family scope where it can be resolved.
    for match in PortfolioNewsMatch.objects.all().iterator():
        family_id = profiles.get(match.user_id)
        if family_id:
            match.family_id = family_id
            match.save(update_fields=["family"])

    # Multiple members of one family may have produced the same
    # article/holding match. Keep one family-level record; the API now
    # treats family as the authoritative news scope.
    seen = set()
    duplicate_ids = []
    for match in (
        PortfolioNewsMatch.objects
        .filter(family__isnull=False)
        .order_by("family_id", "article_id", "holding_type", "holding_id", "id")
        .iterator()
    ):
        key = (
            match.family_id,
            match.article_id,
            match.holding_type,
            match.holding_id,
        )
        if key in seen:
            duplicate_ids.append(match.id)
        else:
            seen.add(key)

    if duplicate_ids:
        PortfolioNewsMatch.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0011_portfolionewsmatch"),
        ("users", "0005_sync_tax_rate_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfolionewsmatch",
            name="family",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="portfolio_news_matches",
                to="users.familygroup",
            ),
        ),
        migrations.RunPython(
            backfill_family_scope,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="portfolionewsmatch",
            name="unique_raw_news_match_per_holding",
        ),
        migrations.AddConstraint(
            model_name="portfolionewsmatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(family__isnull=True),
                fields=("user", "article", "holding_type", "holding_id"),
                name="unique_legacy_raw_news_match_per_holding",
            ),
        ),
        migrations.AddConstraint(
            model_name="portfolionewsmatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(family__isnull=False),
                fields=("family", "article", "holding_type", "holding_id"),
                name="unique_family_raw_news_match_per_holding",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsmatch",
            index=models.Index(
                fields=["family", "-created_at"],
                name="news_match_family_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsmatch",
            index=models.Index(
                fields=["family", "article"],
                name="news_match_family_article_idx",
            ),
        ),
    ]
