"""
Settings Router — per-tenant runtime preferences (e.g. alert language).
"""
from __future__ import annotations

import threading
from typing import Dict

from fastapi import APIRouter, HTTPException, Query, Security
from pydantic import BaseModel

from src.middleware.auth import get_current_principal, assert_tenant_access, require_role
from src.utils.logger import get_logger

logger = get_logger("APMS.Settings")

router = APIRouter(prefix="/settings", tags=["settings"])

_lock = threading.Lock()
_state: Dict[str, str] = {}


def get_alert_language(tenant_id: str = "default") -> str:
    key = tenant_id.strip().lower() or "default"
    with _lock:
        return _state.get(key, _state.get("default", "en"))


class AlertLanguagePayload(BaseModel):
    language: str


class AlertLanguageResponse(BaseModel):
    tenant_id: str
    alert_language: str
    message: str


@router.get("/alert-language", response_model=AlertLanguageResponse, summary="Get alert language")
def get_alert_language_route(
    tenant_id: str = Query(..., description="Tenant scope for alert language"),
    principal: Dict = Security(get_current_principal),
) -> AlertLanguageResponse:
    assert_tenant_access(principal, tenant_id)
    lang = get_alert_language(tenant_id)
    return AlertLanguageResponse(tenant_id=tenant_id, alert_language=lang, message="OK")


@router.post("/alert-language", response_model=AlertLanguageResponse, summary="Set alert language")
def set_alert_language(
    payload: AlertLanguagePayload,
    tenant_id: str = Query(..., description="Tenant scope for alert language"),
    principal: Dict = Security(require_role("admin")),
) -> AlertLanguageResponse:
    assert_tenant_access(principal, tenant_id)
    code = payload.language.lower().split("-")[0].strip() or "en"
    key = tenant_id.strip().lower()
    with _lock:
        _state[key] = code
    logger.info(f"[Settings] Alert language for tenant '{key}' set to '{code}'")
    return AlertLanguageResponse(
        tenant_id=tenant_id,
        alert_language=code,
        message=f"Alert language set to '{code}'",
    )
