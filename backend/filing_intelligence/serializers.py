from rest_framework import serializers
from .models import Filing

class FilingSerializer(serializers.ModelSerializer):
    class Meta:
        model=Filing
        fields=["id","exchange","company_name","symbol","isin","filing_type","subject","filing_url","published_at","event_type","severity","processing_status"]
