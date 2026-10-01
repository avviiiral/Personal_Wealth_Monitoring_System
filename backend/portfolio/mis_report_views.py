import re
from datetime import date

from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from users.permissions import is_system_owner, require_active_family
from .mis_report_service import MISReportService


def _authorized_active_family(user):
    family = require_active_family(user)
    if is_system_owner(user):
        return family
    if not user.profile.family_groups.filter(pk=family.pk).exists():
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("The selected family is not available to this user.")
    return family


def _json_safe(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _sanitize_filename(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "Family"))
    return cleaned.strip("._") or "Family"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mis_report(request):
    family = _authorized_active_family(request.user)
    report = MISReportService.build(family)

    report["reporting_date"] = report["reporting_date"].isoformat()
    report["summary"] = {key: _json_safe(value) for key, value in report["summary"].items()}
    report["asset_class_summary"] = [
        {key: _json_safe(value) for key, value in row.items()}
        for row in report["asset_class_summary"]
    ]
    report["holdings"] = [
        {key: _json_safe(value) for key, value in row.items()}
        for row in report["holdings"]
    ]
    return Response(report)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mis_report_download(request):
    family = _authorized_active_family(request.user)
    report = MISReportService.build(family)

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "MIS Summary"
    holdings_sheet = workbook.create_sheet("Holdings")

    header_fill = PatternFill("solid", fgColor="374151")
    title_fill = PatternFill("solid", fgColor="1F2937")
    white_font = Font(color="FFFFFF", bold=True)

    summary_sheet["A1"] = "MIS Report"
    summary_sheet["A1"].font = Font(bold=True, size=16, color="FFFFFF")
    summary_sheet["A1"].fill = title_fill
    summary_sheet["A2"] = "Family Member"
    summary_sheet["B2"] = report["family_name"]
    summary_sheet["A3"] = "Reporting Date"
    summary_sheet["B3"] = report["reporting_date"]
    summary_sheet["A5"] = "Total Invested Value"
    summary_sheet["B5"] = float(report["summary"]["total_invested"])
    summary_sheet["A6"] = "Total Current Value"
    summary_sheet["B6"] = float(report["summary"]["total_current_value"])
    summary_sheet["A7"] = "Total P&L"
    summary_sheet["B7"] = float(report["summary"]["total_pnl"])
    summary_sheet["A8"] = "P&L %"
    summary_sheet["B8"] = float(report["summary"]["pnl_percentage"])

    summary_sheet["A10"] = "Asset Class Summary"
    summary_sheet["A10"].font = Font(bold=True)

    summary_headers = ["Asset Class", "Invested Value", "Current Value", "P&L", "P&L %"]
    for col, header in enumerate(summary_headers, 1):
        cell = summary_sheet.cell(11, col, header)
        cell.fill = header_fill
        cell.font = white_font

    for row_index, row in enumerate(report["asset_class_summary"], 12):
        values = [
            row["asset_class"],
            float(row["invested_value"]),
            float(row["current_value"]),
            float(row["pnl"]),
            float(row["pnl_percentage"]),
        ]
        for col, value in enumerate(values, 1):
            summary_sheet.cell(row_index, col, value)

    holdings_headers = [
        "Family Member", "Asset Name", "Portfolio", "Asset Class", "Sub Class",
        "Asset ID", "ISIN / Scheme Code",
        "Symbol", "Quantity", "Average Cost", "Invested Value",
        "Current Price", "Current Value", "P&L", "P&L %", "XIRR",
    ]
    for col, header in enumerate(holdings_headers, 1):
        cell = holdings_sheet.cell(1, col, header)
        cell.fill = header_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center")

    for row_index, row in enumerate(report["holdings"], 2):
        values = [
            row["family_name"], row["asset_name"], row["portfolio"], row["asset_class"], row["sub_class"],
            row["asset_id"], row["isin"], row["symbol"],
            row["quantity"], row["average_cost"], row["invested_value"],
            row["current_price"], row["current_value"], row["pnl"],
            row["pnl_percentage"], row["xirr"],
        ]
        for col, value in enumerate(values, 1):
            holdings_sheet.cell(row_index, col, value)

    for sheet in (summary_sheet, holdings_sheet):
        sheet.freeze_panes = "A2"
        for column_cells in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 32)

    filename = f"MIS_Report_{_sanitize_filename(family.name)}_{date.today().isoformat()}.xlsx"
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response
