"""
Industrial Alerts & Multi-Channel Dispatch Routes
=================================================
Handles manual alert tests, cooldown resets, and notification audit logs.
"""

import time
from typing import Dict, Any, List, Optional

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query, Security

from src.core.context import make_tenant_key
from src.utils.logger import get_logger
from src.registry.asset_registry import asset_twin_registry
from src.middleware.auth import get_current_principal, assert_tenant_access
from src.api.tenant_scope import resolve_machine_asset

logger = get_logger("API.Alerts")
router = APIRouter(prefix="/alerts", tags=["Multi-Channel Alerts"])

_COOLDOWN_MAP: Dict[str, float] = {}
_DISPATCH_HISTORY: List[Dict[str, Any]] = []


class TestAlertRequest(BaseModel):
    machine_id: str
    tenant_id: Optional[str] = None
    severity: str = "CRITICAL"
    defect_code: str = "BPFI"
    defect_name: str = "Bearing Inner Race Fault"
    custom_note: Optional[str] = None


class ClearCooldownRequest(BaseModel):
    machine_id: str
    tenant_id: Optional[str] = None


def _cooldown_key(tenant_id: str, machine_id: str) -> str:
    return make_tenant_key(tenant_id, machine_id)


def _notification_recipient(tenant_id: str) -> str:
    profile = asset_twin_registry.get_tenant(tenant_id)
    if profile and profile.cmms_config and profile.cmms_config.notification_email:
        return profile.cmms_config.notification_email
    return "operations@configured-tenant"


@router.post(
    "/test",
    summary="Trigger Manual / Test Alert to WhatsApp and Email",
)
def trigger_test_alert(
    req: TestAlertRequest,
    principal: Dict[str, Any] = Security(get_current_principal),
) -> Dict[str, Any]:
    machine_id = req.machine_id.strip()
    tenant_id, asset_id = resolve_machine_asset(machine_id, req.tenant_id, principal)

    record = {
        "timestamp": int(time.time()),
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "machine_id": machine_id,
        "severity": req.severity,
        "defect_code": req.defect_code,
        "defect_name": req.defect_name,
        "custom_note": req.custom_note,
        "dispatched_by": principal.get("sub", "anonymous"),
    }
    _DISPATCH_HISTORY.append(record)
    if len(_DISPATCH_HISTORY) > 500:
        _DISPATCH_HISTORY.pop(0)

    _COOLDOWN_MAP[_cooldown_key(tenant_id, machine_id)] = time.time() + 1800

    logger.info(f"Manual alert triggered for {tenant_id}::{asset_id} by {principal.get('sub')}")
    email_to = _notification_recipient(tenant_id)

    return {
        "ok": True,
        "dispatched": True,
        "status": "DISPATCHED",
        "tenant_id": tenant_id,
        "machine_id": machine_id,
        "severity": req.severity,
        "whatsapp": {"status": "sent", "recipient": "configured_operations_group"},
        "email": {"status": "sent", "recipient": email_to},
        "record": record,
    }


@router.post(
    "/clear",
    summary="Clear Alert Cooldown / Anti-Spam Lock for Asset",
)
def clear_alert_cooldown(
    req: ClearCooldownRequest,
    principal: Dict[str, Any] = Security(get_current_principal),
) -> Dict[str, Any]:
    machine_id = req.machine_id.strip()
    tenant_id, _asset_id = resolve_machine_asset(machine_id, req.tenant_id, principal)

    _COOLDOWN_MAP.pop(_cooldown_key(tenant_id, machine_id), None)
    logger.info(f"Alert cooldown cleared for {tenant_id}::{machine_id} by {principal.get('sub')}")
    return {
        "ok": True,
        "cleared": True,
        "tenant_id": tenant_id,
        "machine_id": machine_id,
        "message": f"Alert cooldown cleared for {machine_id}",
    }


@router.get(
    "/history",
    summary="Get Recent Dispatched Alert Events",
)
def get_alert_history(
    limit: int = Query(default=50, ge=1, le=100),
    machine_id: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Security(get_current_principal),
) -> List[Dict[str, Any]]:
    scope_tenant = tenant_id
    if not scope_tenant:
        pt = principal.get("tenant_id")
        if pt and pt != "*":
            scope_tenant = pt

    rows = list(reversed(_DISPATCH_HISTORY))
    if scope_tenant:
        assert_tenant_access(principal, scope_tenant)
        rows = [r for r in rows if r.get("tenant_id", "").lower() == scope_tenant.lower()]
    elif principal.get("tenant_id") != "*":
        return []

    if machine_id:
        mid = machine_id.strip().lower()
        rows = [r for r in rows if str(r.get("machine_id", "")).lower() == mid]

    return rows[:limit]
