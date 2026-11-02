"""
Run LangGraph pipeline from fleet POST /ai/query latest + alerts (live snapshot).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.agents.dynamic_orchestrator import global_orchestrator
from src.api.tenant_scope import resolve_scoped_machine
from src.schemas.prediction import DiagnosticResult
from src.services.fleet_query_service import fleet_query_service
from src.services.fleet_telemetry_adapter import (
    alerts_to_breaches,
    build_ingest_packet,
    latest_row_from_machine_payload,
)


def _machine_block(payload: Optional[Dict[str, Any]], machine_id: str) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    want = machine_id.strip().lower()
    for m in payload.get("machines") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("machineId") or m.get("machine_id") or "").lower()
        if mid == want:
            return m
    return None


def try_fleet_live_diagnosis(
    machine_id: str,
    tenant_hint: Optional[str] = None,
) -> Tuple[Optional[DiagnosticResult], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Returns (diagnostic_result, fleet_alerts_as_breaches, fleet_machine_row).
    """
    if not fleet_query_service.is_configured():
        return None, [], None

    if not tenant_hint or not str(tenant_hint).strip():
        return None, [], None

    tenant_id, asset_id, twin, tenant_profile = resolve_scoped_machine(machine_id, tenant_hint)
    payload = fleet_query_service.query_snapshot(
        [machine_id],
        include=["latest", "alerts", "activity", "status", "parameters"],
    )
    block = _machine_block(payload, machine_id)
    if not block:
        return None, [], None

    latest = latest_row_from_machine_payload(block)
    if not latest:
        return None, alerts_to_breaches(block.get("alerts")), block

    packet = build_ingest_packet(tenant_id, asset_id, latest, twin)
    if not packet.signals:
        return None, alerts_to_breaches(block.get("alerts")), block

    result = global_orchestrator.run_pipeline(packet, twin, tenant_profile)
    return result, alerts_to_breaches(block.get("alerts")), block
