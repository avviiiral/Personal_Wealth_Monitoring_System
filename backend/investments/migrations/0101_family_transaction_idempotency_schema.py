from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0100_family_transaction_idempotency"),
    ]

    operations = [
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
                condition=models.Q(source_key__isnull=False),
                name="unique_transaction_family_source_key",
            ),
        ),
    ]
