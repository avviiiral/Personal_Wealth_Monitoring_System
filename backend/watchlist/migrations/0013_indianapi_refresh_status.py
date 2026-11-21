from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("watchlist", "0012_indianapi_request_usage"),
    ]

    operations = [
        migrations.AddField(
            model_name="indianapirequestusage",
            name="refresh_status",
            field=models.CharField(
                choices=[
                    ("NEVER", "Never"),
                    ("IN_PROGRESS", "In progress"),
                    ("SUCCESS", "Success"),
                    ("FAILED", "Failed"),
                    ("RATE_LIMITED", "Rate limited"),
                ],
                default="NEVER",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="indianapirequestusage",
            name="last_refresh_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="indianapirequestusage",
            name="last_refresh_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="indianapirequestusage",
            name="last_refresh_error",
            field=models.TextField(blank=True, default=""),
        ),
    ]
