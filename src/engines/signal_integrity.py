"""
Signal Integrity & Sensor Quality Verification Engine
Prevents broken hardware and cut wires from triggering false mechanical alarms.
"""

from typing import Dict, Any, List, Optional
from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket


def check_namur_ne43(current_ma: Optional[float]) -> Dict[str, Any]:
    """Evaluates analog 4-20mA sensor loop against NAMUR NE43 standard."""
    if current_ma is None:
        return {"status": "NOT_APPLICABLE", "valid": True}
    if current_ma < 3.6:
        return {"status": "OPEN_CIRCUIT_WIRE_BREAK", "valid": False, "loop_ma": current_ma}
    elif current_ma > 21.0:
        return {"status": "SHORT_CIRCUIT_HARDWARE_FAULT", "valid": False, "loop_ma": current_ma}
    return {"status": "LOOP_HEALTHY", "valid": True, "loop_ma": current_ma}


def check_physical_ranges(packet: IngestTelemetryPacket, twin: AssetDigitalTwin) -> List[Dict[str, Any]]:
    """Checks each signal against min/max physical bounds defined in twin.signal_specs."""
    violations = []
    for signal_name, val in packet.signals.items():
        spec = twin.signal_specs.get(signal_name)
        if spec:
            if spec.min_physical_bound is not None and val < spec.min_physical_bound:
                violations.append({
                    "signal": signal_name,
                    "value": val,
                    "min_bound": spec.min_physical_bound,
                    "issue": "UNDER_PHYSICAL_RANGE"
                })
            elif spec.max_physical_bound is not None and val > spec.max_physical_bound:
                violations.append({
                    "signal": signal_name,
                    "value": val,
                    "max_bound": spec.max_physical_bound,
                    "issue": "EXCEEDED_PHYSICAL_RANGE"
                })
    return violations


def check_stuck_sensor(current_val: float, past_vals: List[float], tolerance: float = 1e-6) -> bool:
    """Detects if a continuous analog sensor has flatlined across N historical samples."""
    if len(past_vals) < 5:
        return False
    return all(abs(v - current_val) < tolerance for v in past_vals[-5:])
