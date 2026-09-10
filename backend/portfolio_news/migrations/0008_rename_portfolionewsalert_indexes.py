from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0007_portfolionewsalert_family_group"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="portfolionewsalert",
            old_name="news_alert_user_family_created_idx",
            new_name="news_alert_usr_fam_created",
        ),
        migrations.RenameIndex(
            model_name="portfolionewsalert",
            old_name="news_alert_user_family_unread_idx",
            new_name="news_alert_usr_fam_unread",
        ),
        migrations.RenameIndex(
            model_name="portfolionewsalert",
            old_name="news_alert_user_family_tier_idx",
            new_name="news_alert_usr_fam_tier",
        ),
    ]
