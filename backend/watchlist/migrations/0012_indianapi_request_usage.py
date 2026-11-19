from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("watchlist", "0011_mutual_fund_research_report"),
    ]

    operations = [
        migrations.CreateModel(
            name="IndianAPIRequestUsage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("month", models.CharField(help_text="Calendar month in YYYY-MM format.", max_length=7, unique=True)),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("last_requested_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-month"]},
        ),
    ]
