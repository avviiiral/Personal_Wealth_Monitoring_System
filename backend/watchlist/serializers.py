from rest_framework import serializers

from mf_intelligence.models import MFScheme
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
    metrics_as_of = serializers.SerializerMethodField()
    mutual_fund = serializers.SerializerMethodField()
    pms = serializers.SerializerMethodField()
    is_watchlisted = serializers.SerializerMethodField()
    benchmark = serializers.SerializerMethodField()
    comparison_benchmark = serializers.SerializerMethodField()

    class Meta:
        model = InvestmentProduct
        fields = [
            "id", "product_type", "name", "provider", "country", "category", "sub_category",
            "isin", "external_identifier", "currency", "source", "source_reference", "source_date",
            "official_website", "benchmark", "comparison_benchmark", "status", "ownership",
            "performance", "metrics", "metrics_as_of", "mutual_fund", "pms", "is_watchlisted",
        ]

    def get_benchmark(self, obj):
        if obj.product_type == ProductType.MUTUAL_FUND and hasattr(obj, "mutual_fund"):
            return obj.mutual_fund.benchmark
        if obj.product_type == ProductType.PMS and hasattr(obj, "pms"):
            return obj.pms.benchmark
        return None

    def get_comparison_benchmark(self, obj):
        return obj.comparison_benchmark or self.get_benchmark(obj)

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

    def _metric_snapshot(self, obj):
        metric_snapshots = self.context.get("metric_snapshots", {})
        return metric_snapshots.get(obj.id) or self._latest_snapshot(obj)

    def _mf_intelligence_scheme(self, obj):
        if obj.product_type != ProductType.MUTUAL_FUND:
            return None
        scheme_code = None
        if hasattr(obj, "mutual_fund"):
            scheme_code = obj.mutual_fund.scheme_code
        scheme_code = str(scheme_code or obj.external_identifier or "").strip()
        if not scheme_code:
            return None
        cached = self.context.get("mf_intelligence_schemes")
        if cached is not None:
            return cached.get(scheme_code)
        return (
            MFScheme.objects.filter(amfi_code=scheme_code)
            .select_related("category")
            .prefetch_related("metrics")
            .first()
        )

    def get_status(self, obj):
        if self.get_is_watchlisted(obj):
            return "WATCHLIST"
        return self._ownership(obj)["status"]

    def get_ownership(self, obj):
        return self._ownership(obj)["ownership"]

    def get_performance(self, obj):
        latest = self._latest_snapshot(obj)
        metric_snapshot = self._metric_snapshot(obj)
        snapshots = []
        for snapshot in (latest, metric_snapshot):
            if snapshot is not None and all(existing.pk != snapshot.pk for existing in snapshots):
                snapshots.append(snapshot)
        return PerformanceSnapshotSerializer(snapshots, many=True).data

    def get_metrics_as_of(self, obj):
        metric_snapshot = self._metric_snapshot(obj)
        intelligence = self._mf_intelligence_scheme(obj)
        dates = []
        if metric_snapshot is not None:
            dates.append(metric_snapshot.date)
        if intelligence is not None:
            intelligence_date = intelligence.data_as_of or intelligence.latest_nav_date
            if intelligence_date is not None:
                dates.append(intelligence_date)
        return max(dates) if dates else None

    def get_metrics(self, obj):
        latest = self._latest_snapshot(obj)
        metric_snapshot = self._metric_snapshot(obj)
        intelligence = self._mf_intelligence_scheme(obj)
        if not latest and not metric_snapshot and not intelligence:
            return {}
        fields = (
            ("1D", "return_1d", None),
            ("1W", "return_1w", "return_1w"),
            ("1M", "return_1m", "return_1m"),
            ("3M", "return_3m", "return_3m"),
            ("6M", "return_6m", "return_6m"),
            ("1Y", "return_1y", "return_1y"),
            ("3Y", "return_3y", "return_3y"),
            ("5Y", "return_5y", "return_5y"),
            ("Since Inception", "return_since_inception", "return_since_inception"),
            ("CAGR", "cagr", None),
        )
        result = {}
        for key, snapshot_field, intelligence_field in fields:
            value = next(
                (
                    getattr(snapshot, snapshot_field)
                    for snapshot in (latest, metric_snapshot)
                    if snapshot is not None and getattr(snapshot, snapshot_field, None) is not None
                ),
                None,
            )
            if value is None and intelligence is not None and intelligence_field:
                value = getattr(intelligence, intelligence_field, None)
            result[key] = value
        return result

    def get_mutual_fund(self, obj):
        if obj.product_type != ProductType.MUTUAL_FUND:
            return None
        value = obj.mutual_fund if hasattr(obj, "mutual_fund") else None
        intelligence = self._mf_intelligence_scheme(obj)
        if value is None and intelligence is None:
            return None

        child_nav_date = value.latest_nav_date if value else None
        intelligence_is_newer = bool(
            intelligence
            and intelligence.latest_nav_date
            and (child_nav_date is None or intelligence.latest_nav_date > child_nav_date)
        )
        risk_metrics = {}
        if intelligence is not None:
            metric_fields = (
                "trailing_cagr", "avg_rolling_return", "rolling_return_p10",
                "rolling_return_p25", "rolling_return_p50", "rolling_return_p75",
                "rolling_return_p90", "rolling_return_sd", "trailing_xirr",
                "amfi_volatility", "beta", "correlation", "alpha", "upside_capture",
                "downside_capture", "sharpe_ratio", "sortino_ratio", "information_ratio",
                "trailing_max_drawdown",
            )
            risk_metrics = {
                metric.horizon: {field: getattr(metric, field) for field in metric_fields}
                for metric in intelligence.metrics.all()
            }
        child_nav = value.latest_nav if value else None
        child_aum = value.aum if value else None
        child_ter = value.expense_ratio if value else None
        return {
            "scheme_code": (value.scheme_code if value else None) or (intelligence.amfi_code if intelligence else None),
            "fund_type": (value.fund_type if value else None) or (intelligence.category.name if intelligence and intelligence.category else None),
            "plan": (value.plan if value else None) or (intelligence.plan if intelligence else None),
            "option": (value.option if value else None) or (intelligence.option if intelligence else None),
            "inception_date": (value.inception_date if value else None) or (intelligence.first_nav_date if intelligence else None),
            "aum": child_aum if child_aum is not None else (intelligence.avg_aum_crore if intelligence else None),
            "aum_period": intelligence.aum_period if intelligence else None,
            "expense_ratio": child_ter if child_ter is not None else (intelligence.ter if intelligence else None),
            "benchmark": value.benchmark if value else None,
            "fund_manager": value.fund_manager if value else None,
            "fund_manager_website": value.fund_manager_website if value else None,
            "risk_level": value.risk_level if value else None,
            "minimum_investment": value.minimum_investment if value else None,
            "exit_load": value.exit_load if value else None,
            "latest_nav": (
                intelligence.latest_nav
                if intelligence_is_newer
                else (child_nav if child_nav is not None else (intelligence.latest_nav if intelligence else None))
            ),
            "latest_nav_date": (
                intelligence.latest_nav_date
                if intelligence_is_newer
                else (child_nav_date or (intelligence.latest_nav_date if intelligence else None))
            ),
            "intelligence": {
                "category": intelligence.category.name if intelligence and intelligence.category else None,
                "asset_class": intelligence.category.asset_class if intelligence and intelligence.category else None,
                "data_as_of": intelligence.data_as_of if intelligence else None,
                "metrics_synced_at": intelligence.metrics_synced_at if intelligence else None,
                "aum_crore": intelligence.avg_aum_crore if intelligence else None,
                "ter": intelligence.ter if intelligence else None,
                "all_time_max_drawdown": intelligence.all_time_max_drawdown if intelligence else None,
                "returns": {
                    "1W": intelligence.return_1w,
                    "1M": intelligence.return_1m,
                    "3M": intelligence.return_3m,
                    "6M": intelligence.return_6m,
                    "YTD": intelligence.return_ytd,
                    "1Y": intelligence.return_1y,
                    "2Y": intelligence.return_2y,
                    "3Y": intelligence.return_3y,
                    "5Y": intelligence.return_5y,
                    "10Y": intelligence.return_10y,
                    "Since Inception": intelligence.return_since_inception,
                } if intelligence else {},
                "calendar_returns": intelligence.calendar_returns if intelligence else {},
                "risk_metrics": risk_metrics,
            },
        }

    def get_pms(self, obj):
        if obj.product_type != ProductType.PMS or not hasattr(obj, "pms"):
            return None
        value = obj.pms
        return {
            "fund_manager": value.fund_manager,
            "fund_manager_website": value.fund_manager_website,
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
