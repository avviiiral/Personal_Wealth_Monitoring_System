from decimal import Decimal

from rest_framework import serializers

from mutual_funds.models import (
    MutualFundHolding,
    MutualFundTransaction,
    MutualFundScheme,
    SIP,
    SIPInstallment,
)


class MutualFundHoldingSerializer(
    serializers.ModelSerializer
):

    scheme_name = serializers.CharField(
        source="scheme.scheme_name",
        read_only=True,
    )

    scheme_code = serializers.CharField(
        source="scheme.scheme_code",
        read_only=True,
    )

    amc_name = serializers.CharField(
        source="scheme.amc_name",
        read_only=True,
    )

    pnl_percentage = serializers.SerializerMethodField()

    class Meta:
        model = MutualFundHolding

        fields = [
            "id",
            "scheme",
            "scheme_name",
            "scheme_code",
            "amc_name",
            "units",
            "invested_value",
            "average_nav",
            "current_nav",
            "current_value",
            "unrealized_pnl",
            "pnl_percentage",
            "updated_at",
        ]

    def get_pnl_percentage(self, obj):

        if not obj.invested_value:
            return 0

        return round(
            float(
                (
                    obj.unrealized_pnl
                    / obj.invested_value
                ) * 100
            ),
            2,
        )


class MutualFundTransactionSerializer(
    serializers.ModelSerializer
):

    scheme_name = serializers.CharField(
        source="scheme.scheme_name",
        read_only=True,
    )

    transaction_type_display = serializers.CharField(
        source="get_transaction_type_display",
        read_only=True,
    )

    class Meta:
        model = MutualFundTransaction

        fields = [
            "id",
            "scheme",
            "scheme_name",
            "transaction_type",
            "transaction_type_display",
            "transaction_date",
            "units",
            "nav",
            "amount",
            "fees",
            "notes",
            "created_at",
        ]


class SIPSerializer(
    serializers.ModelSerializer
):

    scheme_name = serializers.CharField(
        source="scheme.scheme_name",
        read_only=True,
    )

    frequency_display = serializers.CharField(
        source="get_frequency_display",
        read_only=True,
    )

    status = serializers.SerializerMethodField()

    due_count = serializers.SerializerMethodField()

    monthly_commitment = serializers.SerializerMethodField()

    class Meta:
        model = SIP

        fields = [
            "id",
            "scheme",
            "scheme_name",
            "amount",
            "frequency",
            "frequency_display",
            "start_date",
            "end_date",
            "next_installment_date",
            "is_active",
            "status",
            "due_count",
            "monthly_commitment",
            "created_at",
            "updated_at",
        ]

    def get_status(self, obj):

        from .services.sip_engine import SIPEngine

        return SIPEngine.get_sip_status(obj)["status"]

    def get_due_count(self, obj):

        from .services.sip_engine import SIPEngine

        return SIPEngine.get_sip_status(obj)["due_count"]

    def get_monthly_commitment(self, obj):

        if obj.frequency == "MONTHLY":

            return obj.amount

        if obj.frequency == "WEEKLY":

            return (
                obj.amount
                * Decimal("52")
                / Decimal("12")
            )

        if obj.frequency == "QUARTERLY":

            return (
                obj.amount
                / Decimal("3")
            )

        if obj.frequency == "YEARLY":

            return (
                obj.amount
                / Decimal("12")
            )

        return obj.amount


class SIPInstallmentSerializer(
    serializers.ModelSerializer
):

    sip_id = serializers.IntegerField(
        source="sip.id",
        read_only=True,
    )

    scheme_name = serializers.CharField(
        source="sip.scheme.scheme_name",
        read_only=True,
    )

    frequency = serializers.CharField(
        source="sip.frequency",
        read_only=True,
    )

    frequency_display = serializers.CharField(
        source="sip.get_frequency_display",
        read_only=True,
    )

    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    transaction_id = serializers.IntegerField(
        source="transaction.id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = SIPInstallment

        fields = [
            "id",
            "sip_id",
            "scheme_name",
            "frequency",
            "frequency_display",
            "scheduled_date",
            "amount",
            "status",
            "status_display",
            "transaction_id",
            "executed_at",
            "notes",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "sip_id",
            "scheme_name",
            "frequency",
            "frequency_display",
            "status",
            "status_display",
            "transaction_id",
            "executed_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        
class MutualFundSchemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MutualFundScheme

        fields = [
            "id",
            "scheme_name",
            "amc_name",
            "scheme_code",
            "isin_growth",
            "isin_dividend",
            "plan",
            "option",
            "category",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def validate_scheme_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Scheme name cannot be empty."
            )

        return value


class CreateMutualFundTransactionSerializer(
    serializers.ModelSerializer
):
    class Meta:
        model = MutualFundTransaction

        fields = [
            "id",
            "scheme",
            "transaction_type",
            "transaction_date",
            "units",
            "nav",
            "amount",
            "fees",
            "notes",
            "created_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
        ]

    def validate_scheme(self, scheme):
        request = self.context.get("request")

        if request is None or not request.user.is_authenticated:
            raise serializers.ValidationError(
                "Authentication is required."
            )

        if scheme.owner_id != request.user.id:
            raise serializers.ValidationError(
                "You can only use your own mutual fund schemes."
            )

        if not scheme.is_active:
            raise serializers.ValidationError(
                "Cannot create a transaction for an inactive scheme."
            )

        return scheme

    def validate_units(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Units cannot be negative."
            )

        return value

    def validate_nav(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "NAV cannot be negative."
            )

        return value

    def validate_amount(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Amount cannot be negative."
            )

        return value

    def validate_fees(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Fees cannot be negative."
            )

        return value


class CreateSIPSerializer(serializers.ModelSerializer):
    class Meta:
        model = SIP

        fields = [
            "id",
            "scheme",
            "amount",
            "frequency",
            "start_date",
            "end_date",
            "next_installment_date",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def validate_scheme(self, scheme):
        request = self.context.get("request")

        if request is None or not request.user.is_authenticated:
            raise serializers.ValidationError(
                "Authentication is required."
            )

        if scheme.owner_id != request.user.id:
            raise serializers.ValidationError(
                "You can only use your own mutual fund schemes."
            )

        if not scheme.is_active:
            raise serializers.ValidationError(
                "Cannot create a SIP for an inactive scheme."
            )

        return scheme

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "SIP amount must be greater than zero."
            )

        return value

    def validate_end_date(self, value):
        start_date = self.initial_data.get("start_date")

        if value and start_date and str(value) < str(start_date):
            raise serializers.ValidationError(
                "End date cannot be before start date."
            )

        return value