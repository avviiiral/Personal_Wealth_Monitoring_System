# Generated for AMFI scheme-wise quarterly AAUM snapshots.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("mutual_funds", "0017_expand_amfi_scheme_option_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="AMFISchemeAAUM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quarter_start", models.DateField()),
                ("quarter_label", models.CharField(max_length=40)),
                ("average_aum_cr", models.DecimalField(decimal_places=4, max_digits=22)),
                ("source", models.CharField(default="AMFI_SCHEME_AAUM", max_length=40)),
                ("source_scheme_name", models.CharField(blank=True, default="", max_length=300)),
                ("imported_at", models.DateTimeField(auto_now=True)),
                ("scheme", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="aaum_snapshots", to="mutual_funds.amfimasterscheme")),
            ],
            options={
                "ordering": ["-quarter_start", "-average_aum_cr"],
                "indexes": [
                    models.Index(fields=["quarter_start", "scheme"], name="mf_aaum_qtr_scheme_idx"),
                    models.Index(fields=["scheme", "-quarter_start"], name="mf_aaum_scheme_qtr_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("scheme", "quarter_start", "source"), name="unique_amfi_scheme_aaum_quarter"),
                ],
            },
        ),
    ]
