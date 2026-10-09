"""
Agent Delta: Prescriptive Action & CMMS Work Order Dispatcher
Adapts to client infrastructure: SAP PM (Propel Industries) vs Webhooks/Tickets (Nest Group).
Deduplicates open work orders to prevent spamming plant technicians.
"""

from typing import Dict, Any, List, Optional
from uuid import uuid4
from src.core.context import TenantContext
from src.schemas.tenant import TenantProfile
from src.schemas.asset import AssetDigitalTwin
from src.schemas.prediction import PrescriptiveAction, FaultRecord
from src.utils.logger import get_logger

logger = get_logger("Agent.Delta")


class AgentDeltaPrescriptive:
    """
    Formulates prescriptive repair actions and routes them to client CMMS.
    """
    def __init__(self):
        # Tracks active open work orders by tenant::asset to prevent duplicate creation
        self._open_orders: Dict[str, str] = {}

    def process(
        self,
        twin: AssetDigitalTwin,
        tenant_profile: Optional[TenantProfile],
        context: TenantContext,
        gamma_state: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        health_status = gamma_state.get("health_status", "HEALTHY")
        faults_raw = gamma_state.get("faults", [])
        
        # If asset is healthy, do not generate action
        if health_status == "HEALTHY" or not faults_raw:
            return None

        key = context.compound_key
        top_fault = faults_raw[0]
        severity = top_fault.get("severity", "WARNING")
        fault_code = top_fault.get("fault_code", "UNKNOWN")
        fault_desc = top_fault.get("description", "")

        # Determine target integration
        cmms_type = "webhook"
        if tenant_profile and tenant_profile.cmms_config:
            cmms_type = tenant_profile.cmms_config.integration_type

        # Check if an order is already open for this asset
        if key in self._open_orders:
            existing_wo = self._open_orders[key]
            action = PrescriptiveAction(
                action_type="FOLLOW_UP",
                work_order_id=existing_wo,
                priority="HIGH" if severity == "CRITICAL" else "MEDIUM",
                target_system=cmms_type,
                description=f"Active Work Order {existing_wo} already in progress for fault: {fault_code}",
                status="SUPPRESSED_DUPLICATE"
            )
            return action.model_dump()

        # Formulate new work order
        wo_id = f"WO-{uuid4().hex[:8].upper()}"
        self._open_orders[key] = wo_id

        priority = "EMERGENCY" if severity == "CRITICAL" else "HIGH"
        if context.vertical == "heavy_machinery":
            rec_text = f"[SAP PM Notification] Asset {twin.name} ({twin.asset_id}) triggered {fault_code}. Action: Inspect drive bearings and structural fasteners during shift changeover. Details: {fault_desc}"
        else:
            rec_text = f"[Maintenance Ticket] SMT line {twin.name} ({twin.asset_id}) reported {fault_code}. Action: Inspect pick-and-place nozzle vacuum seals and clean rotary filters. Details: {fault_desc}"

        action = PrescriptiveAction(
            action_type="COMPONENT_REPLACEMENT" if severity == "CRITICAL" else "INSPECTION",
            work_order_id=wo_id,
            priority=priority,
            target_system=cmms_type,
            description=rec_text,
            scheduled_downtime_shift="Next Planned Shift Break",
            status="DISPATCHED"
        )
        return action.model_dump()

    def clear_work_order(self, tenant_id: str, asset_id: str) -> bool:
        from src.core.context import make_tenant_key
        k = make_tenant_key(tenant_id, asset_id)
        if k in self._open_orders:
            del self._open_orders[k]
            return True
        return False
