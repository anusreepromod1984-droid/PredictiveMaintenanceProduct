"""LLM and rules synthesis for plant report Remarks."""

import json

from src.services import report_llm_synthesizer as synth


def test_normalize_llm_result():
    summary, findings = synth._normalize_llm_result(
        {"summary": " Fleet is stable. ", "findings": ["Check vibration", "", "Review limits"]}
    )
    assert summary == "Fleet is stable."
    assert findings == ["Check vibration", "Review limits"]


def test_synthesize_with_rules_no_agent_names():
    context = {
        "title": "Fleet summary — G-Sensor-01",
        "machineIds": ["G-Sensor-01"],
        "period": {"label": "8d", "timezone": "Asia/Calcutta"},
        "reportKpis": [
            {"label": "Alerts", "value": "8386"},
            {"label": "Online now", "value": "1 / 1"},
        ],
        "telemetrySnapshot": {
            "machinesReturned": 1,
            "alertsInSnapshot": 1,
            "machines": [
                {
                    "machineId": "G-Sensor-01",
                    "alertsInSnapshot": [
                        {"parameter": "vibration.overall", "value": 0.12, "severity": "high"}
                    ],
                    "parameterTrends": [
                        {"parameter": "vibration.overall", "max": 25.29, "samples": 191}
                    ],
                }
            ],
        },
    }
    out = synth.synthesize_with_rules(context)
    assert "Alpha" not in out["summary"]
    assert "LangGraph" not in out["summary"]
    assert "8386" in out["summary"] or any("8386" in f for f in out["findings"])
    assert out["source"] == "rules"


def test_synthesize_report_insights_uses_llm_when_mocked(monkeypatch):
    context = {"title": "Test", "reportKpis": [], "telemetrySnapshot": {"machines": []}}

    def _fake_llm(ctx):
        assert ctx["title"] == "Test"
        return {
            "summary": "One asset needs attention due to sustained vibration exceedance.",
            "findings": ["Inspect coupling", "Verify sensor mount"],
            "source": "llm",
        }

    monkeypatch.setattr(synth.settings, "REPORT_LLM_ENABLED", True)
    monkeypatch.setattr(synth.settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(synth.settings, "REPORT_LLM_API_KEY", "")
    monkeypatch.setattr(synth.settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(synth, "synthesize_with_llm", _fake_llm)

    out = synth.synthesize_report_insights(context)
    assert out["source"] == "llm"
    assert "vibration" in out["summary"].lower()


def test_extract_json_object_with_fence():
    from src.services.llm_client import extract_json_object

    raw = 'Here is output:\n```json\n{"summary": "Ok", "findings": ["a"]}\n```'
    parsed = extract_json_object(raw)
    assert parsed["summary"] == "Ok"
