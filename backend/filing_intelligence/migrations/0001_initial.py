from django.db import migrations,models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial=True
    dependencies=[("investments","0008_securitymaster_peg_ratio")]
    operations=[
        migrations.CreateModel(name="Filing",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("exchange",models.CharField(choices=[("NSE","NSE"),("BSE","BSE")],max_length=10)),
            ("company_name",models.CharField(max_length=300)),("symbol",models.CharField(blank=True,default="",max_length=100)),("isin",models.CharField(blank=True,default="",max_length=30)),("bse_code",models.CharField(blank=True,default="",max_length=30)),("filing_type",models.CharField(blank=True,default="",max_length=150)),("subject",models.CharField(max_length=1000)),("details",models.TextField(blank=True,default="")),("filing_url",models.URLField(max_length=1500)),("source_url",models.URLField(blank=True,default="",max_length=1500)),("external_filing_id",models.CharField(blank=True,default="",max_length=255)),("published_at",models.DateTimeField()),("received_at",models.DateTimeField(auto_now_add=True)),("content_hash",models.CharField(max_length=64)),
            ("processing_status",models.CharField(choices=[("PENDING","Pending"),("PROCESSED","Processed"),("UNMATCHED","Unmatched"),("FAILED","Failed")],default="PENDING",max_length=20)),("processing_error",models.TextField(blank=True,default="")),("event_type",models.CharField(blank=True,default="",max_length=80)),("severity",models.CharField(choices=[("INFO","Info"),("LOW","Low"),("MEDIUM","Medium"),("HIGH","High"),("CRITICAL","Critical")],default="INFO",max_length=20)),("classification_reason",models.TextField(blank=True,default="")),("classification_facts",models.JSONField(blank=True,default=list)),("created_at",models.DateTimeField(auto_now_add=True)),("updated_at",models.DateTimeField(auto_now=True)),
            ("security",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="exchange_filings",to="investments.asset")),
        ],options={"ordering":["-published_at","-created_at"]}),
        migrations.AddConstraint(model_name="filing",constraint=models.UniqueConstraint(condition=~models.Q(external_filing_id=""),fields=("exchange","external_filing_id"),name="filing_exchange_external_id_uniq")),
        migrations.AddConstraint(model_name="filing",constraint=models.UniqueConstraint(fields=("exchange","content_hash"),name="filing_exchange_content_hash_uniq")),
        migrations.AddIndex(model_name="filing",index=models.Index(fields=["exchange","published_at"],name="filing_exchange_pub_idx")),
        migrations.AddIndex(model_name="filing",index=models.Index(fields=["symbol","exchange"],name="filing_symbol_exchange_idx")),
        migrations.AddIndex(model_name="filing",index=models.Index(fields=["isin"],name="filing_isin_idx")),
        migrations.AddIndex(model_name="filing",index=models.Index(fields=["security","published_at"],name="filing_security_pub_idx")),
        migrations.AddIndex(model_name="filing",index=models.Index(fields=["event_type","severity"],name="filing_event_severity_idx")),
        migrations.AddIndex(model_name="filing",index=models.Index(fields=["processing_status","published_at"],name="filing_processing_idx")),
    ]
