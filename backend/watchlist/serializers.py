from rest_framework import serializers

from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType
from watchlist.services.ownership import OwnershipService


class PerformanceSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerformanceSnapshot
        fields = [
            "date", "nav_or_value", "aum", "return_1d", "return_1w", "return_1m",
            "return_3m", "return_6m", "return_1y", "return_3y", "return_5y",
            "return_since_inception", "cagr", "source", "source_reference", "fetched_at",
        ]


class WatchListProductSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    ownership = serializers.SerializerMethodField()
    performance = serializers.SerializerMethodField()
    metrics = serializers.SerializerMethodField()
    mutual_fund = serializers.SerializerMethodField()
    pms = serializers.SerializerMethodField()
    is_watchlisted = serializers.SerializerMethodField()

    class Meta:
        model = InvestmentProduct
        fields = [
            "id", "product_type", "name", "provider", "country", "category", "sub_category",
            "isin", "external_identifier", "currency", "source", "source_reference", "source_date",
            "official_website", "status", "ownership", "performance", "metrics", "mutual_fund", "pms",
            "is_watchlisted",
        ]

    def get_is_watchlisted(self, obj):
        watchlisted_ids = self.context.get("watchlisted_ids")
        if watchlisted_ids is None:
            return False
        return obj.id in watchlisted_ids

    def _ownership(self, obj):
        cache = self.context.setdefault("ownership_cache", {})
        if obj.id not in cache:
            request = self.context.get("request")
            cache[obj.id] = (
                OwnershipService.enrich(obj, request.user)
                if request and request.user.is_authenticated
                else {"status": "UNIVERSAL", "ownership": []}
            )
        return cache[obj.id]

    def _latest_snapshot(self, obj):
        snapshots = self.context.get("latest_snapshots", {})
        if obj.id in snapshots:
            return snapshots[obj.id]
        return obj.performance_snapshots.order_by("-date", "-id").first()

    def get_status(self, obj):
        # Watch List is an explicit user selection and takes precedence over
        # the ownership-derived status. This makes the checkmark immediately
        # represent the product's Watch List state.
        if self.get_is_watchlisted(obj):
            return "WATCHLIST"
        return self._ownership(obj)["status"]

    def get_ownership(self, obj):
        return self._ownership(obj)["ownership"]

    def get_performance(self, obj):
        latest = self._latest_snapshot(obj)
        return [PerformanceSnapshotSerializer(latest).data] if latest else []

    def get_metrics(self, obj):
        latest = self._latest_snapshot(obj)
        if not latest:
            return {}
        return {
            "1D": latest.return_1d,
            "1W": latest.return_1w,
            "1M": latest.return_1m,
            "3M": latest.return_3m,
            "6M": latest.return_6m,
            "1Y": latest.return_1y,
            "3Y": latest.return_3y,
            "5Y": latest.return_5y,
            "Since Inception": latest.return_since_inception,
            "CAGR": latest.cagr,
        }

    def get_mutual_fund(self, obj):
        if obj.product_type != ProductType.MUTUAL_FUND or not hasattr(obj, "mutual_fund"):
            return None
        value = obj.mutual_fund
        return {
            "scheme_code": value.scheme_code,
            "fund_type": value.fund_type,
            "plan": value.plan,
            "option": value.option,
            "inception_date": value.inception_date,
            "aum": value.aum,
            "expense_ratio": value.expense_ratio,
            "benchmark": value.benchmark,
            "fund_manager": value.fund_manager,
            "risk_level": value.risk_level,
            "minimum_investment": value.minimum_investment,
            "exit_load": value.exit_load,
            "latest_nav": value.latest_nav,
            "latest_nav_date": value.latest_nav_date,
        }

    def get_pms(self, obj):
        if obj.product_type != ProductType.PMS or not hasattr(obj, "pms"):
            return None
        value = obj.pms
        return {
            "strategy_name": value.strategy_name,
            "strategy_type": value.strategy_type,
            "asset_class": value.asset_class,
            "benchmark": value.benchmark,
            "aum": value.aum,
            "inception_date": value.inception_date,
            "minimum_investment": value.minimum_investment,
            "latest_value": value.latest_value,
            "risk_information": value.risk_information,
        }
