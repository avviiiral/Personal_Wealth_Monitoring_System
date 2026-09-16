from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("watchlist", "0003_merge_0002"),
    ]

    operations = [
        migrations.CreateModel(
            name="WatchListEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="watchlist_entries", to="watchlist.investmentproduct")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="watchlist_entries", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="watchlistentry",
            index=models.Index(fields=["user", "product"], name="watchlist_w_user_id_6d17ab_idx"),
        ),
        migrations.AddConstraint(
            model_name="watchlistentry",
            constraint=models.UniqueConstraint(fields=("user", "product"), name="unique_user_watchlist_entry"),
        ),
    ]
