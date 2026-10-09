"""
Preloaded Enterprise Assets for Propel Industries and Nest Group
Demonstrates multi-vertical, multi-device, multi-parameter support out of the box.
"""

from typing import Dict, List
from src.schemas.tenant import TenantProfile, CMMSConfig
from src.schemas.asset import AssetDigitalTwin, SignalSpec


PRELOADED_TENANTS: List[TenantProfile] = [
    TenantProfile(
        tenant_id="propel_industries",
        name="Propel Industries Pvt Ltd (Mining & Crushing Division)",
        vertical="heavy_machinery",
        cmms_config=CMMSConfig(
            integration_type="sap_pm",
            endpoint_url="https://sap-gateway.propelind.com/pm/workorders",
            notification_email="maintenance.crushing@propelind.com",
            auto_create_work_orders=True
        ),
        metadata={"region": "Coimbatore, India", "segment": "Quarry & Mining Equipment"}
    ),
    TenantProfile(
        tenant_id="nest_group",
        name="Nest Group International (Electronics & SMT Division)",
        vertical="electronics_smt",
        cmms_config=CMMSConfig(
            integration_type="webhook",
            endpoint_url="https://api.nestgroup.net/iot/maintenance/webhook",
            notification_email="smt.alerts@nestgroup.net",
            auto_create_work_orders=True
        ),
        metadata={"region": "Kochi, India", "segment": "High-Precision Discrete Manufacturing"}
    )
]


PRELOADED_ASSETS: List[AssetDigitalTwin] = [
    # -------------------------------------------------------------
    # PROPEL INDUSTRIES ASSETS (Heavy Machinery / Mining)
    # -------------------------------------------------------------
    AssetDigitalTwin(
        tenant_id="propel_industries",
        asset_id="jaw_crusher_01",
        name="Propel AVJ Series Heavy Jaw Crusher 110kW",
        asset_class="jaw_crusher",
        vertical="heavy_machinery",
        criticality=5,
        signal_map={
            "primary_vibration": "drive_vib_de_rms",
            "bearing_temp": "temp_bearing_c",
            "power_load": "motor_kw",
            "hydraulic_pressure": "hydraulic_toggle_bar",
            "feed_rate": "feed_rate_tph",
            "loop_current_ma": "namur_vib_loop_ma"
        },
        signal_specs={
            "drive_vib_de_rms": SignalSpec(client_key="drive_vib_de_rms", display_name="Drive End Vibration", unit="mm/s", min_physical_bound=0.0, max_physical_bound=50.0),
            "temp_bearing_c": SignalSpec(client_key="temp_bearing_c", display_name="Pitman Bearing Temperature", unit="°C", min_physical_bound=-10.0, max_physical_bound=150.0),
            "motor_kw": SignalSpec(client_key="motor_kw", display_name="Drive Motor Power", unit="kW", min_physical_bound=0.0, max_physical_bound=200.0),
            "hydraulic_toggle_bar": SignalSpec(client_key="hydraulic_toggle_bar", display_name="Hydraulic Clamping Pressure", unit="bar", min_physical_bound=0.0, max_physical_bound=300.0)
        },
        thresholds={
            "vibration_warning_mm_s": 11.2,
            "vibration_danger_mm_s": 18.0,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
            "hydraulic_pressure_min_bar": 130.0,
            "load_idle_threshold_pct": 15.0
        },
        pipeline_config={
            "quality_strategy": "namur_ne43",
            "regime_detection": "load_threshold",
            "diagnostic_engines": ["iso_vibration", "weibull_rul", "bearing_physics"],
            "prescriptive_target": "sap_pm"
        }
    ),
    AssetDigitalTwin(
        tenant_id="propel_industries",
        asset_id="vibrating_screen_02",
        name="Propel PVS Circular Motion Screen 30kW",
        asset_class="vibrating_screen",
        vertical="heavy_machinery",
        criticality=4,
        signal_map={
            "primary_vibration": "deck_vib_accel_g",
            "bearing_temp": "exciter_temp_c",
            "motor_current": "drive_current_a"
        },
        thresholds={
            "vibration_warning_mm_s": 12.0,
            "vibration_danger_mm_s": 19.5,
            "temp_warning_celsius": 75.0,
            "temp_danger_celsius": 90.0,
            "load_idle_threshold_pct": 10.0
        },
        pipeline_config={
            "quality_strategy": "digital_range_check",
            "regime_detection": "load_threshold",
            "diagnostic_engines": ["iso_vibration", "weibull_rul"]
        }
    ),

    # -------------------------------------------------------------
    # NEST GROUP ASSETS (High-Tech Electronics & SMT)
    # -------------------------------------------------------------
    AssetDigitalTwin(
        tenant_id="nest_group",
        asset_id="smt_line1_pnp",
        name="Nest SMT Panasonic High-Speed Modular Mounter",
        asset_class="smt_pick_place",
        vertical="electronics_smt",
        criticality=5,
        signal_map={
            "nozzle_vacuum": "head_vacuum_kpa",
            "cycle_time": "placement_cycle_ms",
            "servo_torque": "x_axis_torque_pct",
            "power_load": "mounter_kw"
        },
        signal_specs={
            "head_vacuum_kpa": SignalSpec(client_key="head_vacuum_kpa", display_name="Spindle Vacuum Level", unit="kPa", min_physical_bound=-101.3, max_physical_bound=0.0),
            "placement_cycle_ms": SignalSpec(client_key="placement_cycle_ms", display_name="Placement Cycle Time", unit="ms", min_physical_bound=5.0, max_physical_bound=200.0),
            "x_axis_torque_pct": SignalSpec(client_key="x_axis_torque_pct", display_name="X-Axis Linear Motor Torque", unit="%", min_physical_bound=0.0, max_physical_bound=100.0)
        },
        thresholds={
            "vacuum_critical_kpa": -65.0,
            "cycle_time_max_ms": 35.0,
            "servo_torque_max_pct": 85.0,
            "load_idle_threshold_pct": 5.0
        },
        pipeline_config={
            "quality_strategy": "digital_range_check",
            "regime_detection": "load_threshold",
            "diagnostic_engines": ["smt_vacuum_diagnostics", "cycle_time_drift", "anomaly_detector"],
            "prescriptive_target": "webhook"
        }
    ),
    AssetDigitalTwin(
        tenant_id="nest_group",
        asset_id="cleanroom_chiller_01",
        name="Nest Cleanroom Process Water Chiller 45kW",
        asset_class="chiller",
        vertical="electronics_smt",
        criticality=4,
        signal_map={
            "primary_vibration": "compressor_vib_rms",
            "bearing_temp": "discharge_temp_c",
            "ambient_temp": "cleanroom_ambient_temp_c",
            "power_load": "chiller_kw"
        },
        thresholds={
            "vibration_warning_mm_s": 4.5,
            "vibration_danger_mm_s": 7.1,
            "temp_warning_celsius": 85.0,
            "temp_danger_celsius": 100.0,
            "load_idle_threshold_pct": 8.0
        },
        pipeline_config={
            "quality_strategy": "digital_range_check",
            "regime_detection": "load_threshold",
            "diagnostic_engines": ["iso_vibration", "weibull_rul"]
        }
    )
]
