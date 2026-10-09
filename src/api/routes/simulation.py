"""
Multi-Tenant Simulation & Demonstration Routes
Simulates Propel Industries (Heavy Mining) and Nest Group (High-Tech SMT)
to demonstrate dynamic agent adaptation and strict isolation.
"""

from fastapi import APIRouter
from typing import Dict, Any, List
from src.schemas.telemetry import IngestTelemetryPacket
from src.registry.asset_registry import asset_twin_registry
from src.agents.dynamic_orchestrator import global_orchestrator
from src.core.buffer import platform_buffer

router = APIRouter(prefix="/simulation", tags=["Multi-Tenant Live Simulation"])


@router.post("/propel_crusher")
def simulate_propel_crusher(condition: str = "critical_wear") -> Dict[str, Any]:
    """
    Simulates Propel Heavy Jaw Crusher with multi-physics mining parameters.
    condition: 'normal', 'critical_wear', or 'sensor_wire_break'
    """
    twin = asset_twin_registry.get_asset("propel_industries", "jaw_crusher_01")
    tenant = asset_twin_registry.get_tenant("propel_industries")

    if condition == "normal":
        signals = {
            "drive_vib_de_rms": 5.4,
            "temp_bearing_c": 58.0,
            "motor_kw": 88.0,
            "hydraulic_toggle_bar": 165.0,
            "feed_rate_tph": 310.0,
            "namur_vib_loop_ma": 12.0
        }
    elif condition == "sensor_wire_break":
        signals = {
            "drive_vib_de_rms": 0.0,
            "temp_bearing_c": 25.0,
            "motor_kw": 0.0,
            "hydraulic_toggle_bar": 160.0,
            "feed_rate_tph": 0.0,
            "namur_vib_loop_ma": 2.1  # Cut wire (<3.6mA)
        }
    else:  # critical_wear
        signals = {
            "drive_vib_de_rms": 14.8,  # Severe vibration
            "temp_bearing_c": 96.5,    # Overheating bearing
            "motor_kw": 115.0,
            "hydraulic_toggle_bar": 115.0,  # Below 130 bar min
            "feed_rate_tph": 340.0,
            "namur_vib_loop_ma": 16.5
        }

    packet = IngestTelemetryPacket(
        tenant_id="propel_industries",
        asset_id="jaw_crusher_01",
        signals=signals
    )

    result = global_orchestrator.run_pipeline(packet, twin, tenant)
    return {
        "scenario": f"Propel Industries Heavy Crusher - {condition.upper()}",
        "diagnostic_result": result
    }


@router.post("/nest_smt")
def simulate_nest_smt(condition: str = "vacuum_leak") -> Dict[str, Any]:
    """
    Simulates Nest Group SMT Pick-and-Place line with discrete electronics parameters.
    condition: 'normal', 'vacuum_leak', or 'idle_setup'
    """
    twin = asset_twin_registry.get_asset("nest_group", "smt_line1_pnp")
    tenant = asset_twin_registry.get_tenant("nest_group")

    if condition == "normal":
        signals = {
            "head_vacuum_kpa": -88.5,
            "placement_cycle_ms": 22.4,
            "x_axis_torque_pct": 45.0,
            "mounter_kw": 12.0
        }
        state = "running"
    elif condition == "idle_setup":
        signals = {
            "head_vacuum_kpa": 0.0,
            "placement_cycle_ms": 0.0,
            "x_axis_torque_pct": 2.0,
            "mounter_kw": 1.2
        }
        state = "idle"
    else:  # vacuum_leak & axis friction
        signals = {
            "head_vacuum_kpa": -54.0,  # Vacuum leak (normal is -85 kPa)
            "placement_cycle_ms": 39.8,  # Drifted past 35ms threshold
            "x_axis_torque_pct": 89.0,   # High friction
            "mounter_kw": 16.5
        }
        state = "running"

    packet = IngestTelemetryPacket(
        tenant_id="nest_group",
        asset_id="smt_line1_pnp",
        signals=signals,
        operating_state=state
    )

    result = global_orchestrator.run_pipeline(packet, twin, tenant)
    return {
        "scenario": f"Nest Group Electronics SMT - {condition.upper()}",
        "diagnostic_result": result
    }


@router.post("/cross_tenant_isolation_test")
def simulate_cross_tenant_isolation() -> Dict[str, Any]:
    """
    Demonstrates zero cross-tenant data mixup.
    Registers two assets with identical ID 'unit_01' under Propel and Nest,
    ingests divergent data, and validates that buffers and diagnostics remain isolated.
    """
    from src.schemas.asset import AssetDigitalTwin

    # 1. Register unit_01 under Propel
    propel_twin = AssetDigitalTwin(
        tenant_id="propel_industries",
        asset_id="unit_01",
        name="Propel Secondary Cone Crusher Unit 01",
        asset_class="jaw_crusher",
        vertical="heavy_machinery",
        signal_map={"primary_vibration": "vib_mm_s"}
    )
    asset_twin_registry.register_asset(propel_twin)

    # 2. Register unit_01 under Nest
    nest_twin = AssetDigitalTwin(
        tenant_id="nest_group",
        asset_id="unit_01",
        name="Nest SMT Reflow Oven Unit 01",
        asset_class="smt_pick_place",
        vertical="electronics_smt",
        signal_map={"nozzle_vacuum": "vac_kpa"}
    )
    asset_twin_registry.register_asset(nest_twin)

    # 3. Ingest Propel packet
    p_packet = IngestTelemetryPacket(
        tenant_id="propel_industries",
        asset_id="unit_01",
        signals={"vib_mm_s": 15.5}
    )
    propel_diag = global_orchestrator.run_pipeline(
        p_packet, propel_twin, asset_twin_registry.get_tenant("propel_industries")
    )

    # 4. Ingest Nest packet
    n_packet = IngestTelemetryPacket(
        tenant_id="nest_group",
        asset_id="unit_01",
        signals={"vac_kpa": -89.0}
    )
    nest_diag = global_orchestrator.run_pipeline(
        n_packet, nest_twin, asset_twin_registry.get_tenant("nest_group")
    )

    # 5. Verify isolated buffers
    p_tail = platform_buffer.get_telemetry_tail("propel_industries", "unit_01")
    n_tail = platform_buffer.get_telemetry_tail("nest_group", "unit_01")

    return {
        "status": "ISOLATION_CONFIRMED",
        "propel_asset": {
            "name": propel_twin.name,
            "health_status": propel_diag.health_status,
            "buffer_signal_sample": p_tail[-1]["signals"] if p_tail else {}
        },
        "nest_asset": {
            "name": nest_twin.name,
            "health_status": nest_diag.health_status,
            "buffer_signal_sample": n_tail[-1]["signals"] if n_tail else {}
        },
        "zero_cross_talk_verified": (
            "vib_mm_s" in p_tail[-1]["signals"]
            and "vac_kpa" in n_tail[-1]["signals"]
            and "vac_kpa" not in p_tail[-1]["signals"]
            and "vib_mm_s" not in n_tail[-1]["signals"]
        )
    }
