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


def _normalized_sub_class(value):
    return "".join(ch for ch in _clean(value).upper() if ch.isalnum())


def _is_allowed_equity_subclass(sub_class):
    normalized = _normalized_sub_class(sub_class)
    return normalized in {
        "EQUITYPMS",
        "DIRECTEQUITY",
        "EQUITYMUTUALFUNDS",
    }


def _is_equity_pms(sub_class):
    return _normalized_sub_class(sub_class) == "EQUITYPMS"


def _is_direct_equity(sub_class):
    return _normalized_sub_class(sub_class) == "DIRECTEQUITY"


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

    def add_asset(asset_name, cap_type, percentage):
        asset_name = _clean(asset_name)
        percentage = float(percentage or 0)
        if not asset_name or percentage <= 0:
            return
        bucket = _cap_bucket(cap_type)
        item = matrix.setdefault(
            asset_name,
            {
                "small_cap": 0.0,
                "mid_cap": 0.0,
                "large_cap": 0.0,
                "unclassified": 0.0,
            },
        )
        item[bucket] += percentage

    pms_positions = []

    for position in positions:
        current_value = float(position.current_value or 0)
        if current_value <= 0:
            continue

        if not _is_allowed_equity_subclass(position.latest_sub_class):
            continue

        if _is_direct_equity(position.latest_sub_class):
            security = getattr(position.asset, "security_master", None)
            add_asset(
                position.asset.name,
                security.cap_type if security else None,
                100.0,
            )
        elif _is_equity_pms(position.latest_sub_class):
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
            asset_underlyings = rows_by_asset.get(position.asset_id, [])

            if not asset_underlyings:
                security = getattr(position.asset, "security_master", None)
                add_asset(
                    position.asset.name,
                    security.cap_type if security else None,
                    100.0,
                )
                continue

            for underlying in asset_underlyings:
                percentage = float(underlying.holding_percentage or 0)
                if percentage <= 0:
                    continue
                cap_type = underlying.cap_type or security_lookup.get(
                    ("name", _clean(underlying.stock_name).upper())
                )
                add_asset(
                    position.asset.name,
                    cap_type,
                    percentage,
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
                add_asset(holding.scheme.scheme_name, None, 100.0)
                continue

            for underlying in snapshot_rows:
                percentage = float(underlying.percentage_of_nav or 0)
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

                add_asset(
                    holding.scheme.scheme_name,
                    cap_type,
                    percentage,
                )

    # Each asset row contains the percentage of that asset's
    # underlying exposure falling into each market-cap bucket.
    rows = []
    for asset_name, item in sorted(
        matrix.items(),
        key=lambda entry: -sum(entry[1].values()),
    ):
        rows.append(
            {
                "underlying": asset_name,
                "asset_name": asset_name,
                "small_cap": item["small_cap"] or None,
                "mid_cap": item["mid_cap"] or None,
                "large_cap": item["large_cap"] or None,
                "unclassified": item["unclassified"] or None,
            }
        )

    # Keep the existing summary rows, but their values continue to
    # represent overall equity exposure rather than per-asset
    # underlying percentages.
    equity_asset_values = {}
    for position in positions:
        current_value = float(position.current_value or 0)
        if current_value <= 0 or not _is_allowed_equity_subclass(position.latest_sub_class):
            continue
        if _is_direct_equity(position.latest_sub_class):
            security = getattr(position.asset, "security_master", None)
            bucket = _cap_bucket(security.cap_type if security else None)
            equity_asset_values[bucket] = equity_asset_values.get(bucket, 0.0) + current_value

    if pms_positions:
        pms_asset_ids = [position.asset_id for position in pms_positions]
        # Reuse the loaded underlying rows to derive current-value exposure.
        for position in pms_positions:
            current_value = float(position.current_value or 0)
            asset_underlyings = rows_by_asset.get(position.asset_id, [])
            if not asset_underlyings:
                security = getattr(position.asset, "security_master", None)
                bucket = _cap_bucket(security.cap_type if security else None)
                equity_asset_values[bucket] = equity_asset_values.get(bucket, 0.0) + current_value
                continue
            for underlying in asset_underlyings:
                percentage = float(underlying.holding_percentage or 0) / 100.0
                if percentage <= 0:
                    continue
                cap_type = underlying.cap_type or security_lookup.get(("name", _clean(underlying.stock_name).upper()))
                bucket = _cap_bucket(cap_type)
                equity_asset_values[bucket] = equity_asset_values.get(bucket, 0.0) + current_value * percentage

    # MF current-value exposure for the summary rows.
    for holding in equity_mf_holdings:
        snapshot_rows = rows_by_scheme.get(holding.scheme_id, []) if scheme_ids else []
        if not snapshot_rows:
            bucket = "unclassified"
            equity_asset_values[bucket] = equity_asset_values.get(bucket, 0.0) + float(holding.current_value or 0)
            continue
        for underlying in snapshot_rows:
            percentage = float(underlying.percentage_of_nav or 0) / 100.0
            if percentage <= 0:
                continue
            cap_type = None
            if underlying.isin:
                cap_type = security_lookup.get(("isin", _clean(underlying.isin).upper()))
            if not cap_type:
                cap_type = security_lookup.get(("name", _clean(underlying.security_name).upper()))
            bucket = _cap_bucket(cap_type)
            equity_asset_values[bucket] = equity_asset_values.get(bucket, 0.0) + float(holding.current_value or 0) * percentage

    total_current_value = sum(equity_asset_values.values())

    rows.extend(
        [
            {
                "asset_name": "% of Equity",
                **{
                    bucket: (
                        value / total_current_value * 100
                        if total_current_value
                        else 0
                    )
                    for bucket, value in equity_asset_values.items()
                },
            },
            {
                "asset_name": "Current Value",
                **{
                    bucket: equity_asset_values.get(bucket, 0.0)
                    for bucket in ("small_cap", "mid_cap", "large_cap", "unclassified")
                },
            },
            {
                "asset_name": "total",
                **{
                    bucket: equity_asset_values.get(bucket, 0.0)
                    for bucket in ("small_cap", "mid_cap", "large_cap", "unclassified")
                },
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
