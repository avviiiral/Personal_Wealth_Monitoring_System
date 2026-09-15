from rest_framework import serializers

from .models import MutualFundUnderlying


class MutualFundUnderlyingSerializer(serializers.ModelSerializer):
    scheme_name = serializers.CharField(source="scheme.scheme_name", read_only=True)
    scheme_code = serializers.CharField(source="scheme.scheme_code", read_only=True)

    class Meta:
        model = MutualFundUnderlying
        fields = [
            "id",
            "scheme",
            "scheme_name",
            "scheme_code",
            "security_name",
            "isin",
            "quantity",
            "market_value",
            "percentage_of_nav",
            "portfolio_date",
            "source",
            "source_reference",
            "fetched_at",
        ]
