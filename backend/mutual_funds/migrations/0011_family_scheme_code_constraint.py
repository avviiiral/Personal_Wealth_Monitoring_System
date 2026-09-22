from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0010_family_transaction_idempotency_schema"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="mutualfundscheme",
            name="unique_mf_scheme_family_code",
        ),
        migrations.AddConstraint(
            model_name="mutualfundscheme",
            constraint=models.UniqueConstraint(
                fields=("family", "scheme_code"),
                name="unique_mf_scheme_family_code",
            ),
        ),
    ]
