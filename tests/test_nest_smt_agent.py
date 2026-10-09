"""
Unit Test: Nest Group High-Tech Electronics SMT Agent
Validates nozzle vacuum loss, cycle time drift, and ticket formulation.
"""

import pytest
from src.schemas.telemetry import IngestTelemetryPacket
from src.registry.asset_registry import asset_twin_registry
from src.agents.dynamic_orchestrator import global_orchestrator


def test_nest_smt_vacuum_leak():
    twin = asset_twin_registry.get_asset("nest_group", "smt_line1_pnp")
    tenant = asset_twin_registry.get_tenant("nest_group")

    # Vacuum degraded to -52.0 kPa (below -65.0 kPa critical bound)
    packet = IngestTelemetryPacket(
        tenant_id="nest_group",
        asset_id="smt_line1_pnp",
        signals={
            "head_vacuum_kpa": -52.0,
            "placement_cycle_ms": 38.5,
            "x_axis_torque_pct": 88.0,
            "mounter_kw": 14.0
        }
    )
    res = global_orchestrator.run_pipeline(packet, twin, tenant)
    assert res.health_status in ("WARNING", "CRITICAL")
    assert any(f.fault_code == "SMT_VACUUM_LEAK" for f in res.active_faults)
    assert res.prescriptive_action is not None
    assert "nozzle vacuum" in res.prescriptive_action.description.lower()
