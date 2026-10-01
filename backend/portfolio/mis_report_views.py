from datetime import date
from decimal import Decimal
from io import BytesIO
import re

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from users.permissions import is_system_owner, require_active_family
from .mis_report_service import MISReportService




def _parse_report_dates(request):
    from_value = request.query_params.get("from_date")
    to_value = request.query_params.get("to_date")
    if not from_value and not to_value:
        return None, None
    if not from_value or not to_value:
        raise ValidationError({"detail": "Both from_date and to_date are required."})
    try:
        from_date = date.fromisoformat(from_value)
        to_date = date.fromisoformat(to_value)
    except ValueError:
        raise ValidationError({"detail": "Dates must be in YYYY-MM-DD format."})
    if from_date > to_date:
        raise ValidationError({"detail": "From date cannot be after to date."})
    if to_date > date.today():
        raise ValidationError({"detail": "To date cannot be in the future."})
    return from_date, to_date


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


_TITLE_FILL = PatternFill(fill_type="solid", fgColor="D9EAF7")
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="B4C7E7")
_SECTION_FILL = PatternFill(fill_type="solid", fgColor="DDEBF7")
_SUBHEADER_FILL = PatternFill(fill_type="solid", fgColor="E2F0D9")
_SUBTOTAL_FILL = PatternFill(fill_type="solid", fgColor="FFF2CC")
_GRAND_TOTAL_FILL = PatternFill(fill_type="solid", fgColor="C6E0B4")
_DIFF_FILL = PatternFill(fill_type="solid", fgColor="FCE4D6")
_INR_NUMBER_FORMAT = '₹#,##,##0.00;[Red]-₹#,##,##0.00'
_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)

def _style_header(cell, fill=_HEADER_FILL):
    cell.font = Font(bold=True, size=12)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.fill = fill
    cell.border = _BORDER


def _style_title(cell):
    cell.font = Font(bold=True, size=14)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    cell.fill = _TITLE_FILL


def _autosize(ws, minimum=12, maximum=36):
    for column_cells in ws.columns:
        letter = get_column_letter(column_cells[0].column)
        length = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[letter].width = min(max(length + 2, minimum), maximum)


