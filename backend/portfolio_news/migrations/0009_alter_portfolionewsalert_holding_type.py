from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("portfolio_news", "0008_exchange_filing_alerts")]
    operations = [
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="holding_type",
            field=models.CharField(choices=[("EQUITY", "Equity"), ("MUTUAL_FUND", "Mutual Fund"), ("WATCHLIST", "Watchlist")], max_length=20),
        ),
    ]
