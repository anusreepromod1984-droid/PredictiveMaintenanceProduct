"""
LangGraph report insight pipeline: run Alpha→Beta→Gamma→Delta per machine on report
window data, then build an insight graph + narrative summary.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END

from src.agents.dynamic_orchestrator import global_orchestrator
from src.registry.asset_registry import asset_twin_registry
from src.schemas.prediction import DiagnosticResult
from src.services.fleet_query_service import _machine_rows
from src.services.fleet_telemetry_adapter import build_ingest_packet, latest_row_from_machine_payload
from src.services.threshold_provider import UpstreamFleetClient
from src.services.plant_report_summarizer import (
    REPORT_QUERY_TIMEOUT_S,
    _build_query_body,
    _alert_lines,
    _duration_from_date_range,
)
from src.utils.logger import get_logger

logger = get_logger("Agents.ReportInsight")


class ReportInsightState(TypedDict, total=False):
    report_meta: Dict[str, Any]
    machine_ids: List[str]
    upstream_payload: Optional[Dict[str, Any]]
    diagnostics: Dict[str, Dict[str, Any]]
    graph: Dict[str, Any]
    summary: str
    findings: List[str]
    agent_trace: List[str]


_AGENT_NODES = [
    ("alpha", "Agent Alpha", "Signal integrity & sensor quality"),
    ("beta", "Agent Beta", "Operating regime detection"),
    ("gamma", "Agent Gamma", "Defect isolation & prognostics"),
    ("delta", "Agent Delta", "Prescriptive maintenance"),
]


def _status_from_health(health_status: str) -> str:
    h = (health_status or "").upper()
    if h in ("CRITICAL", "SENSOR_FAULT"):
        return "critical"
    if h in ("WARNING", "DEGRADED"):
        return "warning"
    if h == "IDLE":
        return "idle"
    return "ok"


def _build_agent_flow_graph(diagnostics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Multi-machine agent DAG with per-node status from latest diagnostic runs."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []

    for key, label, desc in _AGENT_NODES:
        nodes.append({
            "id": key,
            "label": label,
            "description": desc,
            "type": "agent",
        })

    edges.extend([
        {"from": "alpha", "to": "beta"},
        {"from": "beta", "to": "gamma"},
        {"from": "gamma", "to": "delta"},
    ])

    machine_nodes: List[Dict[str, Any]] = []
    for mid, diag in diagnostics.items():
        status = _status_from_health(str(diag.get("health_status") or ""))
        machine_nodes.append({
            "id": f"machine:{mid}",
            "label": mid,
            "type": "machine",
            "healthStatus": diag.get("health_status"),
            "healthScore": diag.get("health_score"),
            "status": status,
            "agentTrace": diag.get("agent_trace") or [],
        })
        edges.append({"from": "delta", "to": f"machine:{mid}"})

    return {
        "kind": "agent_insight",
        "pipeline": {"nodes": nodes, "edges": edges},
        "machines": machine_nodes,
    }


def _build_parameter_series(
    upstream_payload: Optional[Dict[str, Any]],
    selected_parameters: Optional[List[str]],
) -> List[Dict[str, Any]]:
    series_out: List[Dict[str, Any]] = []
    if not upstream_payload:
        return series_out

    for machine in _machine_rows(upstream_payload):
        mid = str(machine.get("machineId") or machine.get("machine_id") or "")
        history = machine.get("history") if isinstance(machine.get("history"), dict) else {}
        rows = history.get("rows") or []
        params = selected_parameters or []
        if not params and rows and isinstance(rows[-1], dict):
            params = [
                k for k, v in rows[-1].items()
                if k not in ("timestamp", "ts", "machineId", "machine_id") and isinstance(v, (int, float))
            ][:4]

        for param in params:
            points = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ts = row.get("timestamp") or row.get("ts")
                val = row.get(param)
                if ts is None or not isinstance(val, (int, float)):
                    continue
                t_ms = int(float(ts) * 1000) if float(ts) < 1e12 else int(ts)
                points.append({"t": t_ms, "v": float(val)})
            if len(points) >= 2:
                vals = [p["v"] for p in points]
                series_out.append({
                    "machineId": mid,
                    "parameter": param,
                    "points": points[-120:],
                    "stats": {
                        "min": min(vals),
                        "max": max(vals),
                        "avg": round(statistics.mean(vals), 4),
                    },
                })
    return series_out


def _findings_from_diagnostic(machine_id: str, diag: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    hs = diag.get("health_status")
    score = diag.get("health_score")
    if hs:
        lines.append(f"{machine_id}: Agent consensus health {hs} (score {score})")

    for f in diag.get("active_faults") or []:
        if not isinstance(f, dict):
            continue
        lines.append(
            f"{machine_id}: [{f.get('severity', 'WARN')}] {f.get('fault_code')} — "
            f"{f.get('description', 'defect')} ({f.get('failure_domain', 'n/a')})"
        )

    prog = diag.get("prognostics")
    if isinstance(prog, dict) and prog.get("rul_days") is not None:
        lines.append(
            f"{machine_id}: Agent Gamma RUL ≈ {prog.get('rul_days')} days "
            f"({prog.get('method', 'model')})"
        )

    action = diag.get("prescriptive_action")
    if isinstance(action, dict) and action.get("description"):
        lines.append(f"{machine_id}: Agent Delta — {action.get('description')}")

    alpha = diag.get("signal_integrity")
    if isinstance(alpha, dict) and alpha.get("issues"):
        for issue in alpha.get("issues")[:3]:
            if isinstance(issue, dict):
                lines.append(f"{machine_id}: Agent Alpha — {issue.get('description', 'signal issue')}")

    return lines


def _summary_from_agents(
    report_meta: Dict[str, Any],
    diagnostics: Dict[str, Dict[str, Any]],
    alert_count: int,
) -> str:
    title = report_meta.get("title") or "Plant report"
    period = report_meta.get("period_label") or "selected period"
    n = len(report_meta.get("machine_ids") or [])
    critical = sum(
        1 for d in diagnostics.values()
        if str(d.get("health_status", "")).upper() in ("CRITICAL", "SENSOR_FAULT")
    )
    warning = sum(
        1 for d in diagnostics.values()
        if str(d.get("health_status", "")).upper() in ("WARNING", "DEGRADED")
    )

    parts = [
        f"{title} — LangGraph agents Alpha through Delta evaluated {n} machine(s) over {period}.",
        f"{len(diagnostics)} diagnostic graph run(s) completed; upstream alert count in window: {alert_count}.",
    ]
    if critical:
        parts.append(f"{critical} asset(s) in CRITICAL/SENSOR_FAULT require immediate review.")
    elif warning:
        parts.append(f"{warning} asset(s) show degradation; schedule inspection per Agent Delta guidance.")
    else:
        parts.append("Agents report nominal or idle regimes with no isolated critical defects on latest samples.")

    return " ".join(parts)


class ReportInsightOrchestrator:
    def __init__(self):
        self.workflow = self._compile()

    def _node_fetch_upstream(self, state: ReportInsightState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace") or [])
        trace.append("ReportGraph:FetchUpstream")
        meta = state["report_meta"]
        client = UpstreamFleetClient(timeout_s=REPORT_QUERY_TIMEOUT_S)
        body = _build_query_body(
            state["machine_ids"],
            meta.get("date_range"),
            meta.get("timezone"),
            meta.get("selected_parameters"),
        )
        payload = client.query(body) if client.is_configured else None
        if not payload:
            raise RuntimeError("Upstream POST /api/ai/query failed or returned no data")
        return {"upstream_payload": payload, "agent_trace": trace}

    def _node_run_agent_pipelines(self, state: ReportInsightState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace") or [])
        trace.append("ReportGraph:RunAgentPipelines")
        payload = state.get("upstream_payload")
        diagnostics: Dict[str, Dict[str, Any]] = {}

        for machine in _machine_rows(payload):
            mid = str(machine.get("machineId") or machine.get("machine_id") or "")
            if not mid:
                continue
            tenant_id = state["report_meta"].get("tenant_id")
            from src.services.fleet_machine_resolver import resolve_fleet_machine

            resolved = resolve_fleet_machine(mid, tenant_id)
            if not resolved:
                continue
            t_id, a_id, twin, tenant_profile = resolved
            row = latest_row_from_machine_payload(machine)
            if not row:
                history = machine.get("history") if isinstance(machine.get("history"), dict) else {}
                rows = history.get("rows") or []
                row = rows[-1] if rows else {}
            if not row:
                continue

            packet = build_ingest_packet(t_id, a_id, row, twin)
            if not packet.signals:
                continue

            try:
                result: DiagnosticResult = global_orchestrator.run_pipeline(packet, twin, tenant_profile)
                diagnostics[mid] = result.model_dump()
                trace.append(f"ReportGraph:PipelineComplete:{mid}")
            except Exception as e:
                logger.warning(f"Report agent pipeline failed for {mid}: {e}")
                trace.append(f"ReportGraph:PipelineFailed:{mid}")

        return {"diagnostics": diagnostics, "agent_trace": trace}

    def _node_build_graph(self, state: ReportInsightState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace") or [])
        trace.append("ReportGraph:BuildInsightGraph")
        meta = state["report_meta"]
        diagnostics = state.get("diagnostics") or {}
        flow = _build_agent_flow_graph(diagnostics)
        series = _build_parameter_series(state.get("upstream_payload"), meta.get("selected_parameters"))
        graph = {**flow, "series": series}
        return {"graph": graph, "agent_trace": trace}

    def _node_synthesize(self, state: ReportInsightState) -> Dict[str, Any]:
        trace = list(state.get("agent_trace") or [])
        trace.append("ReportGraph:SynthesizeInsights")
        meta = state["report_meta"]
        diagnostics = state.get("diagnostics") or {}
        payload = state.get("upstream_payload")

        alert_count = 0
        findings: List[str] = []
        for machine in _machine_rows(payload):
            mid = str(machine.get("machineId") or "")
            findings.extend(_alert_lines(mid, machine.get("alerts")))
            alerts = machine.get("alerts")
            if isinstance(alerts, list):
                alert_count += len(alerts)

        for mid, diag in diagnostics.items():
            findings.extend(_findings_from_diagnostic(mid, diag))

        if not findings:
            findings = ["Agents found no isolated defects on the latest evaluated samples in this window."]

        summary = _summary_from_agents(meta, diagnostics, alert_count)
        return {
            "summary": summary,
            "findings": findings[:30],
            "agent_trace": trace,
        }

    def _compile(self):
        g = StateGraph(ReportInsightState)
        g.add_node("fetch_upstream", self._node_fetch_upstream)
        g.add_node("run_agents", self._node_run_agent_pipelines)
        g.add_node("build_graph", self._node_build_graph)
        g.add_node("synthesize", self._node_synthesize)
        g.set_entry_point("fetch_upstream")
        g.add_edge("fetch_upstream", "run_agents")
        g.add_edge("run_agents", "build_graph")
        g.add_edge("build_graph", "synthesize")
        g.add_edge("synthesize", END)
        return g.compile()

    def run(self, report_meta: Dict[str, Any], machine_ids: List[str]) -> Dict[str, Any]:
        initial: ReportInsightState = {
            "report_meta": report_meta,
            "machine_ids": machine_ids,
            "diagnostics": {},
            "agent_trace": [],
        }
        final = self.workflow.invoke(initial)
        return {
            "summary": final.get("summary") or "",
            "findings": final.get("findings") or [],
            "graph": final.get("graph") or {},
            "meta": {
                "agentTrace": final.get("agent_trace") or [],
                "machinesEvaluated": list((final.get("diagnostics") or {}).keys()),
                "generatedAt": time.time(),
            },
        }


report_insight_orchestrator = ReportInsightOrchestrator()
