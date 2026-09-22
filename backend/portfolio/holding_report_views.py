from datetime import date
import re

from django.db.models import OuterRef, Subquery

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import AssetUnderlyingHolding, PortfolioPosition, Transaction, TransactionType
from investments.services.xirr import XIRRCalculator
from users.permissions import family_scope, require_active_family


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def holding_report(request):
    """Return current portfolio positions with asset-class, subclass, asset-name, and holding XIRR."""
    family = require_active_family(request.user)
    active_family = family

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

    positions = list(
        PortfolioPosition.objects
        .filter(
            family_id=active_family.id,
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
            family_id=active_family.id,
            asset_id__in=asset_ids,
        )
        .only(
            "id",
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
        tx_family = str(tx.family_name or "").strip() or "Unassigned"
        portfolio = str(tx.portfolio or "").strip() or "Unassigned"
        asset_class = str(tx.asset_class or "").strip() or "Unassigned"
        sub_class = str(tx.sub_class or "").strip() or "Unassigned"
        asset_name = str(tx.asset_name or "").strip()

        base_key = (tx_family, portfolio, asset_class, sub_class)
        xirr_transactions.setdefault(("asset_class", tx_family, portfolio, asset_class), []).append(tx)
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

    asset_class_current_values = {}
    subclass_current_values = {}
    asset_name_current_values = {}

    for position in positions:
        family = clean(position.family_name)
        portfolio = clean(position.portfolio)
        asset_class = clean(position.latest_asset_class)
        sub_class = clean(position.latest_sub_class)
        asset_name = clean(position.latest_asset_name, position.asset.name)
        current_value = float(position.current_value or 0)

        asset_class_key = ("asset_class", family, portfolio, asset_class)
        base_key = (family, portfolio, asset_class, sub_class)
        subclass_key = ("sub_class", *base_key)
        asset_key = ("asset_name", *base_key, asset_name)
        asset_class_current_values[asset_class_key] = asset_class_current_values.get(asset_class_key, 0.0) + current_value
        subclass_current_values[subclass_key] = subclass_current_values.get(subclass_key, 0.0) + current_value
        asset_name_current_values[asset_key] = asset_name_current_values.get(asset_key, 0.0) + current_value

    asset_class_xirr = {
        key: calculate_group_xirr(key, current_value)
        for key, current_value in asset_class_current_values.items()
    }
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
        position_key = (family, portfolio, tx.asset_id)
        xirr_transactions_by_position.setdefault(position_key, []).append(tx)

    def calculate_position_xirr(position):
        key = (
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

    uploaded_underlying_by_asset = {}
    if asset_ids:
        # Resolve uploaded underlying snapshots primarily by Asset FK.  The
        # asset-name fallback is intentional: older imports can leave a
        # duplicate Asset record while the current PortfolioPosition points
        # at the other record.  In that case the uploaded snapshot is still
        # the correct family-owned snapshot for the same named investment.
        underlying_rows = AssetUnderlyingHolding.objects.filter(
            family_id=active_family.id,
        ).select_related("asset").only(
            "asset_id", "stock_name", "holding_percentage", "asset__name"
        )
        underlying_by_asset_name = {}
        for underlying_row in underlying_rows:
            uploaded_underlying_by_asset.setdefault(underlying_row.asset_id, []).append(underlying_row)
            asset_name_key = clean(underlying_row.asset.name, "").casefold()
            if asset_name_key:
                underlying_by_asset_name.setdefault(asset_name_key, []).append(underlying_row)

        for position in positions:
            if position.asset_id in uploaded_underlying_by_asset:
                continue
            asset_name_key = clean(position.asset.name, "").casefold()
            fallback_rows = underlying_by_asset_name.get(asset_name_key, [])
            if fallback_rows:
                uploaded_underlying_by_asset[position.asset_id] = fallback_rows

    underlying_xirr_by_asset = {}
    for asset_id, underlying_rows in uploaded_underlying_by_asset.items():
        position = next((item for item in positions if item.asset_id == asset_id), None)
        if position is None:
            continue
        position_transactions = xirr_transactions_by_position.get(
            (clean(position.family_name), clean(position.portfolio), asset_id),
            [],
        )
        underlying_values = {}
        for underlying_row in underlying_rows:
            percentage = float(underlying_row.holding_percentage or 0) / 100.0
            if percentage <= 0:
                continue
            cash_flows = []
            for tx in position_transactions:
                amount = float(tx.amount or 0) * percentage
                if tx.notes == "DIVIDEND REINVESTMENT":
                    continue
                if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
                    cash_flows.append((tx.transaction_date, -amount))
                elif tx.transaction_type == TransactionType.SELL:
                    cash_flows.append((tx.transaction_date, amount))

            current_value = float(position.current_value or 0) * percentage
            if current_value > 0:
                cash_flows.append((date.today(), current_value))
            xirr = None
            if len(cash_flows) >= 2:
                xirr = XIRRCalculator.calculate(cash_flows)

            underlying_values[str(underlying_row.stock_name).strip()] = {
                "xirr": xirr,
                "holding_percentage": float(underlying_row.holding_percentage or 0),
            }
        if underlying_values:
            underlying_xirr_by_asset[asset_id] = underlying_values

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
        asset_class_key = ("asset_class", family, portfolio, asset_class)
        base_key = (family, portfolio, asset_class, sub_class)
        subclass_key = ("sub_class", *base_key)
        asset_key = ("asset_name", *base_key, asset_name)

        results.append({
            "id": position.id,
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
            "asset_class_xirr": asset_class_xirr.get(asset_class_key),
            "sub_class_xirr": subclass_xirr.get(subclass_key),
            "asset_name_xirr": asset_name_xirr.get(asset_key),
            "underlying_xirr": underlying_xirr_by_asset.get(position.asset_id, {}),
            "sector": security_master.sector if security_master else None,
            "cap_type": security_master.cap_type if security_master else None,
            "amc_name": security_master.amc_name if security_master else None,
        })

    return Response({
        "success": True,
        "count": len(results),
        "results": results,
    }, status=status.HTTP_200_OK)



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def holding_matrix_report(request):
    """Return Underlying x Asset Name percentages using canonical ISIN identity."""
    family = require_active_family(request.user)

    def clean_matrix(value, default=""):
        value = str(value or "").strip()
        return value or default

    def canonical_key(isin, name):
        cleaned_isin = clean_matrix(isin).upper()
        if cleaned_isin:
            return ("isin", cleaned_isin)
        normalized = re.sub(r"[^A-Z0-9]", "", clean_matrix(name).upper())
        normalized = re.sub(r"(LIMITED|LTD|PRIVATE|PVT)$", "", normalized)
        return ("name", normalized)

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

    positions = list(
        PortfolioPosition.objects
        .filter(family_id=family.id, asset__is_active=True, quantity__gt=0)
        .select_related("asset")
        .annotate(
            latest_asset_name=Subquery(latest_transaction.values("asset_name")[:1]),
            latest_underlying=Subquery(latest_transaction.values("underlying")[:1]),
        )
    )

    uploaded_rows = list(
        AssetUnderlyingHolding.objects
        .filter(family_id=family.id)
        .select_related("asset")
        .only(
            "asset_id", "stock_name", "isin", "holding_percentage",
            "asset__name",
        )
    )

    uploaded_by_asset = {}
    uploaded_by_name = {}
    for underlying in uploaded_rows:
        uploaded_by_asset.setdefault(underlying.asset_id, []).append(underlying)
        uploaded_by_name.setdefault(
            clean_matrix(underlying.asset.name).casefold(), []
        ).append(underlying)

    exposure = {}
    display_names = {}
    underlying_totals = {}

    for position in positions:
        current_value = float(position.current_value or 0)
        if current_value <= 0:
            continue

        asset_name = clean_matrix(position.latest_asset_name, position.asset.name)
        rows = uploaded_by_asset.get(position.asset_id, [])
        if not rows:
            rows = uploaded_by_name.get(
                clean_matrix(position.asset.name).casefold(), []
            )

        if rows:
            for underlying in rows:
                pct = float(underlying.holding_percentage or 0)
                underlying_name = clean_matrix(underlying.stock_name)
                if pct <= 0 or not underlying_name:
                    continue

                identity = canonical_key(underlying.isin, underlying_name)
                display_names.setdefault(identity, underlying_name)

                value = current_value * pct / 100.0
                key = (asset_name, identity)
                exposure[key] = exposure.get(key, 0.0) + value
                underlying_totals[identity] = (
                    underlying_totals.get(identity, 0.0) + value
                )
            continue

        underlying = clean_matrix(position.latest_underlying)
        if not underlying:
            continue

        identity = canonical_key("", underlying)
        display_names.setdefault(identity, underlying)
        key = (asset_name, identity)
        exposure[key] = exposure.get(key, 0.0) + current_value
        underlying_totals[identity] = (
            underlying_totals.get(identity, 0.0) + current_value
        )

    identities = sorted(
        underlying_totals.keys(),
        key=lambda identity: display_names.get(identity, "").casefold(),
    )
    asset_names = sorted(
        {asset_name for asset_name, _ in exposure.keys()},
        key=str.casefold,
    )

    results = []
    for asset_name in asset_names:
        row = {"asset_name": asset_name}
        for identity in identities:
            total = underlying_totals.get(identity, 0.0)
            value = exposure.get((asset_name, identity), 0.0)
            row[display_names[identity]] = (
                value / total * 100.0 if total > 0 else 0.0
            )
        results.append(row)

    return Response({
        "success": True,
        "count": len(results),
        "underlyings": [display_names[identity] for identity in identities],
        "results": results,
    }, status=status.HTTP_200_OK)

