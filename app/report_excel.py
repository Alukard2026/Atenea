"""Exportaciones XLSX en memoria. Los valores calculados son instantáneas Decimal."""

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from math import ceil

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

from app.workdays import DAY_NAMES


GREEN = "234D43"
PALE = "EEF4F1"
LINE = Side(style="thin", color="DCE4DF")
HEADERS = ("Fecha", "Día", "Cliente / Empresa", "Proyecto / Expediente", "Descripción del trabajo", "Horas", "Facturable")
WIDTHS = (14, 14, 28, 28, 64, 14, 14)


def cell_value(cell, value):
    """Forzar TODOS los textos a inline strings, incluso =, +, -, @ y espacios iniciales.

    No se crean fórmulas, hipervínculos ni macros. Se sustituyen únicamente los
    controles que XML no admite, conservando tabuladores y saltos de línea.
    """
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("\ufffd", value)
        value = value.replace("\ufffe", "\ufffd").replace("\uffff", "\ufffd")
        cell.value = value
        cell.data_type = "s"
        cell.quotePrefix = value.lstrip().startswith(("=", "+", "-", "@"))
    else:
        cell.value = value
    cell.font = Font(name="Arial", size=10, color="243B33")
    cell.alignment = Alignment(vertical="top", wrap_text=True,
                               horizontal="right" if isinstance(value, (Decimal, int)) else "left")
    if isinstance(value, Decimal):
        cell.number_format = "0.00"
    elif isinstance(value, datetime):
        cell.number_format = "dd/mm/yyyy hh:mm"
    elif isinstance(value, date):
        cell.number_format = "dd/mm/yyyy"


def row_values(sheet, row, values, *, header=False, total=False):
    for column, value in enumerate(values, 1):
        cell = sheet.cell(row, column)
        cell_value(cell, value)
        if header or total:
            cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF" if header else GREEN)
            cell.fill = PatternFill("solid", fgColor=GREEN if header else PALE)
            cell.border = Border(bottom=LINE, top=LINE)
        if header:
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        else:
            cell.border = Border(bottom=LINE)
    lines = max((sum(max(1, ceil(len(line) / max(1, WIDTHS[index] - 3))) for line in str(value or "").split("\n"))
                 for index, value in enumerate(values)), default=1)
    # Excel limita la altura de fila a 409 puntos. El texto completo permanece en la celda.
    sheet.row_dimensions[row].height = min(409, max(30 if header else 24, lines * 15 + 8))


def heading(sheet, row, text):
    cell_value(sheet.cell(row, 1), text)
    sheet.cell(row, 1).font = Font(name="Arial", size=14, bold=True, color=GREEN)
    sheet.cell(row, 1).alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[row].height = 28


