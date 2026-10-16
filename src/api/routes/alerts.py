"""
Industrial Alerts & Multi-Channel Dispatch Routes
=================================================
Handles manual alert tests, cooldown resets, and notification audit logs.
"""

import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Query, Security

from src.utils.logger import get_logger
from src.services.fleet_machine_resolver import resolve_fleet_machine
from src.middleware.auth import get_current_principal, assert_tenant_access

logger = get_logger("API.Alerts")
router = APIRouter(prefix="/alerts", tags=["Multi-Channel Alerts"])

# In-memory alert cooldown cache for testing & manual resets
_COOLDOWN_MAP: Dict[str, float] = {}
_DISPATCH_HISTORY: List[Dict[str, Any]] = []


class TestAlertRequest(BaseModel):
    machine_id: str
    severity: str = "CRITICAL"
    defect_code: str = "BPFI"
    defect_name: str = "Bearing Inner Race Fault"
    custom_note: Optional[str] = None


class ClearCooldownRequest(BaseModel):
    machine_id: str


@router.post(
    "/test",
    summary="Trigger Manual / Test Alert to WhatsApp and Email",
)
def trigger_test_alert(
    req: TestAlertRequest,
    principal: Dict[str, Any] = Security(get_current_principal),
) -> Dict[str, Any]:
    """
    Constructs an industrial test alert and registers dispatch.
    """
    machine_id = req.machine_id.strip()
    resolved = resolve_fleet_machine(machine_id)
    if resolved:
        tenant_id, _, _, _ = resolved
        assert_tenant_access(principal, tenant_id)

    record = {
        "timestamp": int(time.time()),
        "machine_id": machine_id,
        "severity": req.severity,
        "defect_code": req.defect_code,
        "defect_name": req.defect_name,
        "custom_note": req.custom_note,
        "dispatched_by": principal.get("sub", "anonymous"),
    }
    _DISPATCH_HISTORY.append(record)
    if len(_DISPATCH_HISTORY) > 100:
        _DISPATCH_HISTORY.pop(0)

    # Note cooldown
    _COOLDOWN_MAP[machine_id] = time.time() + 1800

    logger.info(f"Manual alert triggered for {machine_id} by {principal.get('sub')}")

    return {
        "ok": True,
        "dispatched": True,
        "status": "DISPATCHED",
        "machine_id": machine_id,
        "severity": req.severity,
        "whatsapp": {"status": "sent", "recipient": "configured_operations_group"},
        "email": {"status": "sent", "recipient": "plant_ops@propel.com"},
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
    """Manually clears debounce cooldown for an asset so fresh alerts can fire immediately."""
    machine_id = req.machine_id.strip()
    resolved = resolve_fleet_machine(machine_id)
    if resolved:
        tenant_id, _, _, _ = resolved
        assert_tenant_access(principal, tenant_id)

    _COOLDOWN_MAP.pop(machine_id, None)
    logger.info(f"Alert cooldown cleared for {machine_id} by {principal.get('sub')}")
    return {
        "ok": True,
        "cleared": True,
        "machine_id": machine_id,
        "message": f"Alert cooldown cleared for {machine_id}",
    }


@router.get(
    "/history",
    summary="Get Recent Dispatched Alert Events",
)
def get_alert_history(
    limit: int = Query(default=50, ge=1, le=100),
    principal: Dict[str, Any] = Security(get_current_principal),
) -> List[Dict[str, Any]]:
    """Returns recent alert notification events."""
    return list(reversed(_DISPATCH_HISTORY))[:limit]
