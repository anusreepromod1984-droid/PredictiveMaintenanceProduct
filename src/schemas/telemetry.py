"""
Dynamic Telemetry Ingestion Contract
Supports arbitrary parameter payloads for any industrial vertical.
"""

import time
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from src.schemas.asset import AssetDigitalTwin


class IngestTelemetryPacket(BaseModel):
    """
    Polymorphic Telemetry Packet.
    Accepts arbitrary sensor parameters in `signals` map without requiring code changes.
    """
    tenant_id: str = Field(..., description="Unique client identifier, e.g. 'propel_industries', 'nest_group'")
    asset_id: str = Field(..., description="Machine identifier, e.g. 'jaw_crusher_01', 'smt_line1_pnp'")
    timestamp: float = Field(default_factory=lambda: time.time(), description="Epoch timestamp in seconds")
    
    # Dynamic parameter payload: adapts to ANY vertical or device
    signals: Dict[str, float] = Field(
        default_factory=dict,
        description="Arbitrary sensor parameters: {'vibration_rms': 4.2, 'nozzle_vacuum_kpa': -88.5, ...}"
    )

    # Optional high-frequency acceleration waveform for envelope demodulation
    waveform: Optional[List[float]] = Field(default=None, description="Raw vibration waveform samples")
    sample_rate_hz: Optional[float] = Field(default=None, description="Sampling rate in Hz")

    # Optional operating state signaled by machine PLC
    operating_state: Optional[str] = Field(default="running", description="'running', 'idle', 'setup', 'fault'")

    def get_value(self, client_key: str) -> Optional[float]:
        """Direct retrieval by client key."""
        return self.signals.get(client_key)

    def get_role_value(self, twin: AssetDigitalTwin, standard_role: str) -> Optional[float]:
        """Resolves the client key via the twin's signal_map and returns its value."""
        client_key = twin.resolve_key(standard_role)
        if not client_key:
            return None
        return self.signals.get(client_key)
