"""
Dynamic LangGraph Execution Engine for Multi-Tenant APMS Product
Composes and executes the 4-agent state graph per telemetry packet:
Alpha (Quality) -> Beta (Regime) -> Gamma (Diagnostic) -> Delta (Prescriptive).
"""

import time
from typing import TypedDict, Optional, Dict, Any, List
from langgraph.graph import StateGraph, END

from src.core.context import TenantContext
from src.core.buffer import platform_buffer
from src.schemas.tenant import TenantProfile
from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket
from src.schemas.prediction import DiagnosticResult, FaultRecord, PrognosticRUL, PrescriptiveAction
from src.agents.alpha_quality import AgentAlphaQuality
from src.agents.beta_regime import AgentBetaRegime
from src.agents.gamma_diagnostic import AgentGammaDiagnostic
from src.agents.delta_prescriptive import AgentDeltaPrescriptive
from src.utils.logger import get_logger
from src.utils.metrics import platform_metrics

logger = get_logger("Agents.Orchestrator")


class APMSProductState(TypedDict):
    packet: IngestTelemetryPacket
    twin: AssetDigitalTwin
    tenant_profile: Optional[TenantProfile]
    context: TenantContext
    alpha_state: Optional[Dict[str, Any]]
    beta_state: Optional[Dict[str, Any]]
    gamma_state: Optional[Dict[str, Any]]
    delta_state: Optional[Dict[str, Any]]
    final_result: Optional[DiagnosticResult]
    agent_trace: List[str]


