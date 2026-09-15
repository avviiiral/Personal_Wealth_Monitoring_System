from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0006_mutualfundunderlying"),
    ]

    operations = [
        migrations.AddField(
            model_name="mutualfundunderlying",
            name="sector",
            field=models.CharField(
                blank=True,
                help_text="Industry/sector exactly as disclosed by the official source, when available.",
                max_length=150,
                null=True,
            ),
        ),
    ]
