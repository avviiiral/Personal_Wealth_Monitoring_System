from django.db.models import F, OuterRef, Subquery

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import AssetUnderlyingHolding, PortfolioPosition, SecurityMaster, Transaction
from mutual_funds.models import MutualFundHolding, MutualFundUnderlying
from users.permissions import require_active_family


def _clean(value):
    return str(value or "").strip()


def _is_equity_pms(asset_class, sub_class):
    asset_class = _clean(asset_class).upper()
    sub_class = _clean(sub_class).upper()
    return (
        ("EQUITY" in asset_class or "EQUITY" in sub_class)
        and ("PMS" in asset_class or "PMS" in sub_class)
    )


def _is_direct_equity(asset_class, sub_class):
    asset_class = _clean(asset_class).upper()
    sub_class = _clean(sub_class).upper()
    return "DIRECT EQUITY" in asset_class or "DIRECT EQUITY" in sub_class


def _cap_bucket(cap_type):
    value = _clean(cap_type).upper()
    if "SMALL" in value:
        return "small_cap"
    if "MID" in value:
        return "mid_cap"
    if "LARGE" in value:
        return "large_cap"
    return "unclassified"


def _security_lookup(family_id):
    lookup = {}
    for security in SecurityMaster.objects.filter(family_id=family_id).values(
        "isin", "asset_name", "cap_type"
    ):
        if security["isin"]:
            lookup[("isin", _clean(security["isin"]).upper())] = security["cap_type"]
        if security["asset_name"]:
            lookup[("name", _clean(security["asset_name"]).upper())] = security["cap_type"]
    return lookup


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def equity_market_cap_report(request):
    """Return equity market-cap exposure with PMS and MF underlying look-through."""
    family = require_active_family(request.user)
    security_lookup = _security_lookup(family.id)

    latest_transaction = (
        Transaction.objects
        .filter(
            family_id=OuterRef("family_id"),
            asset_id=OuterRef("asset_id"),
            family_name=OuterRef("family_name"),
            portfolio=OuterRef("portfolio"),
        )
        .order_by("-transaction_date", "-id")
    )

    positions = (
        PortfolioPosition.objects
        .filter(
            family_id=family.id,
            asset__is_active=True,
            quantity__gt=0,
        )
        .select_related("asset", "asset__security_master")
        .annotate(
            latest_asset_class=Subquery(latest_transaction.values("asset_class")[:1]),
            latest_sub_class=Subquery(latest_transaction.values("sub_class")[:1]),
        )
    )

    matrix = {}

    def add(underlying, cap_type, value):
        underlying = _clean(underlying)
        value = float(value or 0)
        if not underlying or value <= 0:
            return
        bucket = _cap_bucket(cap_type)
        item = matrix.setdefault(
            underlying,
            {
                "small_cap": 0.0,
                "mid_cap": 0.0,
                "large_cap": 0.0,
                "unclassified": 0.0,
            },
        )
        item[bucket] += value

    pms_positions = []

    for position in positions:
        current_value = float(position.current_value or 0)
        if current_value <= 0:
            continue

        if _is_direct_equity(position.latest_asset_class, position.latest_sub_class):
            security = getattr(position.asset, "security_master", None)
            add(
                position.asset.name,
                security.cap_type if security else None,
                current_value,
            )
        elif _is_equity_pms(position.latest_asset_class, position.latest_sub_class):
            pms_positions.append(position)

    if pms_positions:
        pms_asset_ids = [position.asset_id for position in pms_positions]
        underlying_rows = (
            AssetUnderlyingHolding.objects
            .filter(family_id=family.id, asset_id__in=pms_asset_ids)
            .only("asset_id", "stock_name", "holding_percentage", "cap_type")
        )
        rows_by_asset = {}
        for underlying in underlying_rows:
            rows_by_asset.setdefault(underlying.asset_id, []).append(underlying)

        for position in pms_positions:
            current_value = float(position.current_value or 0)
            asset_underlyings = rows_by_asset.get(position.asset_id, [])

            if not asset_underlyings:
                security = getattr(position.asset, "security_master", None)
                add(
                    position.asset.name,
                    security.cap_type if security else None,
                    current_value,
                )
                continue

            for underlying in asset_underlyings:
                percentage = float(underlying.holding_percentage or 0) / 100.0
                if percentage <= 0:
                    continue
                cap_type = underlying.cap_type or security_lookup.get(
                    ("name", _clean(underlying.stock_name).upper())
                )
                add(
                    underlying.stock_name,
                    cap_type,
                    current_value * percentage,
                )

    mf_holdings = list(
        MutualFundHolding.objects
        .filter(family_id=family.id, scheme__is_active=True)
        .select_related("scheme")
    )
    equity_mf_holdings = [
        holding
        for holding in mf_holdings
        if "EQUITY" in _clean(holding.scheme.category).upper()
    ]

    scheme_ids = [holding.scheme_id for holding in equity_mf_holdings]

    if scheme_ids:
        latest_date = (
            MutualFundUnderlying.objects
            .filter(scheme_id=OuterRef("scheme_id"))
            .order_by("-portfolio_date")
            .values("portfolio_date")[:1]
        )
        underlying_rows = (
            MutualFundUnderlying.objects
            .filter(scheme_id__in=scheme_ids)
            .annotate(latest_portfolio_date=Subquery(latest_date))
            .filter(portfolio_date=F("latest_portfolio_date"))
            .order_by("scheme_id", "-percentage_of_nav")
        )

        rows_by_scheme = {}
        for underlying in underlying_rows:
            rows_by_scheme.setdefault(underlying.scheme_id, []).append(underlying)

        for holding in equity_mf_holdings:
            snapshot_rows = rows_by_scheme.get(holding.scheme_id, [])

            if not snapshot_rows:
                add(holding.scheme.scheme_name, None, holding.current_value)
                continue

            for underlying in snapshot_rows:
                percentage = float(underlying.percentage_of_nav or 0) / 100.0
                if percentage <= 0:
                    continue

                cap_type = None
                if underlying.isin:
                    cap_type = security_lookup.get(
                        ("isin", _clean(underlying.isin).upper())
                    )
                if not cap_type:
                    cap_type = security_lookup.get(
                        ("name", _clean(underlying.security_name).upper())
                    )

                add(
                    underlying.security_name,
                    cap_type,
                    float(holding.current_value or 0) * percentage,
                )

    current_value_by_cap = {
        "small_cap": sum(item["small_cap"] for item in matrix.values()),
        "mid_cap": sum(item["mid_cap"] for item in matrix.values()),
        "large_cap": sum(item["large_cap"] for item in matrix.values()),
        "unclassified": sum(item["unclassified"] for item in matrix.values()),
    }
    total_current_value = sum(current_value_by_cap.values())

    rows = []
    for underlying, item in sorted(
        matrix.items(),
        key=lambda entry: -sum(entry[1].values()),
    ):
        rows.append(
            {
                "underlying": underlying,
                "small_cap": item["small_cap"] or None,
                "mid_cap": item["mid_cap"] or None,
                "large_cap": item["large_cap"] or None,
                "unclassified": item["unclassified"] or None,
            }
        )

    rows.extend(
        [
            {
                "underlying": "% of Equity",
                **{
                    bucket: (
                        value / total_current_value * 100
                        if total_current_value
                        else 0
                    )
                    for bucket, value in current_value_by_cap.items()
                },
            },
            {
                "underlying": "Current Value",
                **current_value_by_cap,
            },
            {
                "underlying": "total",
                **current_value_by_cap,
            },
        ]
    )

    return Response(
        {
            "success": True,
            "count": len(rows),
            "results": rows,
        },
        status=status.HTTP_200_OK,
    )
