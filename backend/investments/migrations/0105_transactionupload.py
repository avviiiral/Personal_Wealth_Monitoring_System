from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0104_family_transaction_order_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransactionUpload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_name", models.CharField(max_length=255)),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("imported_rows", models.PositiveIntegerField(default=0)),
                ("failed_rows", models.PositiveIntegerField(default=0)),
                ("duplicate_rows", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("PROCESSING", "Processing"), ("COMPLETED", "Completed"), ("PARTIAL", "Partial"), ("FAILED", "Failed")], default="PROCESSING", max_length=20)),
                ("error_message", models.TextField(blank=True, null=True)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("family", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transaction_uploads", to="users.familygroup")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="transaction_uploads", to="auth.user")),
            ],
            options={"ordering": ["-uploaded_at"], "indexes": [models.Index(fields=["family", "-uploaded_at"], name="tx_upload_family_date_idx"), models.Index(fields=["owner", "-uploaded_at"], name="tx_upload_owner_date_idx")]},
        ),
        migrations.CreateModel(
            name="TransactionUploadFailure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("row_number", models.PositiveIntegerField()),
                ("reason", models.TextField()),
                ("field_name", models.CharField(blank=True, max_length=100, null=True)),
                ("row_data", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("upload", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="failures", to="investments.transactionupload")),
            ],
            options={"ordering": ["row_number", "id"], "indexes": [models.Index(fields=["upload", "row_number"], name="tx_upload_failure_row_idx")]},
        ),
    ]
