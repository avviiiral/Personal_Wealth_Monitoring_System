from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("investments", "0001_initial"),
        ("users", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssetUnderlyingHolding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("stock_name", models.CharField(max_length=300)),
                ("holding_percentage", models.DecimalField(decimal_places=4, max_digits=10)),
                ("sector", models.CharField(blank=True, max_length=150, null=True)),
                ("cap_type", models.CharField(blank=True, max_length=50, null=True)),
                ("source", models.CharField(default="EXCEL", max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="underlying_holdings", to="investments.asset")),
                ("family", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="asset_underlying_holdings", to="users.familygroup")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="asset_underlying_holdings", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-holding_percentage", "stock_name"],
                "indexes": [
                    models.Index(fields=["family", "asset"], name="au_family_asset_idx"),
                    models.Index(fields=["asset", "stock_name"], name="au_asset_stock_idx"),
                ],
            },
        ),
    ]
