from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0007_portfolionewsalert_family_group"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="portfolionewsalert",
            new_name="news_alert_usr_fam_created",
            old_name="news_alert_user_family_created_idx",
        ),
        migrations.RenameIndex(
            model_name="portfolionewsalert",
            new_name="news_alert_usr_fam_unread",
            old_name="news_alert_user_family_unread_idx",
        ),
        migrations.RenameIndex(
            model_name="portfolionewsalert",
            new_name="news_alert_usr_fam_tier",
            old_name="news_alert_user_family_tier_idx",
        ),
    ]
