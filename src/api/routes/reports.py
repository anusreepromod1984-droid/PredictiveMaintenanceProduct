"""
Plant Reports — AI summary integration (service-reports → APMS → upstream /api/ai/query).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel, Field

from src.middleware.auth import get_current_principal, assert_tenant_access
from src.services.fleet_machine_resolver import resolve_fleet_machine
from src.services.plant_report_summarizer import generate_report_summary
from src.services.report_summary_store import save_machine_report_summary
from src.utils.logger import get_logger

logger = get_logger("API.Reports")

router = APIRouter(prefix="/reports", tags=["Plant Reports AI Summary"])


class KpiModel(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    value: Optional[Any] = None


class ReportSummaryRequest(BaseModel):
    company: Optional[str] = None
    report_type: Optional[str] = Field(default=None, alias="reportType")
    title: Optional[str] = None
    machine_ids: List[str] = Field(default_factory=list, alias="machineIds")
    date_range: Optional[Dict[str, Any]] = Field(default=None, alias="dateRange")
    timezone: Optional[str] = None
    selected_parameters: Optional[List[str]] = Field(default=None, alias="selectedParameters")
    kpis: Optional[List[KpiModel]] = None
    tenant_id: Optional[str] = Field(default=None, alias="tenantId")

    model_config = {"populate_by_name": True}


class ReportSummaryResponse(BaseModel):
    summary: str
    findings: List[str]
    graph: Dict[str, Any] = Field(default_factory=dict)


def _resolve_tenant(req: ReportSummaryRequest, principal: Dict[str, Any]) -> str:
    tenant = (
        req.tenant_id
        or principal.get("tenant_id")
    )
    if not tenant or tenant == "*":
        raise HTTPException(
            status_code=400,
            detail="tenantId (body), X-Tenant-Id header, or JWT tenant_id is required",
        )
    assert_tenant_access(principal, tenant)
    return str(tenant).lower()


def _assert_machines(tenant_id: str, machine_ids: List[str]) -> None:
    if not machine_ids:
        raise HTTPException(status_code=400, detail="machineIds must be a non-empty list")
    for mid in machine_ids:
        resolved = resolve_fleet_machine(mid, tenant_id)
        if not resolved:
            raise HTTPException(
                status_code=404,
                detail=f"No agent twin mapped for machine '{mid}' under tenant '{tenant_id}'",
            )
        t_id, _, _, _ = resolved
        if t_id.lower() != tenant_id.lower():
            raise HTTPException(status_code=403, detail=f"Machine '{mid}' belongs to another tenant")


@router.post(
    "/summary",
    response_model=ReportSummaryResponse,
    summary="Generate AI plant report summary from upstream /api/ai/query data",
)
def create_report_summary(
    req: ReportSummaryRequest,
    principal: Dict[str, Any] = Security(get_current_principal),
) -> ReportSummaryResponse:
    """
    Authenticated with X-API-Key (same key as ai-query / AI_SERVICE_API_KEY).
    Fetches history, alerts, and latest parameters for the report window, then returns
    narrative summary + bullet findings (target < 60s).
    """
    tenant_id = _resolve_tenant(req, principal)
    machine_ids = [m.strip() for m in req.machine_ids if m and str(m).strip()]
    _assert_machines(tenant_id, machine_ids)

    date_range_dict = dict(req.date_range) if req.date_range else None
    kpi_list = [k.model_dump(exclude_none=True) for k in req.kpis] if req.kpis else None

    try:
        result = generate_report_summary(
            company=req.company,
            report_type=req.report_type,
            title=req.title,
            machine_ids=machine_ids,
            date_range=date_range_dict,
            timezone_name=req.timezone,
            selected_parameters=req.selected_parameters,
            kpis=kpi_list,
            tenant_id=tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    period = date_range_dict
    for mid in machine_ids:
        save_machine_report_summary(
            tenant_id,
            mid,
            summary=result["summary"],
            findings=result["findings"],
            title=req.title,
            report_type=req.report_type,
            company=req.company,
            period=period,
            graph=result.get("graph"),
        )

    logger.info(
        f"Report summary generated for tenant={tenant_id} machines={machine_ids} "
        f"findings={len(result['findings'])}"
    )
    return ReportSummaryResponse(
        summary=result["summary"],
        findings=result["findings"],
        graph=result.get("graph") or {},
    )
