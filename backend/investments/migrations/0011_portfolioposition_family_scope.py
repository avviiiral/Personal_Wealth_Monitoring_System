from django.db import migrations, models


def backfill_portfolio_position_family(apps, schema_editor):
    PortfolioPosition = apps.get_model(
        "investments",
        "PortfolioPosition",
    )
    Transaction = apps.get_model(
        "investments",
        "Transaction",
    )

    for position in PortfolioPosition.objects.filter(
        family__isnull=True
    ).iterator():
        family_id = (
            PortfolioPosition.objects
            .filter(pk=position.pk)
            .values_list("asset__family_id", flat=True)
            .first()
        )

        if family_id is None:
            family_id = (
                Transaction.objects
                .filter(
                    asset_id=position.asset_id,
                    family_name=position.family_name,
                    portfolio=position.portfolio,
                    family__isnull=False,
                )
                .order_by("-transaction_date", "-id")
                .values_list("family_id", flat=True)
                .first()
            )

        if family_id is not None:
            position.family_id = family_id
            position.save(update_fields=["family"])


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0010_family_scoped_ownership"),
    ]

    operations = [
        migrations.RunPython(
            backfill_portfolio_position_family,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="portfolioposition",
            name="unique_portfolio_position",
        ),
        migrations.AddConstraint(
            model_name="portfolioposition",
            constraint=models.UniqueConstraint(
                fields=[
                    "owner",
                    "family",
                    "family_name",
                    "portfolio",
                    "asset",
                ],
                name="unique_portfolio_position",
            ),
        ),
    ]
