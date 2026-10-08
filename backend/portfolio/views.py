import logging
import traceback
from openpyxl import Workbook
from decimal import Decimal
from typing import cast

from django.http import HttpResponse
from django.db import transaction
from django.db.models import Count, Sum

from investments.models import (
    Asset,
    AssetCategory,
    AssetUnderlyingHolding,
    Holding,
    Transaction,
    TransactionEditHistory,
)

from market_data.services.market_data_manager import MarketDataManager
from portfolio.services.holding_engine import HoldingCalculationEngine
from portfolio.services.portfolio_position_engine import PortfolioPositionEngine
from investments.services.asset_underlying import AssetUnderlyingImportError, AssetUnderlyingImporter

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from .serializers import (
    AssetSerializer,
    HoldingSerializer,
    TransactionSerializer,
    TransactionEditHistorySerializer,
)
from users.permissions import family_scope, require_active_family

logger = logging.getLogger("portfolio.views")


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def portfolio_assets(request):
    if request.method == "GET":
        assets = family_scope(Asset.objects, request.user).order_by("name")
        results = AssetSerializer(assets, many=True).data
        return Response({"count": len(results), "results": results})

    serializer = AssetSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    family = require_active_family(request.user)
    asset = cast(Asset, serializer.save(owner=request.user, family=family))
    market_data = {"success": False, "skipped": True, "reason": "Market data not requested."}
    if asset.category in ["STOCK", "ETF"]:
        try:
            market_data = MarketDataManager.fetch_and_rebuild(asset, period="1y")
        except Exception as exc:
            market_data = {"success": False, "skipped": False, "error": str(exc)}
    asset_data = dict(AssetSerializer(asset).data)
    asset_data["market_data"] = market_data
    return Response(asset_data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def portfolio_asset_detail(request, asset_id):
    asset = family_scope(Asset.objects, request.user).filter(id=asset_id).first()
    if asset is None:
        return Response({"detail": "Asset not found."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "GET":
        return Response(AssetSerializer(asset).data, status=status.HTTP_200_OK)
    if request.method == "PUT":
        serializer = AssetSerializer(asset, data=request.data)
    elif request.method == "PATCH":
        serializer = AssetSerializer(asset, data=request.data, partial=True)
    else:
        asset.is_active = False
        asset.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    asset = cast(Asset, serializer.save())
    return Response(AssetSerializer(asset).data, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def portfolio_transactions(request):
    if request.method == "GET":
        transactions = (
            family_scope(Transaction.objects, request.user)
            .select_related("asset")
            .order_by("-transaction_date", "-created_at")
        )
        results = TransactionSerializer(transactions, many=True).data
        return Response({"count": len(results), "results": results})
    serializer = TransactionSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        family = require_active_family(request.user)
        transaction_obj = cast(Transaction, serializer.save(owner=request.user, family=family))
        logger.info("Transaction created: id=%s asset_id=%s user_id=%s", transaction_obj.id, transaction_obj.asset_id, request.user.id)
        HoldingCalculationEngine.rebuild_holding(transaction_obj.asset)
        PortfolioPositionEngine.rebuild_all_for_user(request.user)
    return Response(TransactionSerializer(transaction_obj).data, status=status.HTTP_201_CREATED)


def _transaction_history_snapshot(transaction_obj):
    return {
        "family_name": transaction_obj.family_name,
        "portfolio": transaction_obj.portfolio,
        "asset_class": transaction_obj.asset_class,
        "sub_class": transaction_obj.sub_class,
        "asset_name": transaction_obj.asset_name,
        "underlying": transaction_obj.underlying,
        "advisors": transaction_obj.advisors,
        "transaction_date": str(transaction_obj.transaction_date),
        "transaction_type": transaction_obj.transaction_type,
        "quantity": str(transaction_obj.quantity),
        "price_per_unit": str(transaction_obj.price_per_unit),
        "amount": str(transaction_obj.amount),
        "fees": str(transaction_obj.fees),
        "notes": transaction_obj.notes,
        "source": transaction_obj.source,
        "source_key": transaction_obj.source_key,
        "asset_id": transaction_obj.asset_id,
        "isin": transaction_obj.asset.isin,
    }


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def portfolio_transaction_detail(request, transaction_id):
    transaction_obj = family_scope(Transaction.objects.select_related("asset"), request.user).filter(id=transaction_id).first()
    if transaction_obj is None:
        return Response({"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "GET":
        return Response(TransactionSerializer(transaction_obj).data, status=status.HTTP_200_OK)
    if request.method == "DELETE":
        old_asset = transaction_obj.asset
        old_values = _transaction_history_snapshot(transaction_obj)
        deleted_fields = [
            "advisors",
            "quantity",
            "price_per_unit",
            "amount",
        ]

        with transaction.atomic():
            transaction_id = transaction_obj.id
            asset_id = old_asset.id

            # Keep an audit record before deleting the transaction. The
            # transaction FK is SET_NULL, so the audit row survives and
            # the UI can display it as a deleted transaction.
            TransactionEditHistory.objects.create(
                transaction=transaction_obj,
                owner=transaction_obj.owner,
                family=transaction_obj.family,
                edited_by=request.user,
                old_values={field: old_values[field] for field in deleted_fields},
                new_values={field: None for field in deleted_fields},
                changed_fields=deleted_fields,
            )

            transaction_obj.delete()
            logger.info("Transaction deleted: id=%s asset_id=%s user_id=%s", transaction_id, asset_id, request.user.id)
            HoldingCalculationEngine.rebuild_holding(old_asset)
            PortfolioPositionEngine.rebuild_all_for_user(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = TransactionSerializer(
        transaction_obj,
        data=request.data,
        partial=request.method == "PATCH",
        context={"request": request},
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    old_asset = transaction_obj.asset
    old_values = _transaction_history_snapshot(transaction_obj)
    with transaction.atomic():
        transaction_obj = cast(Transaction, serializer.save())
        new_asset = transaction_obj.asset
        new_values = _transaction_history_snapshot(transaction_obj)
        changed_fields = [field for field in new_values if old_values.get(field) != new_values.get(field)]
        if changed_fields:
            logger.info("Transaction updated: id=%s changed_fields=%s user_id=%s", transaction_obj.id, changed_fields, request.user.id)
            TransactionEditHistory.objects.create(
                transaction=transaction_obj,
                owner=transaction_obj.owner,
                family=transaction_obj.family,
                edited_by=request.user,
                old_values={field: old_values[field] for field in changed_fields},
                new_values={field: new_values[field] for field in changed_fields},
                changed_fields=changed_fields,
            )
        HoldingCalculationEngine.rebuild_holding(old_asset)
        if new_asset.id != old_asset.id:
            HoldingCalculationEngine.rebuild_holding(new_asset)
        PortfolioPositionEngine.rebuild_all_for_user(request.user)
    return Response(TransactionSerializer(transaction_obj).data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_transaction_edit_history(request):
    history = (
        family_scope(TransactionEditHistory.objects, request.user)
        .select_related("transaction", "transaction__asset", "edited_by")
        .order_by("-edited_at")
    )
    results = TransactionEditHistorySerializer(history, many=True).data
    return Response({"count": len(results), "results": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_summary(request):
    holdings = family_scope(Holding.objects, request.user).filter(asset__is_active=True)
    totals = holdings.aggregate(
        total_invested=Sum("invested_value"),
        total_current_value=Sum("current_value"),
        number_of_holdings=Count("id"),
    )
    total_invested = totals["total_invested"] or Decimal("0")
    total_current_value = totals["total_current_value"] or Decimal("0")
    total_unrealized_pnl = total_current_value - total_invested
    pnl_percentage = (total_unrealized_pnl / total_invested) * Decimal("100") if total_invested else Decimal("0")
    return Response({
        "total_invested": total_invested,
        "total_current_value": total_current_value,
        "total_unrealized_pnl": total_unrealized_pnl,
        "pnl_percentage": round(float(pnl_percentage), 2),
        "number_of_holdings": totals["number_of_holdings"] or 0,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_holdings(request):
    holdings = (
        family_scope(Holding.objects, request.user).filter(asset__is_active=True)
        .exclude(asset__category=AssetCategory.MUTUAL_FUND)
        .select_related("asset")
        .order_by("-current_value")
    )
    results = HoldingSerializer(holdings, many=True).data
    return Response({"count": len(results), "results": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_tree(request):
    from portfolio.services.portfolio_tree_service import PortfolioTreeService
    try:
        xirr_filters = {
            "family": request.query_params.get("family", "").strip(),
            "asset_class": request.query_params.get("asset_class", "").strip(),
            "advisor": request.query_params.get("advisor", "").strip(),
        }
        family = require_active_family(request.user)
        tree = PortfolioTreeService.build(
            owner=request.user,
            family_id=family.id,
            xirr_filters=xirr_filters,
        )
    except PermissionDenied as exc:
        return Response(
            {
                "success": False,
                "message": str(exc.detail) if hasattr(exc, "detail") else str(exc),
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    except Exception as exc:
        traceback.print_exc()
        return Response(
            {"success": False, "message": "Unable to build the portfolio tree.", "error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({"success": True, **tree}, status=status.HTTP_200_OK)



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_underlying_uploads(request):
    """Return the latest underlying upload per portfolio asset for the active family."""
    family = require_active_family(request.user)
    rows = (
        AssetUnderlyingHolding.objects
        .filter(family=family)
        .select_related("asset", "uploaded_by")
        .order_by("asset_id", "-uploaded_at", "-id")
    )

    latest_by_asset = {}
    underlying_rows = {}
    for row in rows:
        latest_by_asset.setdefault(row.asset_id, row)
        underlying_rows.setdefault(row.asset_id, []).append(row)

    results = []
    for asset_id, latest in latest_by_asset.items():
        results.append({
            "asset_id": asset_id,
            "asset_name": latest.asset.name,
            "uploaded_by": latest.uploaded_by.username if latest.uploaded_by else (
                latest.owner.username if latest.owner else "Deleted User"
            ),
            "uploaded_at": latest.uploaded_at,
            "underlyings": [
                {
                    "stock_name": row.stock_name,
                    "holding_percentage": str(row.holding_percentage),
                    "isin": row.isin,
                }
                for row in sorted(
                    underlying_rows[asset_id],
                    key=lambda item: (-item.holding_percentage, item.stock_name.casefold()),
                )
            ],
        })

    results.sort(key=lambda item: item["asset_name"].casefold())
    return Response({"count": len(results), "results": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_underlying_template(request):
    """Return the Excel template used by Portfolio underlying uploads."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Underlying"
    sheet.append(["Stocks", "% Holding"])
    sheet.append(["HDFC Bank", 25])
    sheet.append(["ICICI Bank", 20])
    sheet.append(["Reliance Industries", 15])

    instructions = workbook.create_sheet("Instructions")
    instructions.append(["Column", "Description"])
    instructions.append(["Stocks", "Name of the underlying security."])
    instructions.append(["% Holding", "Holding percentage of the underlying security (0 to 100)."])
    instructions.append(["", "Upload one row per underlying security. Keep the headers unchanged."])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="sample_underlying_format.xlsx"'
    workbook.save(response)
    return response


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def portfolio_asset_underlying_import(request, asset_id):
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return Response({"success": False, "message": "Please upload an Excel file using the 'file' field."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        result = AssetUnderlyingImporter.import_file(
            file=uploaded_file,
            asset_id=asset_id,
            owner=request.user,
        )
    except AssetUnderlyingImportError as exc:
        return Response({"success": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except PermissionDenied as exc:
        return Response({"success": False, "message": str(exc.detail) if hasattr(exc, "detail") else str(exc)}, status=status.HTTP_403_FORBIDDEN)
    except Exception as exc:
        traceback.print_exc()
        return Response({"success": False, "message": "Unexpected error while importing underlying data.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({"success": True, "message": "Underlying data uploaded successfully.", "data": result}, status=status.HTTP_201_CREATED)
