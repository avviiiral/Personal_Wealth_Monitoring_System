from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mutual_funds", "0023_mf_ingestion_pipeline"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mfschemeidentitymap",
            name="source",
            field=models.CharField(
                choices=[
                    ("AMFI_TER_NSDL", "AMFI TER NSDL scheme code"),
                    ("AMFI_RISK_SCHEME_ID", "AMFI risk-parameter SchemeID"),
                    ("AMFI_TRACKING_PARENT", "AMFI tracking-error parent scheme"),
                    ("CHARLIEALPHA", "CharlieAlpha API scheme identifier"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="mfexternalportfoliosnapshot",
            name="provider",
            field=models.CharField(
                choices=[
                    ("MFFORECAST", "MF Forecast"),
                    ("CHARLIEALPHA", "CharlieAlpha"),
                ],
                default="MFFORECAST",
                max_length=40,
            ),
        ),
    ]
