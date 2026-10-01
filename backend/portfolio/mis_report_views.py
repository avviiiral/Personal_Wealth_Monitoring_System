from datetime import date
from decimal import Decimal
from io import BytesIO
import re

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, Border, Side
from openpyxl.utils import get_column_letter
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _safe_filename(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "Family"))
    return cleaned.strip("._") or "Family"


def _serialize_report(report):
    def serialize(value):
        if isinstance(value, dict):
            return {key: serialize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serialize(item) for item in value]
        return _json_safe(value)

    return serialize(report)


def _style_header(cell):
    cell.font = Font(bold=True, size=12)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = Border(bottom=Side(style="thin"))


def _style_title(cell):
    cell.font = Font(bold=True, size=14)
    cell.alignment = Alignment(horizontal="left", vertical="center")


def _autosize(ws, minimum=12, maximum=36):
    for column_cells in ws.columns:
        letter = get_column_letter(column_cells[0].column)
        length = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[letter].width = min(max(length + 2, minimum), maximum)


def _build_ips_sheet(workbook, report):
    ws = workbook.create_sheet("IPS")
    families = report["family_names"]
    ws["A1"] = "Sheet 1 - IPS"
    _style_title(ws["A1"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(2, len(families) + 2))

    headers = ["Nature of Investment", "Date", *families, "Grand Total"]
    for col, value in enumerate(headers, 1):
        ws.cell(3, col, value)
        _style_header(ws.cell(3, col))

    current_date = report["reporting_date"].strftime("%d.%m.%Y")
    prior_date = report["prior_month_date"].strftime("%d.%m.%Y")
    ws.cell(4, 1, "Asset Class (Eq, Debt, etc")
    ws.cell(4, 2, current_date)
    ws.cell(5, 2, prior_date)
    ws.cell(6, 2, f"Diff- {current_date}")

    for row_idx, row in enumerate(report["ips"], 4):
        ws.cell(row_idx, 1, row["asset_class"])
        for offset, family in enumerate(families, 3):
            ws.cell(row_idx, offset, row["family_values"].get(family, 0))
        ws.cell(row_idx, len(families) + 3, row["grand_total"])

    # Add prior and difference lines immediately below each asset class, matching
    # the supplied workbook's date/difference convention.
    data_start = 4
    rebuilt = []
    for row in report["ips"]:
        rebuilt.append(("current", row))
        rebuilt.append(("prior", row))
        rebuilt.append(("difference", row))

    for index, (kind, row) in enumerate(rebuilt, data_start):
        ws.cell(index, 1, row["asset_class"] if kind == "current" else "")
        if kind == "current":
            ws.cell(index, 2, current_date)
            values = row["family_values"]
            total = row["grand_total"]
        elif kind == "prior":
            ws.cell(index, 2, prior_date)
            values = row["prior_family_values"]
            total = row["prior_total"]
        else:
            ws.cell(index, 2, f"Diff- {current_date}")
            values = {
                family: row["family_values"].get(family, 0) - row["prior_family_values"].get(family, 0)
                for family in families
            }
            total = row["difference"]
        for offset, family in enumerate(families, 3):
            ws.cell(index, offset, values.get(family, 0))
        ws.cell(index, len(families) + 3, total)

    # Remove the initially written duplicate rows and keep only the workbook layout.
    for row_num in range(4, 4 + len(report["ips"])):
        ws.delete_rows(4)

    # Reinsert the intended rows cleanly after deletion.
    cursor = 4
    for row in report["ips"]:
        for kind in ("current", "prior", "difference"):
            ws.cell(cursor, 1, row["asset_class"] if kind == "current" else "")
            if kind == "current":
                ws.cell(cursor, 2, current_date)
                values, total = row["family_values"], row["grand_total"]
            elif kind == "prior":
                ws.cell(cursor, 2, prior_date)
                values, total = row["prior_family_values"], row["prior_total"]
            else:
                ws.cell(cursor, 2, f"Diff- {current_date}")
                values = {
                    family: row["family_values"].get(family, 0) - row["prior_family_values"].get(family, 0)
                    for family in families
                }
                total = row["difference"]
            for offset, family in enumerate(families, 3):
                ws.cell(cursor, offset, values.get(family, 0))
            ws.cell(cursor, len(families) + 3, total)
            cursor += 1

    ws.freeze_panes = "C4"
    _autosize(ws, 14, 32)
    return ws


def _build_data_sheet(workbook, report):
    ws = workbook.create_sheet("Data Sheet")
    ws["A1"] = "Sheet 2 - Data Sheet"
    _style_title(ws["A1"])
    ws.merge_cells("A1:P1")

    report_date = report["reporting_date"].strftime("%d.%m.%Y")
    opening_label = report["opening_date"].strftime("%b-%y").upper()
    closing_label = report["reporting_date"].strftime("%b-%y").upper()

    ws.merge_cells("E3:G3")
    ws.merge_cells("H3:J3")
    ws.merge_cells("K3:M3")
    ws.merge_cells("N3:P3")
    ws["E3"] = f"Investment Cost {report_date}"
    ws["H3"] = f"{opening_label} Closing MTM"
    ws["K3"] = f"Transactions- Buy/Sell upto {closing_label}"
    ws["N3"] = f"{closing_label} Closing MTM"
    for cell in ("E3", "H3", "K3", "N3"):
        _style_header(ws[cell])

    headers = [
        "Fund Name", "FILE", "Fund Type.V1", "Advisor",
        "Qty/Units", "Rate", f"Total Cost Dt.{report_date}",
        f"Units - Closing {opening_label}", f"NAV- {opening_label}", f"Amount-{opening_label} MTM",
        "Units", "NAV", "Amount",
        f"Units - Closing {closing_label}", f"NAV- {closing_label}", f"Amount-{closing_label} MTM",
    ]
    for col, value in enumerate(headers, 1):
        ws.cell(4, col, value)
        _style_header(ws.cell(4, col))

    subheaders = [
        "Asset Names (CARNELLIAN PMS, ETC)", "Family name (DAJ, etc)", "Asset Class", "",
        "(Investment cost)", "", "", f"(Opening Bal for the year at market value)", "", "",
        "(Transactions for the year at transaction value)", "", "",
        "(Closing Bal for the period at market value)", "", "",
    ]
    for col, value in enumerate(subheaders, 1):
        ws.cell(5, col, value)
        ws.cell(5, col).alignment = Alignment(vertical="top", wrap_text=True)

    for row_idx, row in enumerate(report["data_sheet"], 6):
        values = [
            row["asset_name"], row["family_name"], row["asset_class"], row["advisor"],
            row["qty_units"], row["rate"], row["total_cost"],
            row["opening_units"], row["opening_nav"], row["opening_amount"],
            row["transaction_units"], row["transaction_nav"], row["transaction_amount"],
            row["closing_units"], row["closing_nav"], row["closing_amount"],
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row_idx, col, value)
            ws.cell(row_idx, col).alignment = Alignment(vertical="top", wrap_text=(col <= 4))

    ws.freeze_panes = "A6"
    _autosize(ws, 12, 34)
    for col in range(5, 17):
        for cell in ws.iter_cols(min_col=col, max_col=col, min_row=6, max_row=ws.max_row):
            for item in cell:
                item.number_format = '#,##0.00'
    return ws


def _build_fund_summary_sheet(workbook, report):
    ws = workbook.create_sheet("Fund Type Summary")
    ws["A1"] = "Sheet 3 - Fund Type wise Summary"
    _style_title(ws["A1"])
    ws.merge_cells("A1:D1")

    headers = ["Fund Type.V2", "Fund Name", "Total", "Market Value"]
    for col, value in enumerate(headers, 1):
        ws.cell(3, col, value)
        _style_header(ws.cell(3, col))

    row_idx = 4
    grand_total = Decimal("0")
    for group in report["fund_type_summary"]:
        for item in group["rows"]:
            ws.cell(row_idx, 1, group["fund_type"])
            ws.cell(row_idx, 2, item["fund_name"])
            ws.cell(row_idx, 3, item["total"])
            ws.cell(row_idx, 4, item["market_value_label"])
            row_idx += 1
        ws.cell(row_idx, 1, f"{group['fund_type']} Subtotal")
        ws.cell(row_idx, 3, group["subtotal"])
        ws.cell(row_idx, 1).font = Font(bold=True)
        ws.cell(row_idx, 3).font = Font(bold=True)
        grand_total += Decimal(str(group["subtotal"] or 0))
        row_idx += 1

    ws.cell(row_idx, 1, "Grand Total")
    ws.cell(row_idx, 3, grand_total)
    ws.cell(row_idx, 1).font = Font(bold=True)
    ws.cell(row_idx, 3).font = Font(bold=True)
    ws.cell(row_idx + 1, 1, "Asset class")
    ws.cell(row_idx + 1, 2, "Asset name")
    ws.cell(row_idx + 2, 1, "(With subtotal for each asset class and grand total at end)")
    ws.freeze_panes = "A4"
    _autosize(ws, 14, 48)
    return ws


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mis_report(request):
    family = _authorized_active_family(request.user)
    report = MISReportService.build(family)
    return Response(_serialize_report(report))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mis_report_download(request):
    family = _authorized_active_family(request.user)
    report = MISReportService.build(family)

    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    _build_ips_sheet(workbook, report)
    _build_data_sheet(workbook, report)
    _build_fund_summary_sheet(workbook, report)

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    filename = (
        f"MIS_Report_{_safe_filename(family.name)}_"
        f"{report['reporting_date'].isoformat()}.xlsx"
    )
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
