"""
Platform webhooks: read an uploaded service report, then explain its measured effect.
Bare JSON in and out. Auth: X-API-Key (same key as ai-query).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Security
from pydantic import BaseModel, Field

from src.middleware.auth import get_current_principal
from src.services.service_report_analyze import analyze_service_report
from src.services.service_report_effect import build_service_effect
from src.utils.logger import get_logger

logger = get_logger("API.ServiceReports")

router = APIRouter(prefix="/service-reports", tags=["Service report webhooks"])


class MachineRef(BaseModel):
    id: str
    name: str
    model: Optional[str] = None


class FileRef(BaseModel):
    url: str
    type: str
    name: str
    size: int


class Hints(BaseModel):
    serviceDate: Optional[str] = None
    vendor: Optional[str] = None


class AnalyzeBody(BaseModel):
    documentId: str
    companySlug: str
    machine: MachineRef
    files: List[FileRef] = Field(min_length=1)
    hints: Hints


class PartIn(BaseModel):
    name: str
    qty: Optional[float] = None


class ServiceIn(BaseModel):
    serviceDate: str
    type: str
    vendor: Optional[str] = None
    workDone: List[str] = Field(default_factory=list)
    partsReplaced: List[PartIn] = Field(default_factory=list)
    issuesFound: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    nextServiceDue: Optional[str] = None


class EffectBody(BaseModel):
    documentId: str
    serviceRecordId: str
    companySlug: str
    machine: MachineRef
    service: ServiceIn
    comparison: Dict[str, Any]


@router.post("/analyze")
def analyze(
    body: AnalyzeBody,
    principal: Dict[str, Any] = Security(get_current_principal),
) -> Dict[str, Any]:
    logger.info(
        f"Service report analyze document={body.documentId} company={body.companySlug} "
        f"files={len(body.files)} caller={principal.get('sub')}"
    )
    try:
        return analyze_service_report(body.model_dump())
    except Exception as e:
        logger.warning(f"Service report analyze failed ({e})")
        from src.services.service_report_analyze import _empty
        return _empty()


@router.post("/effect")
def effect(
    body: EffectBody,
    principal: Dict[str, Any] = Security(get_current_principal),
) -> Dict[str, Any]:
    logger.info(
        f"Service report effect record={body.serviceRecordId} company={body.companySlug} "
        f"caller={principal.get('sub')}"
    )
    return build_service_effect(body.model_dump())
