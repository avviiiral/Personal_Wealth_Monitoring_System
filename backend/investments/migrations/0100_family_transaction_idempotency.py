from django.db import migrations, models


def deduplicate_family_transactions(apps, schema_editor):
    Transaction = apps.get_model("investments", "Transaction")

    seen = set()
    duplicate_ids = []

    rows = (
        Transaction.objects
        .filter(
            family__isnull=False,
            source="EXCEL",
            source_key__isnull=False,
        )
        .order_by("family_id", "source_key", "created_at", "id")
        .values_list("id", "family_id", "source_key")
    )

    for tx_id, family_id, source_key in rows:
        key = (family_id, source_key)
        if key in seen:
            duplicate_ids.append(tx_id)
        else:
            seen.add(key)

    if duplicate_ids:
        Transaction.objects.filter(id__in=duplicate_ids).delete()

    # Rebuild family portfolio positions from the surviving transaction
    # ledger so an already-corrupted import is corrected immediately,
    # rather than only on the next upload.
    PortfolioPosition = apps.get_model("investments", "PortfolioPosition")
    Asset = apps.get_model("investments", "Asset")
    MarketPrice = apps.get_model("market_data", "MarketPrice")

    affected_families = set(
        Transaction.objects
        .filter(
            family__isnull=False,
            source="EXCEL",
            source_key__isnull=False,
        )
        .values_list("family_id", flat=True)
    )

    for family_id in affected_families:
        PortfolioPosition.objects.filter(family_id=family_id).delete()

        groups = (
            Transaction.objects
            .filter(
                family_id=family_id,
                family_name__isnull=False,
                portfolio__isnull=False,
            )
            .values("family_name", "portfolio", "asset_id")
            .distinct()
        )

        for group in groups:
            transactions = (
                Transaction.objects
                .filter(
                    family_id=family_id,
                    family_name=group["family_name"],
                    portfolio=group["portfolio"],
                    asset_id=group["asset_id"],
                )
                .order_by("transaction_date", "created_at", "id")
            )

            quantity = 0
            invested_value = 0

            for tx in transactions:
                tx_quantity = tx.quantity or 0
                tx_amount = tx.amount or 0

                if tx.transaction_type in ("BUY", "SIP"):
                    quantity += tx_quantity
                    invested_value += tx_amount
                elif tx.transaction_type == "SELL":
                    if tx_quantity <= 0 or quantity <= 0:
                        continue
                    average_cost = invested_value / quantity if quantity > 0 else 0
                    sell_quantity = min(tx_quantity, quantity)
                    quantity -= sell_quantity
                    invested_value -= average_cost * sell_quantity
                    if quantity <= 0:
                        quantity = 0
                        invested_value = 0

            if quantity <= 0:
                continue

            asset = Asset.objects.get(id=group["asset_id"])
            latest_price = (
                MarketPrice.objects
                .filter(asset_id=asset.id)
                .order_by("-date", "-id")
                .first()
            )
            current_price = latest_price.close_price if latest_price is not None else 0
            average_cost = invested_value / quantity if quantity > 0 else 0
            current_value = quantity * current_price
            gain = current_value - invested_value

            first_tx = transactions.first()
            PortfolioPosition.objects.create(
                owner_id=first_tx.owner_id if first_tx is not None else None,
                family_id=family_id,
                family_name=group["family_name"],
                portfolio=group["portfolio"],
                asset_id=asset.id,
                quantity=quantity,
                average_cost=average_cost,
                invested_value=invested_value,
                current_price=current_price,
                current_value=current_value,
                gain=gain,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0099_asset_underlying_holding"),
        ("market_data", "0003_marketprice_updated_by"),
    ]

    operations = [
        migrations.RunPython(
            deduplicate_family_transactions,
            migrations.RunPython.noop,
        ),
        migrations.RemoveIndex(
            model_name="transaction",
            name="transaction_source_key_idx",
        ),
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(
                fields=("family", "source", "source_key"),
                name="transaction_source_key_idx",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="transaction",
            name="unique_transaction_source_key",
        ),
        migrations.AddConstraint(
            model_name="transaction",
            constraint=models.UniqueConstraint(
                fields=("family", "source", "source_key"),
                condition=models.Q(("source_key__isnull", False)),
                name="unique_transaction_family_source_key",
            ),
        ),
    ]