def base_sheet(workbook, name, title, user, start, end, generated):
    sheet = workbook.create_sheet(name)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.sheet_properties.tabColor = GREEN
    for index, width in enumerate(WIDTHS, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    heading(sheet, 2, title)
    # Metadatos en columnas anchas para nombres extensos; nunca se incluyen email ni IDs.
    for row, label, value in ((4, "Organización", user.organization.name), (5, "Usuario", user.full_name)):
        cell_value(sheet.cell(row, 1), label)
        sheet.merge_cells(start_row=row, start_column=3, end_row=row, end_column=7)
        cell_value(sheet.cell(row, 3), value)
        sheet.row_dimensions[row].height = 34
    row_values(sheet, 6, ("Desde", start, "Hasta", end, "Generado (UTC)", generated))
    sheet.cell(6, 6).number_format = "dd/mm/yyyy"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_margins = PageMargins(left=.3, right=.3, top=.4, bottom=.4, header=.2, footer=.2)
    sheet.oddFooter.center.text = "Página &P de &N"
    return sheet


def detail_table(sheet, row, entries):
    header = row
    row_values(sheet, row, HEADERS, header=True)
    for entry in entries:
        row += 1
        row_values(sheet, row, (
            entry.work_date, DAY_NAMES[entry.work_date.weekday()], entry.client.name,
            entry.project.name if entry.project else "Sin proyecto",
            entry.description or entry.activity, entry.hours, "Sí" if entry.billable else "No",
        ))
    if entries:
        sheet.auto_filter.ref = f"A{header}:G{row}"
    else:
        row += 1
        cell_value(sheet.cell(row, 3), "Sin registros en este período.")
    sheet.freeze_panes = f"C{header + 1}"
    sheet.print_title_rows = f"{header}:{header}"
    return row + 1


def total_rows(sheet, row, report, label):
    for title, value in ((label, report["total"]), ("Total facturable", report["billable_total"]),
                         ("Total no facturable", report["non_billable_total"])):
        row_values(sheet, row, (None, None, None, None, title, value, None), total=True)
        row += 1
    return row


def workbook_bytes(workbook):
    for sheet in workbook:
        sheet.print_area = f"A1:G{sheet.max_row}"
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def weekly_excel(user, report):
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "Atenea"
    sheet = base_sheet(workbook, "Semanal", "Informe semanal de horas", user,
                       report["week_start"], report["week_end"], datetime.now(timezone.utc).replace(tzinfo=None))
    row = detail_table(sheet, 8, report["entries"])
    row = total_rows(sheet, row, report, "Total semanal")
    heading(sheet, row + 1, "Totales diarios")
    row += 3
    row_values(sheet, row, ("Fecha", "Día", "Tipo de día", None, None, "Horas", None), header=True)
    for day in report["days"]:
        row += 1
        row_values(sheet, row, (day["date"], day["label"], "Laborable" if day["is_workday"] else "No laborable",
                                None, None, day["total"], None))
    row += 1
    for label, key in (("Total en días laborables", "working_total"), ("Total en días no laborables", "non_working_total")):
        row_values(sheet, row, (None, None, None, None, label, report[key], None), total=True)
        row += 1
    sheet.merge_cells(start_row=row + 1, start_column=3, end_row=row + 1, end_column=7)
    cell_value(sheet.cell(row + 1, 3), "Días laborables según la configuración actual de la organización.")
    sheet.row_dimensions[row + 1].height = 30
    return workbook_bytes(workbook)


def monthly_excel(user, report):
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "Atenea"
    generated = datetime.now(timezone.utc).replace(tzinfo=None)
    summary = base_sheet(workbook, "Resumen", "Informe mensual de horas", user, report["start"], report["end"], generated)
    cell_value(summary.cell(8, 3), report["month_label"])
    total_rows(summary, 10, report, "Total del mes")
    row_values(summary, 13, (None, None, None, None, "Días con actividad", report["active_days"], None))
    heading(summary, 15, "Horas por cliente")
    row_values(summary, 16, (None, None, "Cliente / Empresa", "Código", None, "Horas del mes", None), header=True)
    row = 17
    for client in report["clients"]:
        row_values(summary, row, (None, None, client["name"], client["code"], None, client["total"], None))
        row += 1
    if not report["clients"]:
        cell_value(summary.cell(row, 3), "Sin registros en este mes.")
        row += 1
    row_values(summary, row, (None, None, None, None, "Total por cliente", report["total"], None), total=True)
    heading(summary, row + 2, "Horas por semana")
    summary.merge_cells(start_row=row + 3, start_column=3, end_row=row + 3, end_column=7)
    cell_value(summary.cell(row + 3, 3), "Solo se suman las fechas que pertenecen al mes seleccionado.")
    summary.row_dimensions[row + 3].height = 30
    row += 5
    row_values(summary, row, ("Desde", "Hasta", None, None, "Semana calendario completa", "Horas del mes", None), header=True)
    for week in report["weeks"]:
        row += 1
        row_values(summary, row, (week["start"], week["end"], None, None, "Lunes a domingo", week["month_total"], None))
    row_values(summary, row + 1, (None, None, None, None, "Total por semana", report["total"], None), total=True)
    summary.freeze_panes = "C9"
    detail = base_sheet(workbook, "Detalle", "Detalle mensual de horas", user, report["start"], report["end"], generated)
    row = detail_table(detail, 8, report["entries"])
    total_rows(detail, row, report, "Total del mes")
    return workbook_bytes(workbook)
