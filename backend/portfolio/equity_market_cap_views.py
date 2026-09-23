from datetime import date
from types import SimpleNamespace

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import AssetUnderlyingHolding, SecurityMaster, Transaction
from users.permissions import require_active_family
from portfolio.services.portfolio_position_engine import PortfolioPositionEngine


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


def _is_equity_pms(sub_class):
    return _normalized_sub_class(sub_class) == "EQUITYPMS"


def _is_direct_equity(sub_class):
    return _normalized_sub_class(sub_class) == "DIRECTEQUITY"


def _is_equity_mutual_fund(sub_class):
    normalized = _normalized_sub_class(sub_class)
    return normalized in {"EQUITYMUTUALFUND", "EQUITYMUTUALFUNDS"}


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

    raw_date = request.query_params.get("as_of_date")
    if raw_date:
        try:
            as_of_date = date.fromisoformat(raw_date)
        except ValueError:
            return Response(
                {"success": False, "message": "as_of_date must be YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        as_of_date = date.today()

    transactions_as_of = list(
        Transaction.objects
        .filter(family=family, transaction_date__lte=as_of_date)
        .select_related("asset")
        .order_by("transaction_date", "created_at", "id")
    )

    grouped_transactions = {}
    for transaction in transactions_as_of:
        key = (
            _clean(transaction.family_name) or family_name,
            _clean(transaction.portfolio) or "Unassigned",
            transaction.asset_id,
        )
        grouped_transactions[key] = transaction

    positions = []
    for (position_family_name, portfolio, asset_id), latest_transaction in grouped_transactions.items():
        asset = latest_transaction.asset
        if not asset.is_active:
            continue

        calculated = PortfolioPositionEngine.calculate_position(
            family=family,
            family_name=position_family_name,
            portfolio=portfolio,
            asset=asset,
            as_of_date=as_of_date,
        )
        quantity = float(calculated["quantity"] or 0)
        invested_value = float(calculated["invested_value"] or 0)
        if quantity <= 0 or invested_value <= 0:
            continue

        positions.append(
            SimpleNamespace(
                family_name=position_family_name,
                portfolio=portfolio,
                asset_id=asset_id,
                asset=asset,
                quantity=quantity,
                invested_value=invested_value,
                latest_asset_class=latest_transaction.asset_class,
                latest_sub_class=latest_transaction.sub_class,
                latest_asset_name=latest_transaction.asset_name,
            )
        )

    # Store invested-value-weighted market-cap exposure per
    # Family + Asset Name. The report is based on the hierarchy:
    # Family -> Asset Class (Equity) -> Sub Class -> Asset Name -> Underlying.
    matrix = {}

    def add_exposure(family_name, sub_class, asset_name, cap_type, invested_amount):
        family_name = _clean(family_name)
        sub_class = _clean(sub_class)
        asset_name = _clean(asset_name)
        invested_amount = float(invested_amount or 0)
        if not family_name or not asset_name or invested_amount <= 0:
            return

        bucket = _cap_bucket(cap_type)
        key = (family_name, sub_class, asset_name)
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
        and (
            _is_equity_pms(position.latest_sub_class)
            or _is_equity_mutual_fund(position.latest_sub_class)
        )
    ]
    underlying_rows = (
        AssetUnderlyingHolding.objects
        .filter(family_id=family.id, asset_id__in=equity_asset_ids, created_at__date__lte=as_of_date)
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
                position.latest_sub_class,
                asset_name_for_position,
                security.cap_type if security else None,
                invested_value,
            )
            continue

        if (
            _is_equity_pms(position.latest_sub_class)
            or _is_equity_mutual_fund(position.latest_sub_class)
        ):
            asset_underlyings = rows_by_asset.get(position.asset_id, [])
            if not asset_underlyings:
                security = getattr(position.asset, "security_master", None)
                add_exposure(
                    family_name_for_position,
                    position.latest_sub_class,
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
                    position.latest_sub_class,
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
                    position.latest_sub_class,
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
            position.latest_sub_class,
            asset_name_for_position,
            security.cap_type if security else None,
            invested_value,
        )

    rows = []
    for (family_name, sub_class, asset_name), item in sorted(
        matrix.items(),
        key=lambda entry: (entry[0][0], entry[0][1], entry[0][2]),
    ):
        total_invested = item["total_invested"]
        rows.append(
            {
                "family_name": family_name,
                "sub_class": sub_class,
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
            "as_of_date": as_of_date.isoformat(),
            "results": rows,
        },
        status=status.HTTP_200_OK,
    )
