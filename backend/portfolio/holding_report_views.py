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
    """Return current portfolio positions with the latest transaction metadata and XIRR."""
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
        key = (tx.owner_id, family, portfolio, tx.asset_id)
        xirr_transactions.setdefault(key, []).append(tx)

    def clean(value, default="Unassigned"):
        value = str(value or "").strip()
        return value or default

    def calculate_xirr(position):
        """Match the XIRR semantics already used by PortfolioTreeService."""
        key = (
            position.owner_id,
            clean(position.family_name),
            clean(position.portfolio),
            position.asset_id,
        )
        cash_flows = []

        for tx in xirr_transactions.get(key, []):
            amount = tx.amount or 0

            if tx.notes == "DIVIDEND REINVESTMENT":
                continue

            if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
                cash_flows.append((tx.transaction_date, -float(amount)))
            elif tx.transaction_type == TransactionType.SELL:
                cash_flows.append((tx.transaction_date, float(amount)))

        current_quantity = float(position.quantity or 0)
        current_value = float(position.current_value or 0)

        if current_quantity > 0 and current_value > 0:
            cash_flows.append((date.today(), current_value))

        if len(cash_flows) < 2:
            return None

        return XIRRCalculator.calculate(cash_flows)

    results = []

    for position in positions:
        asset = position.asset
        security_master = getattr(asset, "security_master", None)
        invested_value = float(position.invested_value or 0)
        gain = float(position.gain or 0)

        results.append({
            "id": position.id,
            "owner_id": position.owner_id,
            "family_name": clean(position.family_name),
            "portfolio": clean(position.portfolio),
            "asset_class": clean(position.latest_asset_class),
            "sub_class": clean(position.latest_sub_class),
            "asset_id": asset.id,
            "asset_name": clean(position.latest_asset_name, asset.name),
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
            "xirr": calculate_xirr(position),
            "sector": security_master.sector if security_master else None,
            "cap_type": security_master.cap_type if security_master else None,
            "amc_name": security_master.amc_name if security_master else None,
        })

    return Response({
        "success": True,
        "count": len(results),
        "results": results,
    }, status=status.HTTP_200_OK)
