from django.db import migrations, models


def merge_scheme_transactions_and_holdings(apps, schema_editor):
    Scheme = apps.get_model("mutual_funds", "MutualFundScheme")
    Transaction = apps.get_model("mutual_funds", "MutualFundTransaction")
    Holding = apps.get_model("mutual_funds", "MutualFundHolding")
    SIP = apps.get_model("mutual_funds", "SIP")

    # First merge schemes that were accidentally created more than once
    # for the same family. The importer historically scoped scheme lookup
    # by owner, so two family members could create separate scheme rows.
    family_schemes = (
        Scheme.objects
        .filter(family__isnull=False)
        .order_by("family_id", "id")
    )

    canonical = {}
    duplicate_scheme_ids = []

    for scheme in family_schemes:
        key = (
            scheme.family_id,
            (scheme.isin_growth or "").strip().upper()
            or (scheme.scheme_name or "").strip().upper(),
        )

        if key not in canonical:
            canonical[key] = scheme
            continue

        keep = canonical[key]

        # Move transactions to the canonical scheme.
        Transaction.objects.filter(scheme_id=scheme.id).update(
            scheme_id=keep.id,
            family_id=keep.family_id,
        )

        # Move SIPs to the canonical scheme.
        SIP.objects.filter(scheme_id=scheme.id).update(
            scheme_id=keep.id,
            family_id=keep.family_id,
        )

        duplicate_holding = Holding.objects.filter(scheme_id=scheme.id).first()
        canonical_holding = Holding.objects.filter(scheme_id=keep.id).first()

        if duplicate_holding is not None:
            if canonical_holding is None:
                duplicate_holding.scheme_id = keep.id
                duplicate_holding.owner_id = keep.owner_id
                duplicate_holding.family_id = keep.family_id
                duplicate_holding.save(
                    update_fields=["scheme", "owner", "family"]
                )
            else:
                duplicate_holding.delete()

        duplicate_scheme_ids.append(scheme.id)

    if duplicate_scheme_ids:
        Scheme.objects.filter(id__in=duplicate_scheme_ids).delete()

    # Deduplicate transactions after scheme merging. Keep the oldest row
    # for an identical family-level transaction.
    seen = set()
    duplicate_tx_ids = []

    transactions = (
        Transaction.objects
        .filter(
            family__isnull=False,
        )
        .order_by(
            "family_id",
            "scheme_id",
            "family_name",
            "portfolio",
            "transaction_type",
            "transaction_date",
            "units",
            "nav",
            "amount",
            "created_at",
            "id",
        )
    )

    for tx in transactions:
        key = (
            tx.family_id,
            tx.scheme_id,
            tx.family_name or "",
            tx.portfolio or "",
            tx.transaction_type,
            tx.transaction_date,
            tx.units,
            tx.nav,
            tx.amount,
        )

        if key in seen:
            duplicate_tx_ids.append(tx.id)
        else:
            seen.add(key)

    if duplicate_tx_ids:
        Transaction.objects.filter(id__in=duplicate_tx_ids).delete()

    # Rebuild all remaining family holdings from the surviving transactions.
    # This removes any stale doubled values left by the old import behavior.
    for scheme in Scheme.objects.filter(family__isnull=False, is_active=True):
        units = 0
        invested = 0

        scheme_transactions = Transaction.objects.filter(
            scheme_id=scheme.id,
            family_id=scheme.family_id,
        ).order_by("transaction_date", "created_at", "id")

        for tx in scheme_transactions:
            tx_units = tx.units or 0
            amount = tx.amount or 0

            if tx.transaction_type in ("PURCHASE", "SIP"):
                units += tx_units
                invested += amount
            elif tx.transaction_type == "REDEMPTION" and units > 0 and tx_units > 0:
                average_cost = invested / units if units else 0
                sold = min(tx_units, units)
                units -= sold
                invested -= average_cost * sold
                if units <= 0:
                    units = 0
                    invested = 0

        average_nav = invested / units if units > 0 else 0

        nav = (
            apps.get_model("mutual_funds", "MutualFundNAV")
            .objects
            .filter(scheme_id=scheme.id)
            .order_by("-date")
            .first()
        )
        current_nav = nav.nav if nav is not None else 0
        current_value = units * current_nav
        pnl = current_value - invested

        Holding.objects.update_or_create(
            scheme_id=scheme.id,
            defaults={
                "owner_id": scheme.owner_id,
                "family_id": scheme.family_id,
                "units": units,
                "invested_value": invested,
                "average_nav": average_nav,
                "current_nav": current_nav,
                "current_value": current_value,
                "unrealized_pnl": pnl,
            },
        )


def populate_mf_source_keys(apps, schema_editor):
    Transaction = apps.get_model("mutual_funds", "MutualFundTransaction")
    import hashlib

    rows = (
        Transaction.objects
        .filter(family__isnull=False, source_key__isnull=True)
        .order_by("id")
    )

    for tx in rows:
        raw = "|".join([
            str(tx.family_id),
            str(tx.family_name or ""),
            str(tx.portfolio or ""),
            str(tx.scheme_id),
            str(tx.transaction_type),
            str(tx.transaction_date),
            str(tx.units),
            str(tx.nav),
            str(tx.amount),
        ]).upper()
        tx.source_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        tx.save(update_fields=["source_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0008_family_scoped_ownership"),
    ]

    operations = [
        migrations.AddField(
            model_name="mutualfundtransaction",
            name="source_key",
            field=models.CharField(
                max_length=64,
                blank=True,
                null=True,
            ),
        ),
        migrations.RunPython(
            merge_scheme_transactions_and_holdings,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            populate_mf_source_keys,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="mutualfundscheme",
            name="unique_mf_scheme_owner_code",
        ),
        migrations.AddConstraint(
            model_name="mutualfundscheme",
            constraint=models.UniqueConstraint(
                condition=models.Q(("scheme_code__isnull", False)),
                fields=("family", "scheme_code"),
                name="unique_mf_scheme_family_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="mutualfundscheme",
            constraint=models.UniqueConstraint(
                fields=("family", "scheme_name"),
                name="unique_mf_scheme_family_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="mutualfundtransaction",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_key__isnull", False)),
                fields=("family", "source_key"),
                name="unique_mf_transaction_family_source_key",
            ),
        ),
        migrations.AddIndex(
            model_name="mutualfundtransaction",
            index=models.Index(
                fields=("family", "source_key"),
                name="mf_tx_family_source_key_idx",
            ),
        ),
    ]
