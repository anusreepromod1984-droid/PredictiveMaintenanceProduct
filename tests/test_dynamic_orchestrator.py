"""
Unit Test: LangGraph StateGraph Dynamic Routing & Quality Gate
"""

import pytest
from src.schemas.telemetry import IngestTelemetryPacket
from src.registry.asset_registry import asset_twin_registry
from src.agents.dynamic_orchestrator import global_orchestrator


def test_alpha_namur_cut_wire_gate():
    twin = asset_twin_registry.get_asset("propel_industries", "jaw_crusher_01")
    tenant = asset_twin_registry.get_tenant("propel_industries")

    # Loop current = 2.0 mA (<3.6 mA is NAMUR cut wire)
    packet = IngestTelemetryPacket(
        tenant_id="propel_industries",
        asset_id="jaw_crusher_01",
        signals={
            "drive_vib_de_rms": 0.0,
            "namur_vib_loop_ma": 2.0
        }
    )
    res = global_orchestrator.run_pipeline(packet, twin, tenant)
    assert res.health_status == "SENSOR_FAULT"
    assert any(f.fault_code == "SENSOR_HARDWARE_FAULT" for f in res.active_faults)


def test_beta_idle_regime_gate():
    twin = asset_twin_registry.get_asset("nest_group", "smt_line1_pnp")
    tenant = asset_twin_registry.get_tenant("nest_group")

    packet = IngestTelemetryPacket(
        tenant_id="nest_group",
        asset_id="smt_line1_pnp",
        signals={"head_vacuum_kpa": 0.0},
        operating_state="idle"
    )
    res = global_orchestrator.run_pipeline(packet, twin, tenant)
    assert res.health_status == "IDLE"
    assert res.operating_regime == "IDLE"
    assert len(res.active_faults) == 0
