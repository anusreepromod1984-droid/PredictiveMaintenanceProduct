"""
Asset Digital Twin Schema
Defines semantic signal mappings, diagnostic pipelines, and thresholds per asset.
"""

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class SignalSpec(BaseModel):
    """Metadata describing a sensor channel."""
    client_key: str = Field(..., description="The exact parameter key received in client JSON")
    display_name: str
    unit: str
    min_physical_bound: Optional[float] = None
    max_physical_bound: Optional[float] = None
    sampling_rate_hz: Optional[float] = None


class AssetDigitalTwin(BaseModel):
    """
    Asset Administration Shell (AAS) Digital Twin.
    Allows clients to map arbitrary telemetry keys to standard physical diagnostic roles.
    """
    tenant_id: str = Field(..., description="Owner tenant ID")
    asset_id: str = Field(..., description="Unique asset ID within tenant")
    name: str = Field(..., description="Machine name, e.g. 'Jaw Crusher 110kW'")
    asset_class: str = Field(..., description="'jaw_crusher', 'vibrating_screen', 'smt_pick_place', 'centrifugal_pump', 'chiller'")
    vertical: str = Field(..., description="'heavy_machinery', 'electronics_smt', 'hvac', etc.")
    criticality: int = Field(default=3, ge=1, le=5, description="1 (low) to 5 (mission-critical)")
    
    # Semantic Mapping: Standard Diagnostic Role -> Client Ingestion Key
    # e.g. {"primary_vibration": "drive_vib_de_rms", "bearing_temp": "temp_bearing_c"}
    signal_map: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps standard roles (primary_vibration, speed_rpm, temperature, current, pressure) to client telemetry keys"
    )

    # Detailed specifications for client channels
    signal_specs: Dict[str, SignalSpec] = Field(default_factory=dict)

    # Diagnostic Pipeline Configuration
    pipeline_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "quality_strategy": "digital_range_check",  # or "namur_ne43"
            "regime_detection": "load_threshold",       # or "discrete_cycles", "none"
            "diagnostic_engines": ["iso_vibration", "anomaly_detector"],
            "prescriptive_target": "webhook"
        }
    )

    # Physical Thresholds (Warning / Danger)
    thresholds: Dict[str, float] = Field(
        default_factory=lambda: {
            "vibration_warning_mm_s": 4.5,
            "vibration_danger_mm_s": 7.1,
            "temp_warning_celsius": 75.0,
            "temp_danger_celsius": 90.0,
            "load_idle_threshold_pct": 10.0
        }
    )

    # Machine Kinematics (optional for rotating machinery)
    kinematics: Dict[str, Any] = Field(
        default_factory=lambda: {
            "rated_rpm": 1480.0,
            "bearing_code": "SKF-6206",
            "gear_ratio": 1.0
        }
    )

    def resolve_key(self, standard_role: str) -> Optional[str]:
        """Resolves the client key corresponding to a standard diagnostic role."""
        return self.signal_map.get(standard_role)
