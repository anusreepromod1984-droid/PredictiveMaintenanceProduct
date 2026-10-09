"""
Unit Test: Propel Industries Mining Machinery Agent
Validates ISO 10816-6 severity evaluation, bearing thermal alarms, and SAP PM dispatch.
"""

import pytest
from src.schemas.telemetry import IngestTelemetryPacket
from src.registry.asset_registry import asset_twin_registry
from src.agents.dynamic_orchestrator import global_orchestrator


def test_propel_crusher_normal_operation():
    twin = asset_twin_registry.get_asset("propel_industries", "jaw_crusher_01")
    tenant = asset_twin_registry.get_tenant("propel_industries")

    packet = IngestTelemetryPacket(
        tenant_id="propel_industries",
        asset_id="jaw_crusher_01",
        signals={
            "drive_vib_de_rms": 5.8,
            "temp_bearing_c": 62.0,
            "motor_kw": 85.0,
            "hydraulic_toggle_bar": 160.0
        }
    )
    res = global_orchestrator.run_pipeline(packet, twin, tenant)
    assert res.health_status == "HEALTHY"
    assert res.health_score >= 80.0
    assert len(res.active_faults) == 0


def test_propel_crusher_critical_wear():
    twin = asset_twin_registry.get_asset("propel_industries", "jaw_crusher_01")
    tenant = asset_twin_registry.get_tenant("propel_industries")

    # High vibration (14.5 mm/s) + Overheating Bearing (96.0 C)
    packet = IngestTelemetryPacket(
        tenant_id="propel_industries",
        asset_id="jaw_crusher_01",
        signals={
            "drive_vib_de_rms": 14.5,
            "temp_bearing_c": 96.0,
            "motor_kw": 110.0,
            "hydraulic_toggle_bar": 150.0
        }
    )
    res = global_orchestrator.run_pipeline(packet, twin, tenant)
    assert res.health_status == "CRITICAL"
    assert res.health_score <= 40.0
    assert len(res.active_faults) >= 1
    
    # Verify SAP PM action was generated
    assert res.prescriptive_action is not None
    assert "SAP PM" in res.prescriptive_action.description
    assert res.prescriptive_action.target_system == "sap_pm"
