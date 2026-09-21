from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0009_family_transaction_idempotency"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="mutualfundscheme",
            name="unique_mf_scheme_owner_code",
        ),
        migrations.AddConstraint(
            model_name="mutualfundscheme",
            constraint=models.UniqueConstraint(
                condition=models.Q(scheme_code__isnull=False),
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
                condition=models.Q(source_key__isnull=False),
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
