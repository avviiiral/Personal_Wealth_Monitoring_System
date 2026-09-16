from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("watchlist", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="investmentproduct",
            index=models.Index(
                fields=["product_type", "category"],
                name="watch_product_category_idx",
            ),
        ),
    ]
