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
        return metric_snapshot.date if metric_snapshot else None

    def get_metrics(self, obj):
        latest = self._latest_snapshot(obj)
        metric_snapshot = self._metric_snapshot(obj)
        if not latest and not metric_snapshot:
            return {}
        fields = (
            ("1D", "return_1d"),
            ("1W", "return_1w"),
            ("1M", "return_1m"),
            ("3M", "return_3m"),
            ("6M", "return_6m"),
            ("1Y", "return_1y"),
            ("3Y", "return_3y"),
            ("5Y", "return_5y"),
            ("Since Inception", "return_since_inception"),
            ("CAGR", "cagr"),
        )
        return {
            key: next(
                (
                    getattr(snapshot, field)
                    for snapshot in (latest, metric_snapshot)
                    if snapshot is not None and getattr(snapshot, field, None) is not None
                ),
                None,
            )
            for key, field in fields
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
            "fund_manager_website": value.fund_manager_website,
            "risk_level": value.risk_level,
            "minimum_investment": value.minimum_investment,
            "exit_load": value.exit_load,
            "latest_nav": value.latest_nav,
            "latest_nav_date": value.latest_nav_date,
            "research_metrics": self._research_metrics(obj),
        }

    def _research_metrics(self, obj):
        snapshot = self.context.get("masterdata_snapshots", {}).get(obj.id)
        if snapshot is None:
            return None

        tracked_fields = (
            "aum_cr", "ter_direct_pct", "ter_regular_pct", "std_dev_pct",
            "sharpe", "sortino", "beta", "alpha_pct", "max_drawdown_pct",
        )
        field_sources = snapshot.field_sources or {}
        return {
            "as_of_date": snapshot.as_of_date,
            "aum_cr": snapshot.aum_cr,
            "aum_type": snapshot.aum_type,
            "aum_as_of": snapshot.aum_as_of,
            "aum_source": snapshot.aum_source,
            "ter_direct_pct": snapshot.ter_direct_pct,
            "ter_regular_pct": snapshot.ter_regular_pct,
            "ter_as_of": snapshot.ter_as_of,
            "std_dev_pct": snapshot.std_dev_pct,
            "sharpe": snapshot.sharpe,
            "sortino": snapshot.sortino,
            "beta": snapshot.beta,
            "alpha_pct": snapshot.alpha_pct,
            "max_drawdown_pct": snapshot.max_drawdown_pct,
            "risk_free_rate_used_pct": snapshot.risk_free_rate_used_pct,
            "ratio_window_years": snapshot.ratio_window_years,
            "data_quality_flags": snapshot.data_quality_flags or [],
            "risk_methodology": field_sources.get("std_dev_pct", {}).get("methodology", {}),
            "field_sources": {
                field: field_sources[field]
                for field in tracked_fields
                if field in field_sources
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
