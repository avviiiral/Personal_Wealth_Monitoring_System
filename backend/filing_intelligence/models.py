from django.db import models

class FilingExchange(models.TextChoices):
    NSE = "NSE", "NSE"
    BSE = "BSE", "BSE"

class FilingSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"

class FilingProcessingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSED = "PROCESSED", "Processed"
    UNMATCHED = "UNMATCHED", "Unmatched"
    FAILED = "FAILED", "Failed"

class Filing(models.Model):
    exchange = models.CharField(max_length=10, choices=FilingExchange.choices)
    security = models.ForeignKey("investments.Asset", on_delete=models.SET_NULL, null=True, blank=True, related_name="exchange_filings")
    company_name = models.CharField(max_length=300)
    symbol = models.CharField(max_length=100, blank=True, default="")
    isin = models.CharField(max_length=30, blank=True, default="")
    bse_code = models.CharField(max_length=30, blank=True, default="")
    filing_type = models.CharField(max_length=150, blank=True, default="")
    subject = models.CharField(max_length=1000)
    details = models.TextField(blank=True, default="")
    filing_url = models.URLField(max_length=1500)
    source_url = models.URLField(max_length=1500, blank=True, default="")
    external_filing_id = models.CharField(max_length=255, blank=True, default="")
    published_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    content_hash = models.CharField(max_length=64)
    processing_status = models.CharField(max_length=20, choices=FilingProcessingStatus.choices, default=FilingProcessingStatus.PENDING)
    processing_error = models.TextField(blank=True, default="")
    event_type = models.CharField(max_length=80, blank=True, default="")
    severity = models.CharField(max_length=20, choices=FilingSeverity.choices, default=FilingSeverity.INFO)
    classification_reason = models.TextField(blank=True, default="")
    classification_facts = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["exchange", "external_filing_id"], condition=~models.Q(external_filing_id=""), name="filing_exchange_external_id_uniq"),
            models.UniqueConstraint(fields=["exchange", "content_hash"], name="filing_exchange_content_hash_uniq"),
        ]
        indexes = [
            models.Index(fields=["exchange", "published_at"], name="filing_exchange_pub_idx"),
            models.Index(fields=["symbol", "exchange"], name="filing_symbol_exchange_idx"),
            models.Index(fields=["isin"], name="filing_isin_idx"),
            models.Index(fields=["security", "published_at"], name="filing_security_pub_idx"),
            models.Index(fields=["event_type", "severity"], name="filing_event_severity_idx"),
            models.Index(fields=["processing_status", "published_at"], name="filing_processing_idx"),
        ]

    def __str__(self):
        return f"{self.exchange} {self.symbol or self.company_name}: {self.subject}"
