"""
Standardized Agent Diagnostics & Prognostics Response Contracts
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, Field


class FaultRecord(BaseModel):
    fault_code: str
    description: str
    severity: str = "WARNING"  # INFO, WARNING, CRITICAL
    confidence: float = Field(ge=0.0, le=1.0)
    failure_domain: str = "MECHANICAL"  # MECHANICAL, ELECTRICAL, PROCESS, SENSOR
    triggering_signal: Optional[str] = None
    triggering_value: Optional[float] = None
    threshold: Optional[float] = None


class PrognosticRUL(BaseModel):
    rul_hours: float
    rul_days: float
    confidence_pct: float
    degradation_slope_per_hour: float
    method: str  # "weibull_hazard", "pinn_physics", "multivariate_drift", "baseline_linear"
    projected_maintenance_window: str


class PrescriptiveAction(BaseModel):
    action_type: str = "INSPECTION"  # INSPECTION, RELUBRICATION, COMPONENT_REPLACEMENT, CLEANING
    work_order_id: Optional[str] = None
    priority: str = "MEDIUM"  # LOW, MEDIUM, HIGH, EMERGENCY
    target_system: str = "webhook"  # sap_pm, maximo, webhook, email, sms
    description: str
    scheduled_downtime_shift: Optional[str] = None
    status: str = "PENDING_DISPATCH"  # PENDING_DISPATCH, DISPATCHED, SUPPRESSED_DUPLICATE


class DiagnosticResult(BaseModel):
    """
    Standardized, multi-vertical diagnostic envelope returned to UI and CMMS.
    """
    trace_id: str = Field(default_factory=lambda: f"apms_{uuid4().hex[:12]}")
    tenant_id: str
    asset_id: str
    timestamp: float
    iso_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    # Overall Status & Health
    health_score: float = Field(ge=0.0, le=100.0, description="100.0 = brand new, 0.0 = catastrophic failure")
    health_status: str = Field(description="HEALTHY, WARNING, CRITICAL, SENSOR_FAULT, IDLE")
    operating_regime: str = Field(default="NORMAL", description="NORMAL, HIGH_LOAD, IDLE, TRANSIENT")
    
    # Micro-Agent Evaluation Breakdown
    signal_integrity: Dict[str, Any] = Field(default_factory=dict, description="Agent Alpha sensor quality")
    active_faults: List[FaultRecord] = Field(default_factory=list, description="Agent Gamma isolated defects")
    prognostics: Optional[PrognosticRUL] = None
    prescriptive_action: Optional[PrescriptiveAction] = None

    # Context & Diagnostics
    execution_time_ms: float = 0.0
    agent_trace: List[str] = Field(default_factory=list)
