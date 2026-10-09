"""
Agent Beta: Operational Regime & Context Normalization
Identifies machine operating states (Running vs Idle vs Shock Load)
and normalizes contextual variables (ambient temperature, production batches).
"""

from typing import Dict, Any, Optional
from src.core.context import TenantContext
from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket
from src.engines.regime_detector import detect_operating_regime
from src.utils.logger import get_logger

logger = get_logger("Agent.Beta")


class AgentBetaRegime:
    """
    Classifies operating state and normalizes signals.
    """
    def process(
        self,
        packet: IngestTelemetryPacket,
        twin: AssetDigitalTwin,
        context: TenantContext
    ) -> Dict[str, Any]:
        regime_info = detect_operating_regime(packet, twin)
        
        # Thermal de-weathering: if ambient temperature exists, compute delta T
        bearing_temp = packet.get_role_value(twin, "bearing_temp")
        ambient_temp = packet.get_role_value(twin, "ambient_temp")
        
        normalized_data = {}
        if bearing_temp is not None and ambient_temp is not None:
            delta_t = round(bearing_temp - ambient_temp, 2)
            normalized_data["delta_t_celsius"] = delta_t
            normalized_data["is_thermally_de_weathered"] = True
        else:
            normalized_data["is_thermally_de_weathered"] = False

        return {
            "regime": regime_info["regime"],
            "is_operating": regime_info["is_operating"],
            "reason": regime_info["reason"],
            "normalized": normalized_data
        }
