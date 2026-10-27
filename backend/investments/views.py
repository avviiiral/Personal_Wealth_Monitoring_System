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

from .models import Asset, SecurityAlias, SecurityIdentityResolutionLog, SecurityMaster, TransactionUpload, TransactionUploadFailure

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

from users.permissions import CanWriteFinancialData, family_scope, is_system_owner, require_active_family


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def preview_transaction_duplicates(request):
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return Response({"success": False, "message": "Please select a transaction file."}, status=400)
    if not uploaded_file.name.lower().endswith((".xlsx", ".csv")):
        return Response({"success": False, "message": "Only .xlsx and .csv files are supported."}, status=400)
    try:
        return Response({"success": True, "data": TransactionImporter.preview_duplicates(uploaded_file, request.user)})
    except PermissionDenied as exc:
        return Response({"success": False, "message": str(exc.detail)}, status=403)
    except TransactionImportError as exc:
        return Response({"success": False, "message": str(exc)}, status=400)
    except Exception:
        return Response({"success": False, "message": "Unable to review duplicate transactions."}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
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
        selected_duplicate_rows = request.data.get("selected_duplicate_rows", "[]")
        if isinstance(selected_duplicate_rows, str):
            import json
            selected_duplicate_rows = json.loads(selected_duplicate_rows)
        result = TransactionImporter.import_file(
            file=uploaded_file, owner=request.user, upload_batch=upload,
            selected_duplicate_rows=selected_duplicate_rows,
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
        return Response({"success": False, "message": upload.error_message, "upload_id": upload.id}, status=500)

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
    instruction_rows = [
        ["Standard Transaction Upload Format"],
        ["1. Transactions sheet"],
        ["Enter one transaction per row in the Transactions sheet. Keep every required column header unchanged."],
        ["Required columns: Family Member, Asset Class, Sub Class, Asset Name, Underlying, Advisors, ISIN, Date, Trans. Type, Quantity, Price, Amount."],
        ["Family Member is the user-facing column name accepted by PWMS; Family Name is also accepted for older files."],
        ["2. General investment transaction types (Trans. Type)"],
        ["BUY — Buy/purchase of a security."],
        ["SELL — Sale of a security."],
        ["SIP — Systematic investment transaction."],
        ["DIVIDEND — Dividend received. DIVIDEND PAYOUT is also accepted and maps to DIVIDEND."],
        ["INTEREST — Interest received."],
        ["DEPOSIT — Deposit transaction."],
        ["WITHDRAWAL — Withdrawal transaction."],
        ["BONUS — Bonus shares/bonus transaction."],
        ["SPLIT — Stock split. *SPLIT is also accepted and maps to SPLIT."],
        ["BUYBACK — Accepted and mapped to SELL."],
        ["DIVIDEND REINVESTMENT — Accepted and mapped to BUY for general investments."],
        ["3. Mutual fund transaction types"],
        ["BUY or PURCHASE — Maps to mutual fund PURCHASE."],
        ["SIP — Maps to mutual fund SIP."],
        ["SELL or REDEMPTION — Maps to mutual fund REDEMPTION."],
        ["DIVIDEND — Maps to mutual fund DIVIDEND."],
        ["DIVIDEND REINVESTMENT — Maps to mutual fund PURCHASE."],
        ["4. Summary sheet (optional)"],
        ["Use the Summary sheet to map transactions to Portfolio Name. Required headers: Family Name, Portfolio Name, Asset Class, Advisors, Asset Name, ISIN."],
        ["5. Important notes"],
        ["Use the transaction type labels above exactly as written, unless an alternate accepted label is explicitly noted."],
        ["A dividend payout and a dividend reinvestment are different: payout records dividend income; reinvestment is mapped to a purchase (BUY for general investments and PURCHASE for mutual funds)."],
        ["Only use transaction types that accurately describe the source transaction. Do not relabel fees, taxes, transfers, corrections, or adjustments as another type without confirming the correct accounting treatment."],
        ["This workbook is a format template, not a source of prices or transaction values. Enter the figures and dates from your records."]
    ]
    for row in instruction_rows:
        instructions.append(row)

    for sheet in (transactions, summary, instructions):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        if sheet.title != "Instructions":
            sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            width = min(max(len(str(cell.value or "")) for cell in column) + 2, 70 if sheet.title == "Instructions" else 35)
            sheet.column_dimensions[column[0].column_letter].width = width

    for row in instructions.iter_rows():
        instructions.row_dimensions[row[0].row].height = 30
        instructions.cell(row=row[0].row, column=1).alignment = Alignment(
            wrap_text=True,
            vertical="top",
        )
    for row_number in (1, 2, 6, 18, 24, 26):
        if row_number <= instructions.max_row:
            instructions.cell(row=row_number, column=1).font = Font(bold=True)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="standard_transactions_format.xlsx"'
    workbook.save(response)
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def transaction_underlyings(request):
    """Return distinct underlying classifications stored on transactions for the active family."""
    family = require_active_family(request.user)

    from .models import Transaction

    transactions = (
        Transaction.objects
        .filter(family=family)
        .exclude(underlying__isnull=True)
        .exclude(underlying__exact="")
        .order_by("underlying", "asset_name", "transaction_date")
    )

    grouped = {}
    for transaction in transactions:
        underlying = transaction.underlying.strip()
        if not underlying:
            continue

        key = underlying.casefold()
        item = grouped.setdefault(key, {
            "underlying": underlying,
            "asset_names": set(),
            "asset_classes": set(),
            "sub_classes": set(),
            "advisors": set(),
            "family_members": set(),
            "transaction_count": 0,
            "first_transaction_date": None,
            "last_transaction_date": None,
        })

        if transaction.asset_name:
            item["asset_names"].add(transaction.asset_name.strip())
        if transaction.asset_class:
            item["asset_classes"].add(transaction.asset_class.strip())
        if transaction.sub_class:
            item["sub_classes"].add(transaction.sub_class.strip())
        if transaction.advisors:
            item["advisors"].add(transaction.advisors.strip())
        if transaction.family_name:
            item["family_members"].add(transaction.family_name.strip())

        item["transaction_count"] += 1
        date = transaction.transaction_date
        if item["first_transaction_date"] is None or date < item["first_transaction_date"]:
            item["first_transaction_date"] = date
        if item["last_transaction_date"] is None or date > item["last_transaction_date"]:
            item["last_transaction_date"] = date

    results = []
    for item in grouped.values():
        results.append({
            "underlying": item["underlying"],
            "asset_names": sorted(item["asset_names"], key=str.casefold),
            "asset_classes": sorted(item["asset_classes"], key=str.casefold),
            "sub_classes": sorted(item["sub_classes"], key=str.casefold),
            "advisors": sorted(item["advisors"], key=str.casefold),
            "family_members": sorted(item["family_members"], key=str.casefold),
            "transaction_count": item["transaction_count"],
            "first_transaction_date": item["first_transaction_date"],
            "last_transaction_date": item["last_transaction_date"],
        })

    results.sort(key=lambda row: row["underlying"].casefold())
    return Response({"count": len(results), "results": results})


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
@permission_classes([IsAuthenticated, CanWriteFinancialData])
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

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def security_identity_resolution_history(request):
    """System-owner-only audit view of new security identity mapping activity."""
    if not is_system_owner(request.user):
        raise PermissionDenied("Only System Owners may view security identity resolution logs.")

    logs = (
        SecurityIdentityResolutionLog.objects
        .select_related("family", "actor", "upload", "resolved_asset")
        .order_by("-created_at", "-id")[:500]
    )
    results = []
    for item in logs:
        results.append({
            "id": item.id,
            "family_name": item.family.name,
            "upload_file_name": item.upload.file_name if item.upload else None,
            "uploaded_by": item.actor.username if item.actor else "Deleted User",
            "row_number": item.row_number,
            "created_at": item.created_at,
            "input_asset_name": item.input_asset_name,
            "input_underlying": item.input_underlying,
            "input_isin": item.input_isin,
            "canonical_name": item.canonical_name,
            "resolved_asset_name": item.resolved_asset.name if item.resolved_asset else None,
            "match_method": item.match_method,
            "status": item.status,
            "details": item.details,
        })
    return Response({"count": len(results), "results": results})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def security_alias_list_create(request):
    """List or explicitly approve a family-scoped alternate security name."""
    family = require_active_family(request.user)
    if request.method == "GET":
        aliases = SecurityAlias.objects.filter(family=family, is_active=True).select_related("asset", "approved_by")
        results = [{
            "id": item.id,
            "asset_id": item.asset_id,
            "asset_name": item.asset.name,
            "alias": item.alias,
            "approved_by": item.approved_by.username if item.approved_by else None,
            "approved_at": item.approved_at,
        } for item in aliases.order_by("normalized_alias")]
        return Response({"count": len(results), "results": results})

    alias = str(request.data.get("alias", "")).strip()
    asset_id = request.data.get("asset_id")
    if not alias or not SecurityAlias.normalize(alias):
        return Response({"detail": "A non-empty alias is required."}, status=400)
    try:
        asset = Asset.objects.get(id=asset_id, family=family)
    except (Asset.DoesNotExist, TypeError, ValueError):
        return Response({"detail": "Asset not found in the active family."}, status=404)

    from django.db import IntegrityError
    try:
        item = SecurityAlias.objects.create(
            family=family, asset=asset, alias=alias, approved_by=request.user,
        )
    except IntegrityError:
        return Response({"detail": "This normalized alias is already registered for the active family."}, status=409)
    return Response({
        "id": item.id, "asset_id": item.asset_id, "asset_name": asset.name,
        "alias": item.alias, "approved_by": request.user.username, "approved_at": item.approved_at,
    }, status=201)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def security_alias_deactivate(request, alias_id):
    family = require_active_family(request.user)
    item = SecurityAlias.objects.filter(id=alias_id, family=family, is_active=True).first()
    if item is None:
        return Response({"detail": "Active security alias not found in the active family."}, status=404)
    item.is_active = False
    item.save(update_fields=["is_active"])
    return Response({"success": True, "message": "Security alias deactivated."})
