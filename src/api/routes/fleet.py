"""
Fleet PDM AI Query proxy — capabilities, MQTT connectivity, live snapshot, history.
Uses upstream POST /ai/query (same API key as threshold provider).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.middleware.auth import assert_tenant_access, get_current_principal
from src.services.fleet_history_ingest import sync_history_from_fleet_api
from src.services.fleet_machine_resolver import (
    list_registered_fleet_machine_ids,
    resolve_fleet_machine,
)
from src.services.fleet_query_service import (
    fleet_query_service,
    DEFAULT_SNAPSHOT_INCLUDES,
)

router = APIRouter(prefix="/fleet", tags=["Upstream Fleet /ai/query"])


def _require_fleet():
    if not fleet_query_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Fleet API not configured. Set AI_SERVICE_BASE_URL and AI_SERVICE_API_KEY.",
        )


def _principal_tenant_scope(principal: Dict[str, Any]) -> Optional[str]:
    t = principal.get("tenant_id")
    if t and t != "*":
        return str(t).lower()
    return None


def _assert_machine_access(principal: Dict[str, Any], machine_id: str, tenant_hint: Optional[str] = None):
    resolved = resolve_fleet_machine(machine_id, tenant_hint)
    if not resolved:
        scope = _principal_tenant_scope(principal)
        if scope and machine_id.strip().lower() in list_registered_fleet_machine_ids(scope):
            assert_tenant_access(principal, scope)
            return
        if principal.get("tenant_id") == "*":
            raise HTTPException(status_code=404, detail=f"No agent twin mapped for machine '{machine_id}'")
        raise HTTPException(
            status_code=404,
            detail=f"No agent twin mapped for machine '{machine_id}' (provide tenant_id if ambiguous)",
        )
    tenant_id, _asset_id, _twin, _tp = resolved
    assert_tenant_access(principal, tenant_id)


@router.get("/capabilities")
def fleet_capabilities(
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    """GET /ai/query/capabilities — filtered to tenant-registered machines in production."""
    _require_fleet()
    caps = fleet_query_service.get_capabilities()
    if not caps:
        raise HTTPException(status_code=502, detail="Upstream capabilities request failed")

    scope = tenant_id or _principal_tenant_scope(principal)
    if scope:
        assert_tenant_access(principal, scope)
        allowed = set(list_registered_fleet_machine_ids(scope))
        machines = caps.get("machines") or []
        if isinstance(machines, list):
            filtered = []
            for item in machines:
                if isinstance(item, str) and item.lower() in allowed:
                    filtered.append(item)
                elif isinstance(item, dict):
                    mid = str(item.get("machineId") or "").lower()
                    if mid in allowed:
                        filtered.append(item)
            caps = {**caps, "machines": filtered}
    return caps


@router.get("/machines")
def fleet_visible_machines(
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    _require_fleet()
    scope = tenant_id or _principal_tenant_scope(principal)
    if scope:
        assert_tenant_access(principal, scope)
        return {"machine_ids": list_registered_fleet_machine_ids(scope)}

    if principal.get("tenant_id") != "*":
        raise HTTPException(status_code=400, detail="tenant_id required")
    upstream = fleet_query_service.list_visible_machine_ids()
    return {"machine_ids": upstream}


@router.get("/machines/{machine_id}/resolve")
def fleet_resolve_machine(
    machine_id: str,
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    """Map fleet machineId → agent tenant_id + asset_id."""
    resolved = resolve_fleet_machine(machine_id, tenant_id)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"No mapping for '{machine_id}'")
    t_id, a_id, twin, _tp = resolved
    assert_tenant_access(principal, t_id)
    return {
        "machine_id": machine_id,
        "tenant_id": t_id,
        "asset_id": a_id,
        "asset_name": twin.name,
        "metadata": twin.metadata,
    }


@router.get("/machines/{machine_id}/mqtt")
def fleet_machine_mqtt(
    machine_id: str,
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    _assert_machine_access(principal, machine_id, tenant_id)
    _require_fleet()
    payload = fleet_query_service.query_connectivity([machine_id])
    if not payload:
        raise HTTPException(status_code=502, detail="Upstream query failed")
    cfg = fleet_query_service.extract_mqtt_for_machine(payload, machine_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"No MQTT block for machine '{machine_id}'")
    return cfg


@router.get("/machines/{machine_id}/snapshot")
def fleet_machine_snapshot(
    machine_id: str,
    include: Optional[str] = Query(
        default=None,
        description="Comma-separated includes; default = meta,latest,status,activity,alerts,parameters",
    ),
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    _assert_machine_access(principal, machine_id, tenant_id)
    _require_fleet()
    inc: List[str] = [s.strip() for s in include.split(",") if s.strip()] if include else DEFAULT_SNAPSHOT_INCLUDES
    payload = fleet_query_service.query_snapshot([machine_id], include=inc)
    if not payload:
        raise HTTPException(status_code=502, detail="Upstream query failed")
    return payload


@router.get("/machines/{machine_id}/history")
def fleet_machine_history(
    machine_id: str,
    source: str = Query(default="db", pattern="^(live|db)$"),
    duration: str = Query(default="14d"),
    max_points: int = Query(default=300, ge=1, le=5000),
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    _assert_machine_access(principal, machine_id, tenant_id)
    _require_fleet()
    rows = fleet_query_service.fetch_history(
        [machine_id],
        source=source,
        duration=duration,
        max_points=max_points,
    )
    return {
        "machine_id": machine_id,
        "source": source,
        "duration": duration,
        "count": len(rows),
        "rows": rows,
    }


@router.post("/machines/{machine_id}/history/ingest")
def fleet_machine_history_ingest(
    machine_id: str,
    source: str = Query(default="db", pattern="^(live|db)$"),
    duration: str = Query(default="14d"),
    max_points: int = Query(default=300, ge=1, le=5000),
    run_pipeline: bool = Query(default=False),
    pipeline_every_n: int = Query(default=10, ge=1, le=500),
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    _assert_machine_access(principal, machine_id, tenant_id)
    if "ingest" not in principal.get("roles", []) and "admin" not in principal.get("roles", []):
        if principal.get("tenant_id") != "*":
            raise HTTPException(status_code=403, detail="Role 'ingest' or 'admin' required")
    return sync_history_from_fleet_api(
        machine_id,
        source=source,
        duration=duration,
        max_points=max_points,
        tenant_hint=tenant_id,
        run_pipeline=run_pipeline,
        pipeline_every_n=pipeline_every_n,
    )


@router.post("/query")
def fleet_raw_query(
    body: Dict[str, Any],
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    _require_fleet()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body required")
    machine_ids = body.get("machineIds") or []
    if isinstance(machine_ids, list):
        for mid in machine_ids:
            if isinstance(mid, str):
                _assert_machine_access(principal, mid, None)
    payload = fleet_query_service.query(body)
    if not payload:
        raise HTTPException(status_code=502, detail="Upstream query failed")
    return payload
