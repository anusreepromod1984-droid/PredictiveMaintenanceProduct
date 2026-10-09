"""
Operating Regime & Operational State Classifier
Distinguishes between IDLE, SETUP, NORMAL_LOAD, and SHOCK_LOAD.
"""

from typing import Dict, Any, Optional
from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket


def detect_operating_regime(packet: IngestTelemetryPacket, twin: AssetDigitalTwin) -> Dict[str, Any]:
    """
    Classifies the operational state of the machine.
    Prevents false alarms when the machine is stopped or unloaded.
    """
    # 1. Respect explicit PLC state if provided
    if packet.operating_state and packet.operating_state.lower() in ("idle", "stopped", "off", "setup"):
        return {
            "regime": "IDLE",
            "is_operating": False,
            "reason": f"Signaled by machine PLC: {packet.operating_state}"
        }

    # 2. Check power/current/load against idle thresholds
    load_val = packet.get_role_value(twin, "power_load")
    if load_val is None:
        load_val = packet.get_role_value(twin, "motor_current")

    idle_threshold = twin.thresholds.get("load_idle_threshold_pct", 10.0)
    
    if load_val is not None:
        if load_val <= idle_threshold:
            return {
                "regime": "IDLE",
                "is_operating": False,
                "reason": f"Signal value {load_val} <= idle threshold {idle_threshold}"
            }

    # 3. Check RPM if available
    rpm_val = packet.get_role_value(twin, "speed_rpm")
    if rpm_val is not None and rpm_val < 50.0:
        return {
            "regime": "IDLE",
            "is_operating": False,
            "reason": f"Shaft speed {rpm_val} RPM indicates machine stopped"
        }

    # 4. Check for high shock load (e.g. Propel crusher boulder ingestion)
    vib_val = packet.get_role_value(twin, "primary_vibration")
    danger_vib = twin.thresholds.get("vibration_danger_mm_s", 7.1)
    if vib_val is not None and vib_val > (danger_vib * 1.5):
        return {
            "regime": "HEAVY_SHOCK_LOAD",
            "is_operating": True,
            "reason": f"Transient vibration spike {vib_val} mm/s"
        }

    return {
        "regime": "NORMAL_OPERATION",
        "is_operating": True,
        "reason": "Machine under active nominal load"
    }