def _build_ips_sheet(workbook, report, display_unit="lakhs"):
    ws = workbook.create_sheet("IPS")
    families = report["family_names"]
    ws["A1"] = "IPS"
    _style_title(ws["A1"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(2, len(families) + 3))

    headers = ["Nature of Investment", "Date", *families, "Grand Total"]
    for col, value in enumerate(headers, 1):
        ws.cell(3, col, value)
        _style_header(ws.cell(3, col))

    current_date = report["reporting_date"].strftime("%d.%m.%Y")
    prior_date = report["prior_month_date"].strftime("%d.%m.%Y")
    cursor = 4
    divisors = {"amount": Decimal("1"), "lakhs": Decimal("100000"), "crores": Decimal("10000000")}
    divisor = divisors[display_unit]

    # IPS display unit is presentation-only; source values remain in rupees.
    current_totals = {family: Decimal("0") for family in families}
    prior_totals = {family: Decimal("0") for family in families}

    for row in report["ips"]:
        difference_values = {
            family: row["family_values"].get(family, 0) - row["prior_family_values"].get(family, 0)
            for family in families
        }
        lines = [
            (row["asset_class"], current_date, row["family_values"], row["grand_total"], _SECTION_FILL),
            ("", prior_date, row["prior_family_values"], row["prior_total"], _SUBHEADER_FILL),
            ("", "Diff- " + current_date, difference_values, row["difference"], _DIFF_FILL),
        ]
        for asset_class, label, values, total, fill in lines:
            ws.cell(cursor, 1, asset_class)
            ws.cell(cursor, 2, label)
            for offset, family in enumerate(families, 3):
                value = Decimal(str(values.get(family, 0) or 0))
                ws.cell(cursor, offset, float(value / divisor))
                if label == current_date:
                    current_totals[family] += value
                elif label == prior_date:
                    prior_totals[family] += value
            ws.cell(cursor, len(families) + 3, float(Decimal(str(total or 0)) / divisor))
            for col in range(1, len(families) + 4):
                ws.cell(cursor, col).fill = fill
                ws.cell(cursor, col).border = _BORDER
            cursor += 1

    if report["ips"]:
        ws.cell(cursor, 1, "Grand Total")
        ws.cell(cursor, 2, "Overall")
        for offset, family in enumerate(families, 3):
            ws.cell(cursor, offset, float(current_totals[family] / divisor))
        overall_total = sum(current_totals.values(), Decimal("0"))
        ws.cell(cursor, len(families) + 3, float(overall_total / divisor))
        for col in range(1, len(families) + 4):
            ws.cell(cursor, col).fill = _GRAND_TOTAL_FILL
            ws.cell(cursor, col).border = _BORDER
        ws.cell(cursor, 1).font = Font(bold=True)
        ws.cell(cursor, 2).font = Font(bold=True)
        for col in range(3, len(families) + 4):
            ws.cell(cursor, col).font = Font(bold=True)
    else:
        ws.cell(4, 1, "No data available")

    unit_labels = {"amount": "₹ Amount", "lakhs": "₹ Lakhs", "crores": "₹ Crores"}
    ws.cell(2, 1, f"Values in {unit_labels[display_unit]}")
    ws.cell(2, 1).font = Font(italic=True, size=10)
    for row_cells in ws.iter_rows(min_row=4, max_row=ws.max_row, min_col=3, max_col=len(families) + 3):
        for cell in row_cells:
            cell.number_format = '#,##0' if display_unit == 'amount' else '#,##0.00'
    ws.freeze_panes = "C4"
    _autosize(ws, 14, 32)
    return ws


def _build_data_sheet(workbook, report):
    ws = workbook.create_sheet("Data Sheet")
    ws["A1"] = "Data Sheet"
    _style_title(ws["A1"])
    ws.merge_cells("A1:P1")

    report_date = report["reporting_date"].strftime("%d.%m.%Y")
    opening_label = report["opening_date"].strftime("%b-%y").upper()
    closing_label = report["reporting_date"].strftime("%b-%y").upper()
    period_start = report["period_start"].strftime("%d.%m.%Y")
    period_end = report["period_end"].strftime("%d.%m.%Y")

    # Keep the four grouped section headers from the MIS template, but omit
    # the additional explanatory/subheader row.
    ws.merge_cells("E3:G3")
    ws.merge_cells("H3:J3")
    ws.merge_cells("K3:M3")
    ws.merge_cells("N3:P3")
    ws["E3"] = f"Investment Cost {report_date}"
    ws["H3"] = f"{opening_label} Closing MTM"
    ws["K3"] = f"Transactions- Buy/Sell {period_start} to {period_end}"
    ws["N3"] = f"{closing_label} Closing MTM"

    for col in range(1, 17):
        ws.cell(3, col).fill = _SECTION_FILL
        ws.cell(3, col).border = _BORDER
    for cell in ("E3", "H3", "K3", "N3"):
        _style_header(ws[cell])

    headers = [
        "Fund Name",
        "Family Name",
        "Asset Class",
        "Advisor",
        "Qty/Units",
        "Rate",
        f"Total Cost Dt.{report_date}",
        f"Units - Closing {opening_label}",
        f"NAV- {opening_label}",
        f"Amount-{opening_label} MTM",
        "Units",
        "NAV",
        "Amount",
        f"Units - Closing {closing_label}",
        f"NAV- {closing_label}",
        f"Amount-{closing_label} MTM",
    ]
    for col, value in enumerate(headers, 1):
        ws.cell(4, col, value)
        _style_header(ws.cell(4, col))
        ws.cell(4, col).alignment = Alignment(vertical="top", wrap_text=True)

    for row_idx, row in enumerate(report["data_sheet"], 5):
        values = [
            row["asset_name"],
            row["family_name"],
            row["asset_class"],
            row["advisor"],
            row["qty_units"],
            row["rate"],
            row["total_cost"],
            row["opening_units"],
            row["opening_nav"],
            row["opening_amount"],
            row["transaction_units"],
            row["transaction_nav"],
            row["transaction_amount"],
            row["closing_units"],
            row["closing_nav"],
            row["closing_amount"],
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row_idx, col, value)
            ws.cell(row_idx, col).alignment = Alignment(vertical="top", wrap_text=(col <= 4))
            ws.cell(row_idx, col).border = _BORDER

    ws.freeze_panes = "A5"
    _autosize(ws, 12, 34)
    # Monetary columns use INR with Indian lakh/crore comma grouping; unit columns stay numeric.
    for col in (6, 7, 9, 10, 12, 13, 15, 16):
        for cell in ws.iter_cols(min_col=col, max_col=col, min_row=5, max_row=ws.max_row):
            for item in cell:
                item.number_format = _INR_NUMBER_FORMAT
    for col in (5, 8, 11, 14):
        for cell in ws.iter_cols(min_col=col, max_col=col, min_row=5, max_row=ws.max_row):
            for item in cell:
                item.number_format = '#,##0.00'
    return ws


def _build_tax_report_sheet(workbook, report):
    ws = workbook.create_sheet("Tax Report")
    ws["A1"] = "Tax Report"
    _style_title(ws["A1"])
    ws.merge_cells("A1:T1")

    report_date = report["reporting_date"].strftime("%d.%m.%Y")
    opening_label = report["opening_date"].strftime("%b-%y").upper()
    closing_label = report["reporting_date"].strftime("%b-%y").upper()
    period_start = report["period_start"].strftime("%d.%m.%Y")
    period_end = report["period_end"].strftime("%d.%m.%Y")

    ws.merge_cells("E3:G3")
    ws.merge_cells("H3:J3")
    ws.merge_cells("K3:M3")
    ws.merge_cells("N3:P3")
    ws.merge_cells("Q3:T3")
    ws["E3"] = f"Investment Cost {report_date}"
    ws["H3"] = f"{opening_label} Closing MTM"
    ws["K3"] = f"Transactions- Buy/Sell {period_start} to {period_end}"
    ws["N3"] = f"{closing_label} Closing MTM"
    ws["Q3"] = "Taxation"

    for col in range(1, 21):
        ws.cell(3, col).fill = _SECTION_FILL
        ws.cell(3, col).border = _BORDER
    for cell in ("E3", "H3", "K3", "N3", "Q3"):
        _style_header(ws[cell])

    headers = [
        "Fund Name",
        "Family Name",
        "Asset Class",
        "Advisor",
        "Qty/Units",
        "Rate",
        "Total Cost",
        f"Units - Closing {opening_label}",
        f"NAV- {opening_label}",
        f"Amount-{opening_label} MTM",
        "Transaction Units",
        "Transaction NAV",
        "Transaction Amount",
        f"Units - Closing {closing_label}",
        f"NAV- {closing_label}",
        f"Amount-{closing_label} MTM",
        "Realized P/L",
        "Unrealized P/L",
        "Realized Tax",
        "Unrealized Tax",
    ]
    for col, value in enumerate(headers, 1):
        ws.cell(4, col, value)
        _style_header(ws.cell(4, col))
        ws.cell(4, col).alignment = Alignment(vertical="top", wrap_text=True)

    for row_idx, row in enumerate(report["tax_report"], 5):
        values = [
            row["asset_name"],
            row["family_name"],
            row["asset_class"],
            row["advisor"],
            row["qty_units"],
            row["rate"],
            row["total_cost"],
            row["opening_units"],
            row["opening_nav"],
            row["opening_amount"],
            row["transaction_units"],
            row["transaction_nav"],
            row["transaction_amount"],
            row["closing_units"],
            row["closing_nav"],
            row["closing_amount"],
            row["realized_pnl"],
            row["unrealized_pnl"],
            row["realized_tax"],
            row["unrealized_tax"],
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row_idx, col, value)
            ws.cell(row_idx, col).alignment = Alignment(vertical="top", wrap_text=(col <= 4))
            ws.cell(row_idx, col).border = _BORDER

    if not report["tax_report"]:
        ws.cell(5, 1, "No Tax Report rows available.")
        ws.merge_cells("A5:T5")
        ws.cell(5, 1).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(5, 1).border = _BORDER

    ws.freeze_panes = "A5"
    _autosize(ws, 12, 34)
    # Monetary columns use INR with Indian lakh/crore comma grouping; unit columns stay numeric.
    for col in (6, 7, 9, 10, 12, 13, 15, 16, 17, 18, 19, 20):
        for cell in ws.iter_cols(min_col=col, max_col=col, min_row=5, max_row=ws.max_row):
            for item in cell:
                item.number_format = _INR_NUMBER_FORMAT
    for col in (5, 8, 11, 14):
        for cell in ws.iter_cols(min_col=col, max_col=col, min_row=5, max_row=ws.max_row):
            for item in cell:
                item.number_format = '#,##0.00'
    return ws


def _build_fund_summary_sheet(workbook, report, display_unit="lakhs"):
    ws = workbook.create_sheet("Fund Type Summary")
    ws["A1"] = "Fund Type wise Summary"
    _style_title(ws["A1"])
    ws.merge_cells("A1:C1")

    unit_labels = {
        "amount": "₹ Amount",
        "lakhs": "₹ Lakhs",
        "crores": "₹ Crores",
    }
    divisors = {
        "amount": Decimal("1"),
        "lakhs": Decimal("100000"),
        "crores": Decimal("10000000"),
    }
    divisor = divisors[display_unit]

    ws["A2"] = f"Values in {unit_labels[display_unit]}"
    ws.merge_cells("A2:C2")
    ws["A2"].font = Font(italic=True, size=10)
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center")

    top_headers = ["Fund Type.V2", "Fund Name", "Total"]
    detail_headers = ["Asset class", "Asset name", "Current Market Value"]
    for col, value in enumerate(top_headers, 1):
        ws.cell(3, col, value)
        _style_header(ws.cell(3, col))
    for col, value in enumerate(detail_headers, 1):
        ws.cell(4, col, value)
        _style_header(ws.cell(4, col), fill=_SECTION_FILL)

    row_idx = 5
    grand_total = Decimal("0")
    for group in report["fund_type_summary"]:
        for item in group["rows"]:
            value = Decimal(str(item["total"] or 0))
            ws.cell(row_idx, 1, group["fund_type"])
            ws.cell(row_idx, 2, item["fund_name"])
            ws.cell(row_idx, 3, float(value / divisor))
            for col in range(1, 4):
                ws.cell(row_idx, col).border = _BORDER
            row_idx += 1

        subtotal = Decimal(str(group["subtotal"] or 0))
        ws.cell(row_idx, 1, f"{group['fund_type']} Subtotal")
        ws.cell(row_idx, 3, float(subtotal / divisor))
        for col in range(1, 4):
            ws.cell(row_idx, col).fill = _SUBTOTAL_FILL
            ws.cell(row_idx, col).border = _BORDER
        ws.cell(row_idx, 1).font = Font(bold=True)
        ws.cell(row_idx, 3).font = Font(bold=True)
        grand_total += subtotal
        row_idx += 1

    ws.cell(row_idx, 1, "Grand Total")
    ws.cell(row_idx, 3, float(grand_total / divisor))
    for col in range(1, 4):
        ws.cell(row_idx, col).fill = _GRAND_TOTAL_FILL
        ws.cell(row_idx, col).border = _BORDER
    ws.cell(row_idx, 1).font = Font(bold=True)
    ws.cell(row_idx, 3).font = Font(bold=True)

    for row_cells in ws.iter_rows(min_row=5, max_row=ws.max_row, min_col=3, max_col=3):
        for cell in row_cells:
            cell.number_format = '#,##0' if display_unit == 'amount' else '#,##0.00'
    ws.freeze_panes = "A5"
    _autosize(ws, 14, 48)
    return ws


def _parse_display_unit(request):
    display_unit = (request.query_params.get("display_unit") or "lakhs").strip().lower()
    if display_unit not in {"amount", "lakhs", "crores"}:
        raise ValidationError({"display_unit": "display_unit must be one of: amount, lakhs, crores."})
    return display_unit


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mis_report(request):
    family = _authorized_active_family(request.user)
    from_date, to_date = _parse_report_dates(request)
    try:
        report = MISReportService.build(family, from_date, to_date)
    except ValueError as exc:
        raise ValidationError({"detail": str(exc)})
    return Response(_serialize_report(report))


def _build_notes_sheet(workbook, report):
    ws = workbook.create_sheet("Notes")
    notes = report["notes"]

    ws["A1"] = notes["title"]
    _style_title(ws["A1"])
    ws.merge_cells("A1:F1")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    cursor = 3
    for section in notes["sections"]:
        ws.cell(cursor, 1, section["section_number"])
        ws.cell(cursor, 2, section["title"])
        ws.merge_cells(start_row=cursor, start_column=2, end_row=cursor, end_column=6)
        for col in range(1, 7):
            ws.cell(cursor, col).fill = _SECTION_FILL
            ws.cell(cursor, col).border = _BORDER
        ws.cell(cursor, 1).font = Font(bold=True)
        ws.cell(cursor, 2).font = Font(bold=True, size=12)
        cursor += 1

        headers = [
            "Sr. No",
            "Particulars",
            f"{section['unit_label']} {notes['opening_label']}",
            f"{section['unit_label']} {notes['closing_label']}",
            section["change_label"],
            "% Change",
        ]
        for col, value in enumerate(headers, 1):
            ws.cell(cursor, col, value)
            _style_header(ws.cell(cursor, col), fill=_SUBHEADER_FILL)
        cursor += 1

        for index, item in enumerate(section["items"], 1):
            values = [
                index,
                item["name"],
                float(item["opening_rate"]) if item["opening_rate"] is not None else None,
                float(item["closing_rate"]) if item["closing_rate"] is not None else None,
                float(item["change"]) if item["change"] is not None else None,
                float(item["percent_change"]) / 100 if item["percent_change"] is not None else None,
            ]
            for col, value in enumerate(values, 1):
                ws.cell(cursor, col, value)
                ws.cell(cursor, col).border = _BORDER
                ws.cell(cursor, col).alignment = Alignment(vertical="top", wrap_text=(col == 2))
            ws.cell(cursor, 3).number_format = '#,##0.00'
            ws.cell(cursor, 4).number_format = '#,##0.00'
            ws.cell(cursor, 5).number_format = '#,##0.00;(#,##0.00)'
            ws.cell(cursor, 6).number_format = '0.00%'
            cursor += 1

        if section.get("note"):
            ws.cell(cursor, 2, section["note"])
            ws.merge_cells(start_row=cursor, start_column=2, end_row=cursor, end_column=6)
            ws.cell(cursor, 2).alignment = Alignment(vertical="top", wrap_text=True)
            cursor += 1

        cursor += 1

    _autosize(ws, 12, 42)
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 42
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 14
    ws.freeze_panes = "A3"
    return ws


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mis_report_download(request):
    family = _authorized_active_family(request.user)
    from_date, to_date = _parse_report_dates(request)
    display_unit = _parse_display_unit(request)
    try:
        # Excel export must be self-contained: do not depend on the background
        # scheduler having run since the last request. Refresh the shared MIS
        # reference prices immediately before constructing the workbook.
        MISReportService.refresh_reference_prices()
        report = MISReportService.build(family, from_date, to_date)
    except ValueError as exc:
        raise ValidationError({"detail": str(exc)})

    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    _build_ips_sheet(workbook, report, display_unit)
    _build_data_sheet(workbook, report)
    _build_tax_report_sheet(workbook, report)
    _build_fund_summary_sheet(workbook, report, display_unit)
    _build_notes_sheet(workbook, report)

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
