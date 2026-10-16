"""
Persist fleet /ai/query history rows into the agent historian; optional LangGraph backfill.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.dynamic_orchestrator import global_orchestrator
from src.core.buffer import platform_buffer
from src.registry.asset_registry import asset_twin_registry
from src.services.fleet_machine_resolver import resolve_fleet_machine
from src.services.fleet_query_service import fleet_query_service
from src.services.fleet_telemetry_adapter import build_ingest_packet, fleet_row_to_signals
from src.utils.logger import get_logger

logger = get_logger("Services.FleetHistoryIngest")


def ingest_history_rows(
    machine_id: str,
    rows: List[Dict[str, Any]],
    *,
    tenant_hint: Optional[str] = None,
    run_pipeline: bool = False,
    pipeline_every_n: int = 0,
) -> Dict[str, Any]:
    resolved = resolve_fleet_machine(machine_id, tenant_hint)
    if not resolved:
        return {"ingested": 0, "pipelined": 0, "error": f"No twin mapped for fleet machine '{machine_id}'"}

    tenant_id, asset_id, twin, tenant_profile = resolved
    ingested = 0
    pipelined = 0

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        packet = build_ingest_packet(tenant_id, asset_id, row, twin)
        if not packet.signals:
            continue
        platform_buffer.push_telemetry(tenant_id, asset_id, packet.model_dump())
        ingested += 1

        if run_pipeline and (pipeline_every_n <= 1 or (i + 1) % pipeline_every_n == 0):
            try:
                global_orchestrator.run_pipeline(packet, twin, tenant_profile)
                pipelined += 1
            except Exception as e:
                logger.warning(f"Fleet backfill pipeline failed row {i}: {e}")

    return {
        "machine_id": machine_id,
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "ingested": ingested,
        "pipelined": pipelined,
    }


def sync_history_from_fleet_api(
    machine_id: str,
    *,
    source: str = "db",
    duration: str = "14d",
    max_points: int = 300,
    tenant_hint: Optional[str] = None,
    run_pipeline: bool = False,
    pipeline_every_n: int = 10,
) -> Dict[str, Any]:
    if not fleet_query_service.is_configured():
        return {"error": "Fleet API not configured"}
    rows = fleet_query_service.fetch_history(
        [machine_id],
        source=source,
        duration=duration,
        max_points=max_points,
    )
    result = ingest_history_rows(
        machine_id,
        rows,
        tenant_hint=tenant_hint,
        run_pipeline=run_pipeline,
        pipeline_every_n=pipeline_every_n,
    )
    result["source"] = source
    result["duration"] = duration
    result["fetched"] = len(rows)
    return result
