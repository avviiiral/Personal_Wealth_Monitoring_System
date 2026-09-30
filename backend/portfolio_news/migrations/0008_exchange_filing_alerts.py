from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio_news", "0007_portfolionewsalert_digest_idx"),
        ("filing_intelligence", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfolionewsalert",
            name="source_type",
            field=models.CharField(choices=[("NEWS", "News"), ("EXCHANGE_FILING", "Exchange Filing")], db_index=True, default="NEWS", max_length=30),
        ),
        migrations.AddField(
            model_name="portfolionewsalert",
            name="filing",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="portfolio_news_alerts", to="filing_intelligence.filing"),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(fields=["user", "source_type", "-created_at"], name="news_alert_user_source_idx"),
        ),
    ]