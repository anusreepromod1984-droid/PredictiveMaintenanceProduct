"""Agent insight graph structure from diagnostic runs."""

from src.agents.report_insight_orchestrator import _build_agent_flow_graph, _findings_from_diagnostic


def test_agent_flow_graph_structure():
    diagnostics = {
        "g-sensor-01": {
            "health_status": "WARNING",
            "health_score": 72.0,
            "agent_trace": [
                "AgentAlpha:SignalIntegrity",
                "AgentBeta:RegimeDetector",
                "AgentGamma:MultiVerticalDiagnostics",
                "AgentDelta:PrescriptiveAction",
            ],
            "active_faults": [],
        }
    }
    graph = _build_agent_flow_graph(diagnostics)
    assert graph["kind"] == "agent_insight"
    assert len(graph["pipeline"]["nodes"]) == 4
    assert graph["machines"][0]["id"] == "machine:g-sensor-01"
    assert graph["machines"][0]["status"] == "warning"


def test_findings_from_diagnostic_includes_agents():
    diag = {
        "health_status": "CRITICAL",
        "health_score": 30,
        "active_faults": [
            {
                "severity": "CRITICAL",
                "fault_code": "BPFI",
                "description": "Bearing fault",
                "failure_domain": "BEARING",
            }
        ],
        "prognostics": {"rul_days": 14, "method": "weibull_hazard"},
        "prescriptive_action": {"description": "Replace bearing assembly"},
    }
    lines = _findings_from_diagnostic("m1", diag)
    assert any("Agent Gamma RUL" in line for line in lines)
    assert any("Agent Delta" in line for line in lines)
