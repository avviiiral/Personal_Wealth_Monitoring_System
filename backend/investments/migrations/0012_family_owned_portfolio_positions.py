from django.db import migrations, models


def normalize_family_owned_positions(apps, schema_editor):
    PortfolioPosition = apps.get_model("investments", "PortfolioPosition")

    # Position ownership is now the family. Clear uploader ownership and
    # collapse any duplicate positions that could have existed per uploader.
    positions = PortfolioPosition.objects.order_by("id")
    seen = {}

    for position in positions:
        key = (
            position.family_id,
            position.family_name,
            position.portfolio,
            position.asset_id,
        )

        if key in seen:
            position.delete()
            continue

        if position.owner_id is not None:
            position.owner_id = None
            position.save(update_fields=["owner"])

        seen[key] = position.id


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0011_portfolioposition_family_scope"),
    ]

    operations = [
        migrations.RunPython(
            normalize_family_owned_positions,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="portfolioposition",
            name="unique_portfolio_position",
        ),
        migrations.AddConstraint(
            model_name="portfolioposition",
            constraint=models.UniqueConstraint(
                fields=["family", "family_name", "portfolio", "asset"],
                name="unique_portfolio_position",
            ),
        ),
    ]