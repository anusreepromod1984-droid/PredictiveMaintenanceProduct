"""
Agent Alpha: Sensor Signal Integrity & Hardware Gatekeeper
Guarantees that hardware faults (cut wires, disconnected sensors, out-of-range ADC)
are isolated immediately and do not generate false mechanical breakdown alerts.
"""

from typing import Dict, Any, List, Optional
from src.core.context import TenantContext
from src.core.buffer import platform_buffer
from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket
from src.engines.signal_integrity import check_namur_ne43, check_physical_ranges, check_stuck_sensor
from src.utils.logger import get_logger

logger = get_logger("Agent.Alpha")


class AgentAlphaQuality:
    """
    Evaluates incoming telemetry quality before any AI/physics diagnostics run.
    """
    def process(
        self,
        packet: IngestTelemetryPacket,
        twin: AssetDigitalTwin,
        context: TenantContext
    ) -> Dict[str, Any]:
        issues = []
        is_healthy = True

        # 1. Check NAMUR NE43 analog loop current (if mapped)
        loop_ma = packet.get_role_value(twin, "loop_current_ma")
        if loop_ma is not None:
            namur_res = check_namur_ne43(loop_ma)
            if not namur_res["valid"]:
                is_healthy = False
                issues.append({
                    "type": "NAMUR_NE43_FAULT",
                    "description": f"Analog sensor loop fault: {namur_res['status']} ({loop_ma} mA)",
                    "severity": "CRITICAL"
                })

        # 2. Check Physical Ranges
        range_violations = check_physical_ranges(packet, twin)
        for viol in range_violations:
            is_healthy = False
            issues.append({
                "type": "PHYSICAL_RANGE_VIOLATION",
                "description": f"Sensor '{viol['signal']}' = {viol['value']} out of bounds",
                "severity": "WARNING"
            })

        # 3. Check for Stuck / Flatlined Sensors using historical buffer
        tail = platform_buffer.get_telemetry_tail(context.tenant_id, context.asset_id, limit=10)
        for sig_name, current_val in packet.signals.items():
            if len(tail) >= 5:
                past_vals = [t.get("signals", {}).get(sig_name) for t in tail if sig_name in t.get("signals", {})]
                if past_vals and None not in past_vals:
                    if check_stuck_sensor(current_val, past_vals):
                        issues.append({
                            "type": "FROZEN_SENSOR",
                            "description": f"Sensor '{sig_name}' appears stuck at {current_val} across consecutive samples",
                            "severity": "WARNING"
                        })

        status_str = "PASS" if is_healthy and not issues else ("WARNING" if is_healthy else "FAIL")
        return {
            "status": status_str,
            "can_proceed_to_physics": is_healthy,
            "issues": issues,
            "sensor_count": len(packet.signals)
        }
