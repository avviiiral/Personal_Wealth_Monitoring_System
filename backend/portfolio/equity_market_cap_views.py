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


def _normalized_name(value):
    value = _clean(value).upper()
    return "".join(ch if ch.isalnum() else " " for ch in value).split()


def _name_key(value):
    return "".join(_normalized_name(value))


def _name_matches(left, right):
    left_key = _name_key(left)
    right_key = _name_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if len(left_key) >= 8 and len(right_key) >= 8:
        return left_key.startswith(right_key) or right_key.startswith(left_key)
    return False


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
    family_name = _clean(getattr(family, "name", None) or getattr(family, "family_name", None) or str(family))
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
            latest_asset_name=Subquery(latest_transaction.values("asset_name")[:1]),
        )
    )

    # Store invested-value-weighted market-cap exposure per
    # Family + Asset Name. The report is based on the hierarchy:
    # Family -> Asset Class (Equity) -> Sub Class -> Asset Name -> Underlying.
    matrix = {}

    def add_exposure(family_name, asset_name, cap_type, invested_amount):
        family_name = _clean(family_name)
        asset_name = _clean(asset_name)
        invested_amount = float(invested_amount or 0)
        if not family_name or not asset_name or invested_amount <= 0:
            return

        bucket = _cap_bucket(cap_type)
        key = (family_name, asset_name)
        item = matrix.setdefault(
            key,
            {
                "total_invested": 0.0,
                "small_cap": 0.0,
                "mid_cap": 0.0,
                "large_cap": 0.0,
                "unclassified": 0.0,
            },
        )
        item["total_invested"] += invested_amount
        item[bucket] += invested_amount

    # Use the same Portfolio Asset -> uploaded underlying relationship
    # that Analytics uses. This is intentionally generic: it applies to
    # whatever Equity subclass the uploaded Asset belongs to, including
    # Equity Mutual Funds, without hardcoding fund names or securities.
    equity_asset_ids = [
        position.asset_id
        for position in positions
        if _normalized_sub_class(position.latest_asset_class) == "EQUITY"
        and _normalized_sub_class(position.latest_sub_class) in {
            "EQUITYPMS",
            "EQUITYMUTUALFUNDS",
        }
    ]
    underlying_rows = (
        AssetUnderlyingHolding.objects
        .filter(family_id=family.id, asset_id__in=equity_asset_ids)
        .only("asset_id", "stock_name", "holding_percentage", "cap_type")
    )
    rows_by_asset = {}
    for underlying in underlying_rows:
        rows_by_asset.setdefault(underlying.asset_id, []).append(underlying)

    for position in positions:
        if _normalized_sub_class(position.latest_asset_class) != "EQUITY":
            continue

        family_name_for_position = _clean(position.family_name) or family_name
        asset_name_for_position = _clean(position.latest_asset_name) or _clean(position.asset.name)
        invested_value = float(position.invested_value or 0)
        if invested_value <= 0:
            continue

        if _is_direct_equity(position.latest_sub_class):
            security = getattr(position.asset, "security_master", None)
            add_exposure(
                family_name_for_position,
                asset_name_for_position,
                security.cap_type if security else None,
                invested_value,
            )
            continue

        if _normalized_sub_class(position.latest_sub_class) in {
            "EQUITYPMS",
            "EQUITYMUTUALFUNDS",
        }:
            asset_underlyings = rows_by_asset.get(position.asset_id, [])
            if not asset_underlyings:
                security = getattr(position.asset, "security_master", None)
                add_exposure(
                    family_name_for_position,
                    asset_name_for_position,
                    security.cap_type if security else None,
                    invested_value,
                )
                continue

            percentage_total = sum(
                max(float(underlying.holding_percentage or 0), 0)
                for underlying in asset_underlyings
            )
            if percentage_total <= 0:
                add_exposure(
                    family_name_for_position,
                    asset_name_for_position,
                    None,
                    invested_value,
                )
                continue

            for underlying in asset_underlyings:
                holding_percentage = max(float(underlying.holding_percentage or 0), 0)
                if holding_percentage <= 0:
                    continue
                underlying_invested = invested_value * holding_percentage / percentage_total
                cap_type = underlying.cap_type or security_lookup.get(
                    ("name", _clean(underlying.stock_name).upper())
                )
                add_exposure(
                    family_name_for_position,
                    asset_name_for_position,
                    cap_type,
                    underlying_invested,
                )
            continue

        # Other Equity subclasses remain classified from their own
        # SecurityMaster metadata.
        security = getattr(position.asset, "security_master", None)
        add_exposure(
            family_name_for_position,
            asset_name_for_position,
            security.cap_type if security else None,
            invested_value,
        )

    # Equity Mutual Funds: use the same uploaded underlying classification
    # path used by Analytics Market Cap Allocation whenever available.
    # The uploaded AssetUnderlyingHolding rows carry the resolved cap_type.
    equity_mf_holdings = list(
        MutualFundHolding.objects
        .filter(family_id=family.id, scheme__is_active=True)
        .select_related("scheme")
    )

    uploaded_underlying_rows = (
        AssetUnderlyingHolding.objects
        .filter(family_id=family.id)
        .select_related("asset")
        .only("asset_id", "asset__name", "stock_name", "holding_percentage", "cap_type")
    )
    uploaded_assets = {}
    for underlying in uploaded_underlying_rows:
        uploaded_assets.setdefault(underlying.asset_id, []).append(underlying)

    uploaded_by_name = {}
    uploaded_by_isin = {}
    for underlying in uploaded_underlying_rows:
        asset = underlying.asset
        uploaded_by_name.setdefault(_name_key(asset.name), set()).add(underlying.asset_id)
        if asset.isin:
            uploaded_by_isin.setdefault(_clean(asset.isin).upper(), set()).add(underlying.asset_id)
        security_master = getattr(asset, "security_master", None)
        if security_master and security_master.isin:
            uploaded_by_isin.setdefault(_clean(security_master.isin).upper(), set()).add(underlying.asset_id)

    for holding in equity_mf_holdings:
        invested_value = float(holding.invested_value or 0)
        if invested_value <= 0:
            continue

        asset_name = _clean(holding.scheme.scheme_name)
        matching_asset_ids = set()

        # Match the uploaded Portfolio underlying to the MF holding
        # dynamically. Do not depend on a fixed fund name or a hardcoded
        # list of securities.
        exact_name_ids = uploaded_by_name.get(_name_key(asset_name), set())
        matching_asset_ids.update(exact_name_ids)

        if not matching_asset_ids:
            scheme_isins = {
                _clean(holding.scheme.isin_growth).upper(),
                _clean(holding.scheme.isin_dividend).upper(),
            }
            scheme_isins.discard("")
            for isin in scheme_isins:
                matching_asset_ids.update(uploaded_by_isin.get(isin, set()))

        if not matching_asset_ids:
            candidate_ids = {
                asset_id
                for asset_id, rows in uploaded_assets.items()
                if rows and _name_matches(asset_name, rows[0].asset.name)
            }
            if len(candidate_ids) == 1:
                matching_asset_ids = candidate_ids

        asset_underlyings = [
            row
            for asset_id in matching_asset_ids
            for row in uploaded_assets.get(asset_id, [])
        ]

        if asset_underlyings:
            percentage_total = sum(
                max(float(row.holding_percentage or 0), 0)
                for row in asset_underlyings
            )
            if percentage_total > 0:
                for row in asset_underlyings:
                    holding_percentage = max(float(row.holding_percentage or 0), 0)
                    if holding_percentage <= 0:
                        continue
                    underlying_invested = invested_value * holding_percentage / percentage_total
                    add_exposure(
                        family_name,
                        asset_name,
                        row.cap_type,
                        underlying_invested,
                    )
                continue

        # Fall back to the latest disclosed MF portfolio when the uploaded
        # Analytics-style underlying snapshot is not present for this fund.
        latest_date = (
            MutualFundUnderlying.objects
            .filter(scheme_id=holding.scheme_id)
            .order_by("-portfolio_date")
            .values("portfolio_date")[:1]
        )
        snapshot_rows = list(
            MutualFundUnderlying.objects
            .filter(scheme_id=holding.scheme_id)
            .filter(portfolio_date=Subquery(latest_date))
        )

        if not snapshot_rows:
            add_exposure(family_name, asset_name, None, invested_value)
            continue

        percentage_total = sum(
            max(float(row.percentage_of_nav or 0), 0)
            for row in snapshot_rows
        )
        if percentage_total <= 0:
            add_exposure(family_name, asset_name, None, invested_value)
            continue

        for row in snapshot_rows:
            nav_percentage = max(float(row.percentage_of_nav or 0), 0)
            if nav_percentage <= 0:
                continue
            underlying_invested = invested_value * nav_percentage / percentage_total
            cap_type = None
            if row.isin:
                cap_type = security_lookup.get(("isin", _clean(row.isin).upper()))
            if not cap_type:
                cap_type = security_lookup.get(("name", _clean(row.security_name).upper()))
            add_exposure(
                family_name,
                asset_name,
                cap_type,
                underlying_invested,
            )

    rows = []
    for (family_name, asset_name), item in sorted(
        matrix.items(),
        key=lambda entry: (entry[0][0], entry[0][1]),
    ):
        total_invested = item["total_invested"]
        rows.append(
            {
                "family_name": family_name,
                "asset_name": asset_name,
                "small_cap": item["small_cap"] / total_invested * 100 if total_invested else None,
                "mid_cap": item["mid_cap"] / total_invested * 100 if total_invested else None,
                "large_cap": item["large_cap"] / total_invested * 100 if total_invested else None,
                "unclassified": item["unclassified"] / total_invested * 100 if total_invested else None,
            }
        )

    return Response(
        {
            "success": True,
            "count": len(rows),
            "results": rows,
        },
        status=status.HTTP_200_OK,
    )
