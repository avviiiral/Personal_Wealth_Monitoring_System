# Generated for the corporate-actions management foundation.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="CorporateAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action_type", models.CharField(choices=[
                    ("STOCK_SPLIT", "Stock split"), ("REVERSE_SPLIT", "Reverse stock split"),
                    ("BONUS", "Bonus issue"), ("STOCK_DIVIDEND", "Stock dividend"),
                    ("CASH_DIVIDEND", "Cash dividend"), ("MERGER", "Merger / amalgamation"),
                    ("DEMERGER", "Demerger / spin-off"), ("RIGHTS_ISSUE", "Rights issue"),
                    ("BUYBACK", "Buyback"), ("TENDER_OFFER", "Tender offer"),
                    ("CASH_IN_LIEU", "Cash in lieu"), ("WARRANT_CONVERSION", "Warrant conversion"),
                    ("ADR_RATIO_CHANGE", "ADR/GDR ratio change"), ("TICKER_CHANGE", "Ticker change"),
                    ("ISIN_CHANGE", "ISIN change"), ("DELISTING", "Delisting / suspension"),
                    ("MF_DISTRIBUTION", "Mutual fund distribution / IDCW"),
                    ("MF_OPTION_CHANGE", "Mutual fund plan / option change"),
                    ("MF_UNIT_ADJUSTMENT", "Mutual fund unit adjustment"),
                ], max_length=32)),
                ("security_name", models.CharField(max_length=255)),
                ("isin", models.CharField(blank=True, db_index=True, default="", max_length=20)),
                ("symbol", models.CharField(blank=True, db_index=True, default="", max_length=50)),
                ("exchange", models.CharField(blank=True, default="", max_length=20)),
                ("ex_date", models.DateField()),
                ("record_date", models.DateField(blank=True, null=True)),
                ("payment_date", models.DateField(blank=True, null=True)),
                ("old_units", models.DecimalField(blank=True, decimal_places=8, max_digits=20, null=True)),
                ("new_units", models.DecimalField(blank=True, decimal_places=8, max_digits=20, null=True)),
                ("cash_per_unit", models.DecimalField(blank=True, decimal_places=8, max_digits=20, null=True)),
                ("resulting_isin", models.CharField(blank=True, default="", max_length=20)),
                ("resulting_symbol", models.CharField(blank=True, default="", max_length=50)),
                ("source", models.CharField(blank=True, default="MANUAL", max_length=40)),
                ("source_reference", models.URLField(blank=True, default="", max_length=1000)),
                ("status", models.CharField(choices=[("PENDING", "Pending review"), ("CONFIRMED", "Confirmed"), ("REJECTED", "Rejected")], default="PENDING", max_length=12)),
                ("notes", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="corporate_actions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-ex_date", "security_name"]},
        ),
        migrations.AddConstraint(
            model_name="corporateaction",
            constraint=models.UniqueConstraint(condition=~Q(isin=""), fields=("owner", "isin", "action_type", "ex_date"), name="uniq_corp_action_owner_isin_type_date"),
        ),
        migrations.AddIndex(model_name="corporateaction", index=models.Index(fields=["owner", "status", "ex_date"], name="corp_action_review_idx")),
        migrations.AddIndex(model_name="corporateaction", index=models.Index(fields=["isin", "ex_date"], name="corp_action_isin_date_idx")),
        migrations.AddIndex(model_name="corporateaction", index=models.Index(fields=["symbol", "exchange", "ex_date"], name="corp_action_symbol_idx")),
    ]
