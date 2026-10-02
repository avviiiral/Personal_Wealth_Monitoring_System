from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0105_transactionupload"),
    ]

    operations = [
        migrations.AddField(
            model_name="assetunderlyingholding",
            name="uploaded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="uploaded_asset_underlyings",
                to="auth.user",
            ),
        ),
        migrations.AddField(
            model_name="assetunderlyingholding",
            name="uploaded_at",
            field=models.DateTimeField(auto_now_add=True, null=False),
            preserve_default=True,
        ),
    ]
