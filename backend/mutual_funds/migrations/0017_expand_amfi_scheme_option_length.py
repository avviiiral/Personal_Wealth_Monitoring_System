from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0016_sebi_universe_metadata"),
    ]

    operations = [
        migrations.AlterField(
            model_name="amfimasterscheme",
            name="option",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
    ]
