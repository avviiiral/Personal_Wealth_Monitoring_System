from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0007_portfolionewsalert_family_group"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                (
                    "ALTER INDEX news_alert_user_family_created_idx "
                    "RENAME TO news_alert_usr_fam_created"
                ),
                (
                    "ALTER INDEX news_alert_user_family_unread_idx "
                    "RENAME TO news_alert_usr_fam_unread"
                ),
                (
                    "ALTER INDEX news_alert_user_family_tier_idx "
                    "RENAME TO news_alert_usr_fam_tier"
                ),
            ],
            reverse_sql=[
                (
                    "ALTER INDEX news_alert_usr_fam_created "
                    "RENAME TO news_alert_user_family_created_idx"
                ),
                (
                    "ALTER INDEX news_alert_usr_fam_unread "
                    "RENAME TO news_alert_user_family_unread_idx"
                ),
                (
                    "ALTER INDEX news_alert_usr_fam_tier "
                    "RENAME TO news_alert_user_family_tier_idx"
                ),
            ],
        ),
    ]
