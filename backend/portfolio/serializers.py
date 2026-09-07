from rest_framework import serializers

from investments.models import (
    Asset,
    Holding,
    Transaction,
)

from market_data.models import (
    ManualAssetPrice,
    MarketPrice,
)


class AssetSerializer(
    serializers.ModelSerializer
):

    class Meta:
        model = Asset
        fields = "__all__"

        read_only_fields = (
            "id",
            "owner",
            "created_at",
            "updated_at",
        )


class HoldingSerializer(
    serializers.ModelSerializer
):

    asset_name = serializers.CharField(
        source="asset.name",
        read_only=True,
    )

    asset_category_display = serializers.CharField(
        source="asset.get_category_display",
        read_only=True,
    )

    isin = serializers.CharField(
        source="asset.isin",
        read_only=True,
    )

    price_source = serializers.SerializerMethodField()

    price_updated_date = (
        serializers.SerializerMethodField()
    )

    manual_price_available = (
        serializers.SerializerMethodField()
    )

    manual_price = (
        serializers.SerializerMethodField()
    )

    pnl_percentage = serializers.SerializerMethodField()

    class Meta:
        model = Holding

        fields = (
            "id",
            "asset",
            "asset_name",
            "asset_category_display",
            "isin",
            "quantity",
            "average_cost",
            "invested_value",
            "current_price",
            "current_value",
            "unrealized_pnl",
            "pnl_percentage",
            "updated_at",

            # Manual/effective price metadata
            "price_source",
            "price_updated_date",
            "manual_price_available",
            "manual_price",
        )

        read_only_fields = (
            "id",
            "owner",
            "invested_value",
            "current_value",
            "unrealized_pnl",
            "pnl_percentage",
            "updated_at",
            "price_source",
            "price_updated_date",
            "manual_price_available",
            "manual_price",
        )

    def _latest_automatic_price(
        self,
        obj,
    ):
        return (
            MarketPrice.objects
            .filter(
                asset=obj.asset,
            )
            .order_by("-date")
            .first()
        )

    def _manual_price(
        self,
        obj,
    ):
        return (
            ManualAssetPrice.objects
            .filter(
                asset=obj.asset,
            )
            .first()
        )

    def get_price_source(
        self,
        obj,
    ):
        automatic = (
            self._latest_automatic_price(
                obj
            )
        )

        if automatic is not None:
            return automatic.source

        manual = (
            self._manual_price(
                obj
            )
        )

        if manual is not None:
            return "MANUAL"

        return None

    def get_price_updated_date(
        self,
        obj,
    ):
        automatic = (
            self._latest_automatic_price(
                obj
            )
        )

        if automatic is not None:
            return str(
                automatic.date
            )

        manual = (
            self._manual_price(
                obj
            )
        )

        if manual is not None:
            return str(
                manual.price_date
            )

        return None

    def get_manual_price(
        self,
        obj,
    ):
        manual = (
            self._manual_price(
                obj
            )
        )

        if manual is None:
            return None

        return str(
            manual.price
        )

    def get_manual_price_available(
        self,
        obj,
    ):
        """
        Manual editing is available only when
        automatic market data does not exist.
        """

        automatic = (
            self._latest_automatic_price(
                obj
            )
        )

        return automatic is None

    def get_pnl_percentage(
        self,
        obj,
    ):
        if not obj.invested_value:
            return 0

        return round(
            float(
                obj.unrealized_pnl
                / obj.invested_value
                * 100
            ),
            2,
        )


class TransactionSerializer(
    serializers.ModelSerializer
):

    asset_name_display = serializers.CharField(
        source="asset.name",
        read_only=True,
    )

    isin = serializers.CharField(
        source="asset.isin",
        read_only=True,
    )

    class Meta:
        model = Transaction

        fields = (
            "id",
            "asset",
            "asset_name_display",
            "isin",
            "family_name",
            "portfolio",
            "asset_class",
            "sub_class",
            "asset_name",
            "underlying",
            "advisors",
            "transaction_date",
            "transaction_type",
            "quantity",
            "price_per_unit",
            "amount",
            "fees",
            "source",
            "source_key",
            "created_at",
        )

        read_only_fields = (
            "id",
            "owner",
            "created_at",
            "updated_at",
        )

    def validate_asset(
        self,
        asset,
    ):

        request = self.context.get(
            "request"
        )

        if request is None:
            return asset

        if asset.owner_id != request.user.id:
            raise serializers.ValidationError(
                "Invalid asset."
            )

        return asset