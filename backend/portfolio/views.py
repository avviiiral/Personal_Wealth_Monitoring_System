import traceback
from decimal import Decimal
from typing import cast

from django.db import transaction
from django.db.models import Sum

from investments.models import (
    Asset,
    AssetCategory,
    Holding,
    Transaction,
)

from market_data.services.market_data_manager import (
    MarketDataManager,
)

from portfolio.services.holding_engine import (
    HoldingCalculationEngine,
)

from portfolio.services.portfolio_position_engine import (
    PortfolioPositionEngine,
)

from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .serializers import (
    AssetSerializer,
    HoldingSerializer,
    TransactionSerializer,
)

from users.permissions import get_active_family_group_id


def _active_family_id(request):
    family_id = get_active_family_group_id(request.user)

    if family_id is None:
        return None, Response(
            {
                "detail": "An active family must be selected."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return family_id, None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def portfolio_assets(request):

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    if request.method == "GET":

        assets = (
            Asset.objects
            .filter(family_group_id=family_id)
            .order_by("name")
        )

        return Response({
            "count": assets.count(),
            "results": AssetSerializer(
                assets,
                many=True,
            ).data,
        })

    serializer = AssetSerializer(
        data=request.data,
    )

    if not serializer.is_valid():

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    asset = cast(
        Asset,
        serializer.save(
            owner=request.user,
            family_group_id=family_id,
        ),
    )

    market_data = {
        "success": False,
        "skipped": True,
        "reason": "Market data not requested.",
    }

    if asset.category in ["STOCK", "ETF"]:

        try:

            market_data = (
                MarketDataManager
                .fetch_and_rebuild(
                    asset,
                    period="1y",
                )
            )

        except Exception as exc:

            market_data = {
                "success": False,
                "skipped": False,
                "error": str(exc),
            }

    asset_data = dict(
        AssetSerializer(asset).data
    )

    asset_data["market_data"] = market_data

    return Response(
        asset_data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def portfolio_asset_detail(
    request,
    asset_id,
):

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    try:

        asset = Asset.objects.get(
            id=asset_id,
            family_group_id=family_id,
        )

    except Asset.DoesNotExist:

        return Response(
            {
                "detail": "Asset not found."
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":

        return Response(
            AssetSerializer(asset).data,
            status=status.HTTP_200_OK,
        )

    if request.method == "PUT":

        serializer = AssetSerializer(
            asset,
            data=request.data,
        )

    elif request.method == "PATCH":

        serializer = AssetSerializer(
            asset,
            data=request.data,
            partial=True,
        )

    else:

        asset.is_active = False

        asset.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

    if not serializer.is_valid():

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    asset = cast(
        Asset,
        serializer.save(),
    )

    return Response(
        AssetSerializer(asset).data,
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def portfolio_transactions(request):

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    if request.method == "GET":

        transactions = (
            Transaction.objects
            .filter(family_group_id=family_id)
            .select_related("asset")
            .order_by(
                "-transaction_date",
                "-created_at",
            )
        )

        return Response({
            "count": transactions.count(),
            "results": TransactionSerializer(
                transactions,
                many=True,
            ).data,
        })

    serializer = TransactionSerializer(
        data=request.data,
        context={
            "request": request,
            "family_group_id": family_id,
        },
    )

    if not serializer.is_valid():

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():

        transaction_obj = cast(
            Transaction,
            serializer.save(
                owner=request.user,
                family_group_id=family_id,
            ),
        )

        HoldingCalculationEngine.rebuild_holding(
            transaction_obj.asset
        )

        PortfolioPositionEngine.rebuild_all_for_user(
            request.user
        )

    return Response(
        TransactionSerializer(
            transaction_obj
        ).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def portfolio_transaction_detail(
    request,
    transaction_id,
):

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    try:

        transaction_obj = (
            Transaction.objects
            .select_related("asset")
            .get(
                id=transaction_id,
                family_group_id=family_id,
            )
        )

    except Transaction.DoesNotExist:

        return Response(
            {
                "detail": "Transaction not found."
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":

        return Response(
            TransactionSerializer(
                transaction_obj
            ).data,
            status=status.HTTP_200_OK,
        )

    if request.method == "DELETE":

        old_asset = transaction_obj.asset

        with transaction.atomic():

            transaction_obj.delete()

            HoldingCalculationEngine.rebuild_holding(
                old_asset
            )

            PortfolioPositionEngine.rebuild_all_for_user(
                request.user
            )

        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

    serializer = TransactionSerializer(
        transaction_obj,
        data=request.data,
        partial=request.method == "PATCH",
        context={
            "request": request,
            "family_group_id": family_id,
        },
    )

    if not serializer.is_valid():

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_asset = transaction_obj.asset

    with transaction.atomic():

        transaction_obj = cast(
            Transaction,
            serializer.save(),
        )

        new_asset = transaction_obj.asset

        HoldingCalculationEngine.rebuild_holding(
            old_asset
        )

        if new_asset.id != old_asset.id:

            HoldingCalculationEngine.rebuild_holding(
                new_asset
            )

        PortfolioPositionEngine.rebuild_all_for_user(
            request.user
        )

    return Response(
        TransactionSerializer(
            transaction_obj
        ).data,
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_summary(request):

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    holdings = (
        Holding.objects
        .filter(
            family_group_id=family_id,
            asset__is_active=True,
        )
    )

    total_invested = (
        holdings.aggregate(
            total=Sum("invested_value")
        )["total"]
        or Decimal("0")
    )

    total_current_value = (
        holdings.aggregate(
            total=Sum("current_value")
        )["total"]
        or Decimal("0")
    )

    total_unrealized_pnl = (
        total_current_value
        - total_invested
    )

    pnl_percentage = (
        (
            total_unrealized_pnl
            / total_invested
        ) * Decimal("100")
        if total_invested
        else Decimal("0")
    )

    return Response({
        "total_invested": total_invested,
        "total_current_value": total_current_value,
        "total_unrealized_pnl": total_unrealized_pnl,
        "pnl_percentage": round(
            float(pnl_percentage),
            2,
        ),
        "number_of_holdings": holdings.count(),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_holdings(request):

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    holdings = (
        Holding.objects
        .filter(
            family_group_id=family_id,
            asset__is_active=True,
        )
        .exclude(
            asset__category=AssetCategory.MUTUAL_FUND,
        )
        .select_related("asset")
        .order_by("-current_value")
    )

    return Response({
        "count": holdings.count(),
        "results": HoldingSerializer(
            holdings,
            many=True,
        ).data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_tree(request):

    from portfolio.services.portfolio_tree_service import (
        PortfolioTreeService,
    )

    family_id, error_response = _active_family_id(request)

    if error_response is not None:
        return error_response

    try:
        tree = PortfolioTreeService.build(
            family_group_id=family_id,
        )

    except Exception as exc:
        traceback.print_exc()

        return Response(
            {
                "success": False,
                "message": (
                    "Unable to build the portfolio tree."
                ),
                "error": str(exc),
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "success": True,
            **tree,
        },
        status=status.HTTP_200_OK,
    )
