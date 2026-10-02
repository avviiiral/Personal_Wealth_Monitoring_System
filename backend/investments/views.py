from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework import status
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from .models import SecurityMaster, TransactionUpload, TransactionUploadFailure

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

from users.permissions import family_scope, require_active_family


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def import_transactions(request):
    """Import transaction data and persist an auditable upload summary."""
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return Response({"success": False, "message": "Please upload an Excel or CSV file using the 'file' field."}, status=400)

    filename = uploaded_file.name.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        return Response({"success": False, "message": "Only .xlsx and .csv files are supported."}, status=400)

    family = require_active_family(request.user)
    upload = TransactionUpload.objects.create(
        owner=request.user,
        family=family,
        file_name=uploaded_file.name,
    )

    try:
        result = TransactionImporter.import_file(
            file=uploaded_file,
            owner=request.user,
            upload_batch=upload,
        )
    except PermissionDenied as exc:
        upload.status = "FAILED"
        upload.error_message = str(exc.detail) if hasattr(exc, "detail") else str(exc)
        upload.completed_at = timezone.now()
        upload.save(update_fields=["status", "error_message", "completed_at"])
        return Response({"success": False, "message": upload.error_message}, status=status.HTTP_403_FORBIDDEN)
    except TransactionImportError as exc:
        upload.status = "FAILED"
        upload.error_message = str(exc)
        upload.completed_at = timezone.now()
        upload.save(update_fields=["status", "error_message", "completed_at"])
        return Response({"success": False, "message": str(exc), "upload_id": upload.id}, status=400)
    except Exception as exc:
        upload.status = "FAILED"
        upload.error_message = "Unexpected error while importing the transaction file."
        upload.completed_at = timezone.now()
        upload.save(update_fields=["status", "error_message", "completed_at"])
        return Response({"success": False, "message": upload.error_message, "error": str(exc), "upload_id": upload.id}, status=500)

    refresh_assets_async(result.get("touched_asset_ids", []))

    return Response({
        "success": True,
        "message": "Transaction file processed successfully.",
        "data": result,
        "upload_id": upload.id,
    }, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def transaction_upload_history(request):
    family = require_active_family(request.user)
    uploads = TransactionUpload.objects.filter(family=family).select_related("owner")
    results = []
    for upload in uploads:
        results.append({
            "id": upload.id,
            "file_name": upload.file_name,
            "uploaded_by": upload.owner.username if upload.owner else "Deleted User",
            "uploaded_at": upload.uploaded_at,
            "total_rows": upload.total_rows,
            "imported_rows": upload.imported_rows,
            "failed_rows": upload.failed_rows,
            "duplicate_rows": upload.duplicate_rows,
            "status": upload.status,
            "error_message": upload.error_message,
        })
    return Response({"count": len(results), "results": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def transaction_upload_detail(request, upload_id):
    family = require_active_family(request.user)
    upload = TransactionUpload.objects.filter(id=upload_id, family=family).select_related("owner").first()
    if upload is None:
        return Response({"detail": "Transaction upload not found."}, status=404)

    failures = TransactionUploadFailure.objects.filter(upload=upload).values(
        "id", "row_number", "reason", "field_name", "row_data"
    )
    return Response({
        "id": upload.id,
        "file_name": upload.file_name,
        "uploaded_by": upload.owner.username if upload.owner else "Deleted User",
        "uploaded_at": upload.uploaded_at,
        "total_rows": upload.total_rows,
        "imported_rows": upload.imported_rows,
        "failed_rows": upload.failed_rows,
        "duplicate_rows": upload.duplicate_rows,
        "status": upload.status,
        "error_message": upload.error_message,
        "failures": list(failures),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_transaction_template(request):
    workbook = Workbook()
    transactions = workbook.active
    transactions.title = "Transactions"
    transaction_headers = [
        "Family Member", "Asset Class", "Sub Class", "Asset Name", "Underlying",
        "Advisors", "ISIN", "Date", "Trans. Type", "Quantity", "Price", "Amount",
    ]
    transactions.append(transaction_headers)

    summary = workbook.create_sheet("Summary")
    summary.append(["Portfolio Mapping"])
    summary.append(["Family Name", "Portfolio Name", "Asset Class", "Advisors", "Asset Name", "ISIN"])

    instructions = workbook.create_sheet("Instructions")
    instructions.append(["Standard Transaction Upload Format"])
    instructions.append(["Use the Transactions sheet for transaction rows. The Summary sheet is optional and maps rows to Portfolio Name."])
    instructions.append(["Do not rename the required headers. Family Member is accepted as the user-facing replacement for Family Name."])

    for sheet in (transactions, summary, instructions):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            width = min(max(len(str(cell.value or "")) for cell in column) + 2, 35)
            sheet.column_dimensions[column[0].column_letter].width = width

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="standard_transactions_format.xlsx"'
    workbook.save(response)
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def security_master_list(request):
    """
    Return Security Master records belonging to
    the authenticated user.
    """

    results = [
        {
            "id": security["id"],
            "isin": security["isin"],
            "asset_name": security["asset_name"],
            "sector": security["sector"],
            "cap_type": security["cap_type"],
            "manual_nav_enabled": security["manual_nav_enabled"],
            "manual_nav": (
                str(security["manual_nav"])
                if security["manual_nav"] is not None
                else None
            ),
        }
        for security in (
            SecurityMaster.objects
            .filter(family_id=require_active_family(request.user).id)
            .order_by("asset_name")
            .values(
                "id",
                "isin",
                "asset_name",
                "sector",
                "cap_type",
                "manual_nav_enabled",
                "manual_nav",
            )
        )
    ]

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