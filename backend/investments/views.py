from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import SecurityMaster

from .services.transaction_import import (
    TransactionImportError,
    TransactionImporter,
)

from .services.security_master import (
    SecurityMasterService,
)

from .services.auto_price_refresh import (
    refresh_assets_async,
)

from users.permissions import get_visible_owner_ids


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def import_transactions(request):
    """
    Import transaction data from an Excel or CSV file.

    Expected multipart/form-data field:
        file
    """

    uploaded_file = request.FILES.get("file")

    if uploaded_file is None:
        return Response(
            {
                "success": False,
                "message": (
                    "Please upload an Excel or CSV "
                    "file using the 'file' field."
                ),
            },
            status=400,
        )

    filename = uploaded_file.name.lower()

    if not (
        filename.endswith(".xlsx")
        or filename.endswith(".csv")
    ):
        return Response(
            {
                "success": False,
                "message": (
                    "Only .xlsx and .csv files are supported."
                ),
            },
            status=400,
        )

    try:
        result = TransactionImporter.import_file(
            file=uploaded_file,
            owner=request.user,
        )

    except TransactionImportError as exc:
        return Response(
            {
                "success": False,
                "message": str(exc),
            },
            status=400,
        )

    except Exception as exc:
        return Response(
            {
                "success": False,
                "message": (
                    "Unexpected error while importing "
                    "the transaction file."
                ),
                "error": str(exc),
            },
            status=500,
        )

    # Kick off an immediate price refresh for every asset this
    # import touched, on a background thread - see
    # services.auto_price_refresh. The import itself already
    # committed, so this can never affect the response below or
    # the (already-saved) transactions/assets.
    refresh_assets_async(result.get("touched_asset_ids", []))

    return Response(
        {
            "success": True,
            "message": (
                "Transaction file imported successfully."
            ),
            "data": result,
        },
        status=201,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def security_master_list(request):
    """
    Return Security Master records belonging to
    the authenticated user.
    """

    securities = (
        SecurityMaster.objects
        .filter(owner_id__in=get_visible_owner_ids(request.user))
        .order_by("asset_name")
    )

    results = []

    for security in securities:
        results.append(
            {
                "id": security.id,
                "isin": security.isin,
                "asset_name": security.asset_name,
                "sector": security.sector,
                "cap_type": security.cap_type,
                "manual_nav_enabled": (
                    security.manual_nav_enabled
                ),
                "manual_nav": (
                    str(security.manual_nav)
                    if security.manual_nav is not None
                    else None
                ),
            }
        )

    return Response(
        {
            "success": True,
            "count": len(results),
            "results": results,
        }
    )


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def security_master_detail(
    request,
    security_id,
):
    """
    Retrieve or update a user's Security Master
    classification.
    """

    security = (
        SecurityMaster.objects
        .filter(
            id=security_id,
            owner=request.user,
        )
        .first()
    )

    if security is None:
        return Response(
            {
                "success": False,
                "message": (
                    "Security Master record not found."
                ),
            },
            status=404,
        )

    if request.method == "GET":
        return Response(
            {
                "success": True,
                "data": {
                    "id": security.id,
                    "isin": security.isin,
                    "asset_name": security.asset_name,
                    "sector": security.sector,
                    "cap_type": security.cap_type,
                    "manual_nav_enabled": (
                        security.manual_nav_enabled
                    ),
                    "manual_nav": (
                        str(security.manual_nav)
                        if security.manual_nav is not None
                        else None
                    ),
                },
            }
        )

    data = request.data

    if "sector" in data:
        security.sector = (
            str(data["sector"]).strip()
        )

    if "cap_type" in data:
        security.cap_type = (
            str(data["cap_type"]).strip()
        )

    if "manual_nav_enabled" in data:
        security.manual_nav_enabled = bool(
            data["manual_nav_enabled"]
        )

    if "manual_nav" in data:

        value = data["manual_nav"]

        if value in ("", None):
            security.manual_nav = None
        else:
            try:
                from decimal import Decimal

                security.manual_nav = Decimal(
                    str(value)
                )

            except Exception:
                return Response(
                    {
                        "success": False,
                        "message": (
                            "manual_nav must be a "
                            "valid numeric value."
                        ),
                    },
                    status=400,
                )

    security.save()

    return Response(
        {
            "success": True,
            "message": (
                "Security Master updated successfully."
            ),
            "data": {
                "id": security.id,
                "isin": security.isin,
                "asset_name": security.asset_name,
                "sector": security.sector,
                "cap_type": security.cap_type,
                "manual_nav_enabled": (
                    security.manual_nav_enabled
                ),
                "manual_nav": (
                    str(security.manual_nav)
                    if security.manual_nav is not None
                    else None
                ),
            },
        }
    )