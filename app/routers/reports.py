"""Informes de solo lectura del usuario y organización autenticados."""

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.report_excel import monthly_excel, weekly_excel
from app.reports import month_report, week_report
from app.routers.work import CurrentUser, render
from app.web_auth import DatabaseSession
from app.worklog import FormError, parse_date


router = APIRouter(prefix="/reports")
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def validate_query(request, allowed):
    # Rechazar IDs enviados por el navegador y parámetros ambiguos o desconocidos.
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise HTTPException(400, "Parámetros de informe inválidos. Selecciona el período desde Informes.")


def selected_month(db, user, month):
    try:
        return month_report(db, user, month)
    except FormError as error:
        raise HTTPException(400, error.message) from None


def download(buffer, filename):
    # El nombre se compone exclusivamente de prefijos fijos y fechas ya validadas.
    return Response(buffer.getvalue(), media_type=XLSX_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/month")
def reports_month(request: Request, user: CurrentUser, db: DatabaseSession, month: str = ""):
    validate_query(request, {"month"})
    return render(request, user, "reports_month.html", **selected_month(db, user, month))


@router.get("/week/export")
def export_week(request: Request, user: CurrentUser, db: DatabaseSession, week: str = ""):
    validate_query(request, {"week"})
    try:
        anchor = parse_date(week, "week") if week else date.today()
    except FormError as error:
        raise HTTPException(400, error.message) from None
    report = week_report(db, user, anchor)
    filename = f'Atenea_Horas_{report["week_start"].isoformat()}_al_{report["week_end"].isoformat()}.xlsx'
    return download(weekly_excel(user, report), filename)


@router.get("/month/export")
def export_month(request: Request, user: CurrentUser, db: DatabaseSession, month: str = ""):
    validate_query(request, {"month"})
    report = selected_month(db, user, month)
    return download(monthly_excel(user, report), f'Atenea_Horas_{report["month"]}.xlsx')
