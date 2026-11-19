from datetime import date, timedelta
from decimal import Decimal

from django.db.models import OuterRef, Q, Subquery, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from watchlist.models import (
    BenchmarkMasterPoint,
    InvestmentProduct,
    PerformanceSnapshot,
    ProductType,
)


PERIODS = {
    "1M": ("return_1m", 30),
    "3M": ("return_3m", 90),
    "6M": ("return_6m", 182),
    "1Y": ("return_1y", 365),
    "3Y": ("return_3y", 1095),
    "5Y": ("return_5y", 1826),
}


def _benchmark_returns(benchmark, latest_date):
    if not benchmark:
        return {}

    points = list(
        BenchmarkMasterPoint.objects.filter(
            benchmark__iexact=benchmark,
            date__lte=latest_date,
        ).order_by("-date").values("date", "value")[:2000]
    )
    if not points:
        return {}

    latest = points[0]
    result = {"as_of": latest["date"].isoformat(), "periods": {}}
    for period, (_field, days) in PERIODS.items():
        target = latest["date"] - timedelta(days=days)
        historical = next((point for point in points if point["date"] <= target), None)
        if historical and historical["value"]:
            result["periods"][period] = (
                (Decimal(latest["value"]) / Decimal(historical["value"]) - Decimal("1"))
                * Decimal("100")
            )
        else:
            result["periods"][period] = None
    return result


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_analytics_overview(request):
    """Read-only peer analytics built from persisted Watch List MF snapshots.

    Returns are sourced from the latest available performance snapshot per
    scheme. Missing observations remain null and are excluded from averages
    and rankings; they are never coerced to zero.
    """
    params = request.query_params
    category = params.get("category", "").strip()
    provider = params.get("provider", "").strip()
    plan = params.get("plan", "").strip()
    benchmark = params.get("benchmark", "").strip()

    metric_snapshot = (
        PerformanceSnapshot.objects.filter(product_id=OuterRef("pk"))
        .exclude(
            Q(return_1m__isnull=True)
            & Q(return_3m__isnull=True)
            & Q(return_6m__isnull=True)
            & Q(return_1y__isnull=True)
            & Q(return_3y__isnull=True)
            & Q(return_5y__isnull=True)
        )
        .order_by("-date", "-id")
    )

    products = InvestmentProduct.objects.filter(
        product_type=ProductType.MUTUAL_FUND,
        is_active=True,
    ).select_related("mutual_fund")

    if category:
        products = products.filter(category__iexact=category)
    if provider:
        products = products.filter(provider__iexact=provider)
    if plan:
        products = products.filter(mutual_fund__plan__icontains=plan)

    products = products.annotate(
        metrics_as_of=Subquery(metric_snapshot.values("date")[:1]),
        metrics_source=Subquery(metric_snapshot.values("source")[:1]),
        return_1m=Subquery(metric_snapshot.values("return_1m")[:1]),
        return_3m=Subquery(metric_snapshot.values("return_3m")[:1]),
        return_6m=Subquery(metric_snapshot.values("return_6m")[:1]),
        return_1y=Subquery(metric_snapshot.values("return_1y")[:1]),
        return_3y=Subquery(metric_snapshot.values("return_3y")[:1]),
        return_5y=Subquery(metric_snapshot.values("return_5y")[:1]),
    ).order_by("name", "id")

    rows = []
    for product in products:
        mf = getattr(product, "mutual_fund", None)
        rows.append({
            "id": product.id,
            "name": product.name,
            "provider": product.provider,
            "category": product.category,
            "isin": product.isin,
            "scheme_code": mf.scheme_code if mf else None,
            "plan": mf.plan if mf else None,
            "option": mf.option if mf else None,
            "benchmark": product.comparison_benchmark or (mf.benchmark if mf else None),
            "aum": mf.aum if mf else None,
            "expense_ratio": mf.expense_ratio if mf else None,
            "as_of": product.metrics_as_of.isoformat() if product.metrics_as_of else None,
            "source": product.metrics_source,
            "returns": {
                period: getattr(product, field)
                for period, (field, _days) in PERIODS.items()
            },
        })

    periods = {}
    for period in PERIODS:
        available = [row["returns"][period] for row in rows if row["returns"][period] is not None]
        periods[period] = {
            "average_return": (
                sum((Decimal(value) for value in available), Decimal("0")) / len(available)
                if available else None
            ),
            "covered_funds": len(available),
            "total_funds": len(rows),
        }

    ranking_period = params.get("ranking_period", "1Y").upper()
    if ranking_period not in PERIODS:
        ranking_period = "1Y"
    ranked = [
        row for row in rows
        if row["returns"][ranking_period] is not None
    ]
    ranked.sort(key=lambda row: Decimal(row["returns"][ranking_period]), reverse=True)

    latest_date = max(
        (row["as_of"] for row in rows if row["as_of"]),
        default=None,
    )
    benchmark_data = _benchmark_returns(
        benchmark,
        date.fromisoformat(latest_date) if latest_date else timezone.now().date(),
    )

    total_aum = sum(
        (Decimal(row["aum"]) for row in rows if row["aum"] is not None),
        Decimal("0"),
    )
    categories = list(
        InvestmentProduct.objects.filter(
            product_type=ProductType.MUTUAL_FUND,
            is_active=True,
        ).exclude(category__isnull=True).exclude(category="")
        .values_list("category", flat=True).distinct().order_by("category")
    )
    providers = list(
        InvestmentProduct.objects.filter(
            product_type=ProductType.MUTUAL_FUND,
            is_active=True,
        ).exclude(provider__isnull=True).exclude(provider="")
        .values_list("provider", flat=True).distinct().order_by("provider")
    )

    return Response({
        "filters": {
            "category": category or None,
            "provider": provider or None,
            "plan": plan or None,
            "ranking_period": ranking_period,
            "categories": categories,
            "providers": providers,
        },
        "summary": {
            "fund_count": len(rows),
            "funds_with_metrics": sum(1 for row in rows if row["as_of"]),
            "aum": total_aum,
            "metrics_as_of": latest_date,
            "average_returns": periods,
        },
        "benchmark": {
            "name": benchmark or None,
            **benchmark_data,
        } if benchmark_data else {
            "name": benchmark or None,
            "available": False,
            "message": "Benchmark history is not available for the selected benchmark.",
        },
        "ranking_period": ranking_period,
        "rankings": ranked[:20],
        "funds": rows,
    })
