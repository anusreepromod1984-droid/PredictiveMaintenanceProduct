"""Groq enrichment for fault-assistant AI Assistant card."""

from src.services import fault_assistant_llm as fa_llm


def test_enrich_fault_assistant_uses_llm_when_mocked(monkeypatch):
    base = {
        "machineId": "G-Sensor-01",
        "severity": "critical",
        "headline": "Old headline",
        "summary": "• Agent Alpha: ...",
        "recommendedActions": ["Step A"],
    }

    def _fake_chat(**kwargs):
        return {
            "headline": "High vibration on G-Sensor-01 needs inspection",
            "summary": "Vibration exceeded the alert band.\nCheck mounting before next run.",
            "recommendedActions": ["Inspect coupling", "Verify sensor calibration"],
            "faultExplanation": {
                "whatIsIt": "Alert-level vibration",
                "rootCause": "Reading above configured limit",
                "riskImpact": "CRITICAL",
            },
        }

    monkeypatch.setattr(fa_llm.settings, "ASSISTANT_LLM_ENABLED", True)
    monkeypatch.setattr(fa_llm.settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(fa_llm, "chat_completions_json", _fake_chat)

    out = fa_llm.enrich_fault_assistant(
        base,
        machine_id="G-Sensor-01",
        breaches=[{"parameter": "vibration.overall", "value": 0.15}],
    )
    assert "Agent Alpha" not in out["summary"]
    assert out["headline"].startswith("High vibration")
    assert len(out["recommendedActions"]) == 2
