from datetime import date

from django.db.models import OuterRef, Subquery

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import PortfolioPosition, Transaction, TransactionType
from investments.services.xirr import XIRRCalculator
from users.permissions import get_visible_owner_ids


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def holding_report(request):
    """Return current portfolio positions with holding, asset-name, and subclass XIRR."""
    owner_ids = get_visible_owner_ids(request.user)

    latest_transaction = (
        Transaction.objects
        .filter(
            owner_id=OuterRef("owner_id"),
            asset_id=OuterRef("asset_id"),
            family_name=OuterRef("family_name"),
            portfolio=OuterRef("portfolio"),
        )
        .order_by("-transaction_date", "-id")
    )

    positions = list(
        PortfolioPosition.objects
        .filter(
            owner_id__in=owner_ids,
            asset__is_active=True,
            quantity__gt=0,
        )
        .select_related("asset", "asset__security_master")
        .annotate(
            latest_asset_class=Subquery(latest_transaction.values("asset_class")[:1]),
            latest_sub_class=Subquery(latest_transaction.values("sub_class")[:1]),
            latest_asset_name=Subquery(latest_transaction.values("asset_name")[:1]),
            latest_underlying=Subquery(latest_transaction.values("underlying")[:1]),
            latest_advisors=Subquery(latest_transaction.values("advisors")[:1]),
        )
        .order_by("family_name", "portfolio", "asset__name")
    )

    asset_ids = {position.asset_id for position in positions}

    transactions = (
        Transaction.objects
        .filter(
            owner_id__in=owner_ids,
            asset_id__in=asset_ids,
        )
        .only(
            "id",
            "owner_id",
            "asset_id",
            "family_name",
            "portfolio",
            "asset_class",
            "sub_class",
            "asset_name",
            "transaction_type",
            "transaction_date",
            "amount",
            "notes",
        )
        .order_by("transaction_date", "id")
    )

    xirr_transactions = {}

    for tx in transactions:
        family = str(tx.family_name or "").strip() or "Unassigned"
        portfolio = str(tx.portfolio or "").strip() or "Unassigned"
        asset_class = str(tx.asset_class or "").strip() or "Unassigned"
        sub_class = str(tx.sub_class or "").strip() or "Unassigned"
        asset_name = str(tx.asset_name or "").strip()

        base_key = (tx.owner_id, family, portfolio, asset_class, sub_class)
        xirr_transactions.setdefault(("sub_class", *base_key), []).append(tx)

        asset_key = ("asset_name", *base_key, asset_name)
        xirr_transactions.setdefault(asset_key, []).append(tx)

    def clean(value, default="Unassigned"):
        value = str(value or "").strip()
        return value or default

    def cash_flows_for(transactions_for_group):
        cash_flows = []
        for tx in transactions_for_group:
            amount = tx.amount or 0
            if tx.notes == "DIVIDEND REINVESTMENT":
                continue
            if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
                cash_flows.append((tx.transaction_date, -float(amount)))
            elif tx.transaction_type == TransactionType.SELL:
                cash_flows.append((tx.transaction_date, float(amount)))
        return cash_flows

    def calculate_group_xirr(group_key, current_value):
        cash_flows = cash_flows_for(xirr_transactions.get(group_key, []))
        if current_value > 0:
            cash_flows.append((date.today(), current_value))
        if len(cash_flows) < 2:
            return None
        return XIRRCalculator.calculate(cash_flows)

    subclass_current_values = {}
    asset_name_current_values = {}

    for position in positions:
        family = clean(position.family_name)
        portfolio = clean(position.portfolio)
        asset_class = clean(position.latest_asset_class)
        sub_class = clean(position.latest_sub_class)
        asset_name = clean(position.latest_asset_name, position.asset.name)
        current_value = float(position.current_value or 0)

        base_key = (position.owner_id, family, portfolio, asset_class, sub_class)
        subclass_key = ("sub_class", *base_key)
        asset_key = ("asset_name", *base_key, asset_name)
        subclass_current_values[subclass_key] = subclass_current_values.get(subclass_key, 0.0) + current_value
        asset_name_current_values[asset_key] = asset_name_current_values.get(asset_key, 0.0) + current_value

    subclass_xirr = {
        key: calculate_group_xirr(key, current_value)
        for key, current_value in subclass_current_values.items()
    }
    asset_name_xirr = {
        key: calculate_group_xirr(key, current_value)
        for key, current_value in asset_name_current_values.items()
    }

    xirr_transactions_by_position = {}
    for tx in transactions:
        family = clean(tx.family_name)
        portfolio = clean(tx.portfolio)
        position_key = (tx.owner_id, family, portfolio, tx.asset_id)
        xirr_transactions_by_position.setdefault(position_key, []).append(tx)

    def calculate_position_xirr(position):
        key = (
            position.owner_id,
            clean(position.family_name),
            clean(position.portfolio),
            position.asset_id,
        )
        cash_flows = cash_flows_for(xirr_transactions_by_position.get(key, []))
        if float(position.quantity or 0) > 0 and float(position.current_value or 0) > 0:
            cash_flows.append((date.today(), float(position.current_value)))
        if len(cash_flows) < 2:
            return None
        return XIRRCalculator.calculate(cash_flows)

    results = []

    for position in positions:
        asset = position.asset
        security_master = getattr(asset, "security_master", None)
        invested_value = float(position.invested_value or 0)
        gain = float(position.gain or 0)
        family = clean(position.family_name)
        portfolio = clean(position.portfolio)
        asset_class = clean(position.latest_asset_class)
        sub_class = clean(position.latest_sub_class)
        asset_name = clean(position.latest_asset_name, asset.name)
        base_key = (position.owner_id, family, portfolio, asset_class, sub_class)
        subclass_key = ("sub_class", *base_key)
        asset_key = ("asset_name", *base_key, asset_name)

        results.append({
            "id": position.id,
            "owner_id": position.owner_id,
            "family_name": family,
            "portfolio": portfolio,
            "asset_class": asset_class,
            "sub_class": sub_class,
            "asset_id": asset.id,
            "asset_name": asset_name,
            "underlying": clean(position.latest_underlying, ""),
            "isin": asset.isin,
            "advisors": clean(position.latest_advisors, ""),
            "quantity": float(position.quantity or 0),
            "average_cost": float(position.average_cost or 0),
            "invested_value": invested_value,
            "current_price": float(position.current_price or 0),
            "current_value": float(position.current_value or 0),
            "gain": gain,
            "gain_percentage": round(gain / invested_value * 100, 2) if invested_value else 0,
            "xirr": calculate_position_xirr(position),
            "sub_class_xirr": subclass_xirr.get(subclass_key),
            "asset_name_xirr": asset_name_xirr.get(asset_key),
            "sector": security_master.sector if security_master else None,
            "cap_type": security_master.cap_type if security_master else None,
            "amc_name": security_master.amc_name if security_master else None,
        })

    return Response({
        "success": True,
        "count": len(results),
        "results": results,
    }, status=status.HTTP_200_OK)
