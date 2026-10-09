"""
Unit Test: Multi-Tenant Data Isolation
Ensures zero data leakage between different clients.
"""

import pytest
from src.core.context import make_tenant_key, parse_tenant_key
from src.core.buffer import platform_buffer
from src.schemas.telemetry import IngestTelemetryPacket
from src.schemas.asset import AssetDigitalTwin
from src.registry.asset_registry import asset_twin_registry
from src.agents.dynamic_orchestrator import global_orchestrator


def test_compound_key_generation():
    k1 = make_tenant_key("Propel_Industries", "Crusher_01")
    k2 = make_tenant_key("Nest_Group", "Crusher_01")
    assert k1 != k2
    assert k1 == "propel_industries::Crusher_01"
    assert k2 == "nest_group::Crusher_01"
    
    t_id, a_id = parse_tenant_key(k1)
    assert t_id == "propel_industries"
    assert a_id == "Crusher_01"


def test_isolated_ring_buffer():
    # Push data for Propel
    platform_buffer.push_telemetry("propel_industries", "asset_x", {"signals": {"vib": 10.5}})
    # Push data for Nest
    platform_buffer.push_telemetry("nest_group", "asset_x", {"signals": {"vac": -85.0}})

    propel_tail = platform_buffer.get_telemetry_tail("propel_industries", "asset_x")
    nest_tail = platform_buffer.get_telemetry_tail("nest_group", "asset_x")

    assert len(propel_tail) > 0
    assert len(nest_tail) > 0
    assert "vib" in propel_tail[-1]["signals"]
    assert "vac" not in propel_tail[-1]["signals"]
    assert "vac" in nest_tail[-1]["signals"]
    assert "vib" not in nest_tail[-1]["signals"]


def test_end_to_end_tenant_execution():
    twin_propel = asset_twin_registry.get_asset("propel_industries", "jaw_crusher_01")
    twin_nest = asset_twin_registry.get_asset("nest_group", "smt_line1_pnp")
    assert twin_propel is not None
    assert twin_nest is not None

    p_packet = IngestTelemetryPacket(
        tenant_id="propel_industries",
        asset_id="jaw_crusher_01",
        signals={"drive_vib_de_rms": 6.2, "motor_kw": 80.0}
    )
    p_result = global_orchestrator.run_pipeline(p_packet, twin_propel, None)

    n_packet = IngestTelemetryPacket(
        tenant_id="nest_group",
        asset_id="smt_line1_pnp",
        signals={"head_vacuum_kpa": -88.0, "placement_cycle_ms": 22.0}
    )
    n_result = global_orchestrator.run_pipeline(n_packet, twin_nest, None)

    assert p_result.tenant_id == "propel_industries"
    assert n_result.tenant_id == "nest_group"
    assert "NORMAL" in p_result.operating_regime
    assert "NORMAL" in n_result.operating_regime