class DynamicAPMSOrchestrator:
    """
    LangGraph StateGraph Factory for Multi-Tenant, Multi-Device Predictive Maintenance.
    """
    def __init__(self):
        self.alpha = AgentAlphaQuality()
        self.beta = AgentBetaRegime()
        self.gamma = AgentGammaDiagnostic()
        self.delta = AgentDeltaPrescriptive()
        self.workflow = self._compile_graph()

    def _node_alpha(self, state: APMSProductState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace", []))
        trace.append("AgentAlpha:SignalIntegrity")
        out = self.alpha.process(state["packet"], state["twin"], state["context"])
        return {"alpha_state": out, "agent_trace": trace}

    def _node_beta(self, state: APMSProductState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace", []))
        trace.append("AgentBeta:RegimeDetector")
        out = self.beta.process(state["packet"], state["twin"], state["context"])
        return {"beta_state": out, "agent_trace": trace}

    def _node_gamma(self, state: APMSProductState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace", []))
        trace.append("AgentGamma:MultiVerticalDiagnostics")
        out = self.gamma.process(
            state["packet"],
            state["twin"],
            state["context"],
            state["beta_state"] or {}
        )
        return {"gamma_state": out, "agent_trace": trace}

    def _node_delta(self, state: APMSProductState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace", []))
        trace.append("AgentDelta:PrescriptiveAction")
        out = self.delta.process(
            state["twin"],
            state["tenant_profile"],
            state["context"],
            state["gamma_state"] or {}
        )
        return {"delta_state": out, "agent_trace": trace}

    def _edge_after_alpha(self, state: APMSProductState) -> str:
        alpha_out = state.get("alpha_state", {})
        # If sensors failed completely, skip physics and formulate sensor fault
        if not alpha_out.get("can_proceed_to_physics", True):
            return "node_delta"
        return "node_beta"

    def _edge_after_beta(self, state: APMSProductState) -> str:
        beta_out = state.get("beta_state", {})
        # If machine is idle, skip physics to suppress false alerts
        if not beta_out.get("is_operating", True):
            return "node_delta"
        return "node_gamma"

    def _compile_graph(self):
        graph = StateGraph(APMSProductState)
        graph.add_node("node_alpha", self._node_alpha)
        graph.add_node("node_beta", self._node_beta)
        graph.add_node("node_gamma", self._node_gamma)
        graph.add_node("node_delta", self._node_delta)

        graph.set_entry_point("node_alpha")
        graph.add_conditional_edges(
            "node_alpha",
            self._edge_after_alpha,
            {"node_beta": "node_beta", "node_delta": "node_delta"}
        )
        graph.add_conditional_edges(
            "node_beta",
            self._edge_after_beta,
            {"node_gamma": "node_gamma", "node_delta": "node_delta"}
        )
        graph.add_edge("node_gamma", "node_delta")
        graph.add_edge("node_delta", END)

        return graph.compile()

    def run_pipeline(
        self,
        packet: IngestTelemetryPacket,
        twin: AssetDigitalTwin,
        tenant_profile: Optional[TenantProfile]
    ) -> DiagnosticResult:
        start_time = time.time()
        
        # 1. Build immutable TenantContext
        context = TenantContext(
            tenant_id=packet.tenant_id,
            client_name=tenant_profile.name if tenant_profile else packet.tenant_id,
            vertical=twin.vertical,
            asset_id=packet.asset_id,
            asset_class=twin.asset_class,
            capabilities=twin.pipeline_config.get("diagnostic_engines", [])
        )

        # 2. Push telemetry to tenant-isolated ring buffer
        platform_buffer.push_telemetry(packet.tenant_id, packet.asset_id, packet.model_dump())

        # 3. Initialize state and execute LangGraph DAG
        initial_state: APMSProductState = {
            "packet": packet,
            "twin": twin,
            "tenant_profile": tenant_profile,
            "context": context,
            "alpha_state": None,
            "beta_state": None,
            "gamma_state": None,
            "delta_state": None,
            "final_result": None,
            "agent_trace": []
        }

        final_state = self.workflow.invoke(initial_state)

        # 4. Synthesize final DiagnosticResult
        alpha = final_state.get("alpha_state") or {}
        beta = final_state.get("beta_state") or {}
        gamma = final_state.get("gamma_state") or {}
        delta = final_state.get("delta_state")

        # Handle sensor fault vs idle vs normal diagnosis
        if not alpha.get("can_proceed_to_physics", True):
            health_score = 0.0
            health_status = "SENSOR_FAULT"
            active_faults = [
                FaultRecord(
                    fault_code="SENSOR_HARDWARE_FAULT",
                    description=issue.get("description", "Sensor defect"),
                    severity="CRITICAL",
                    confidence=0.99,
                    failure_domain="SENSOR"
                )
                for issue in alpha.get("issues", [])
            ]
            prognostics = None
        elif not beta.get("is_operating", True):
            health_score = 100.0
            health_status = "IDLE"
            active_faults = []
            prognostics = None
        else:
            health_score = gamma.get("health_score", 100.0)
            health_status = gamma.get("health_status", "HEALTHY")
            active_faults = [FaultRecord(**f) for f in gamma.get("faults", [])]
            prognostics = PrognosticRUL(**gamma["prognostics"]) if gamma.get("prognostics") else None

        prescriptive = PrescriptiveAction(**delta) if delta else None
        elapsed_ms = round((time.time() - start_time) * 1000.0, 2)

        result = DiagnosticResult(
            tenant_id=packet.tenant_id,
            asset_id=packet.asset_id,
            timestamp=packet.timestamp,
            health_score=health_score,
            health_status=health_status,
            operating_regime=beta.get("regime", "NORMAL"),
            signal_integrity=alpha,
            active_faults=active_faults,
            prognostics=prognostics,
            prescriptive_action=prescriptive,
            execution_time_ms=elapsed_ms,
            agent_trace=final_state.get("agent_trace", [])
        )

        # Cache diagnostic result in isolated buffer (also writes through to historian)
        platform_buffer.save_diagnostic(packet.tenant_id, packet.asset_id, result.model_dump())

        # Record Prometheus-compatible metrics
        try:
            platform_metrics.record_ingestion(packet.tenant_id, packet.asset_id)
            platform_metrics.record_pipeline_duration(packet.tenant_id, packet.asset_id, elapsed_ms)
            platform_metrics.set_health_score(packet.tenant_id, packet.asset_id, result.health_score)
            critical_count = sum(1 for f in result.active_faults if f.severity == "CRITICAL")
            platform_metrics.set_active_faults(packet.tenant_id, packet.asset_id, len(result.active_faults), critical_count)
            if result.prognostics:
                platform_metrics.set_rul_hours(packet.tenant_id, packet.asset_id, result.prognostics.rul_hours)
            # SBM anomaly index from gamma state
            gamma = final_state.get("gamma_state") or {}
            sbm = gamma.get("sbm_analysis")
            if sbm and sbm.get("sbm_active"):
                platform_metrics.set_sbm_anomaly_index(packet.tenant_id, packet.asset_id, sbm.get("anomaly_index", 0.0))
            if result.health_status == "SENSOR_FAULT":
                platform_metrics.record_sensor_fault(packet.tenant_id, packet.asset_id)
        except Exception as e:
            logger.warning(f"Metrics recording error: {e}")

        return result


# Singleton orchestrator instance
global_orchestrator = DynamicAPMSOrchestrator()
