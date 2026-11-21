from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0015_financial_precision_4dp"),
    ]

    operations = [
        migrations.AddField(
            model_name="amfimasterscheme",
            name="amc_name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="raw_category_header",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="plan",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="option",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="level_1_branch",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="level_2_group",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="level_3_category",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="is_growth_plan",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="is_direct_plan_growth",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="sebi_universe_active",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="amfi_source_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="amfimasterscheme",
            name="universe_last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="amfimasterscheme",
            index=models.Index(
                fields=["level_1_branch", "level_3_category", "plan", "option"],
                name="amfi_sebi_tree_plan_idx",
            ),
        ),
    ]
