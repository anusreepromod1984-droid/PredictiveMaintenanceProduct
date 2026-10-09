from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from src.schemas.telemetry import IngestTelemetryPacket
from src.schemas.prediction import DiagnosticResult
from src.registry.asset_registry import asset_twin_registry
from src.agents.dynamic_orchestrator import global_orchestrator
from src.core.buffer import platform_buffer

router = APIRouter(tags=["Telemetry Ingestion"])


@router.post("/telemetry/ingest", response_model=DiagnosticResult)
def ingest_and_diagnose(packet: IngestTelemetryPacket):
    """
    Primary ingestion endpoint.
    Accepts arbitrary sensor parameters, loads the matching asset twin,
    and executes the 4-agent LangGraph pipeline in real time.
    """
    twin = asset_twin_registry.get_asset(packet.tenant_id, packet.asset_id)
    if not twin:
        raise HTTPException(
            status_code=404,
            detail=f"Asset '{packet.asset_id}' under tenant '{packet.tenant_id}' is not registered."
        )

    tenant_profile = asset_twin_registry.get_tenant(packet.tenant_id)
    result = global_orchestrator.run_pipeline(packet, twin, tenant_profile)
    return result


@router.get("/tenants/{tenant_id}/assets/{asset_id}/history")
def get_telemetry_history(tenant_id: str, asset_id: str, limit: int = 50):
    """Retrieves the recent telemetry time-series tail from the tenant-isolated ring buffer."""
    tail = platform_buffer.get_telemetry_tail(tenant_id, asset_id, limit=limit)
    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "count": len(tail),
        "telemetry": tail
    }
