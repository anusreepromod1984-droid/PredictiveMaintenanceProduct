"""Groq analyst assist for diagnostic pipeline (non-overriding second opinion)."""

from src.services import agent_analyst_assist as assist


def test_merge_analyst_into_card():
    diagnosis = {
        "headline": "Vibration high",
        "severity": "critical",
        "recommendedActions": ["Inspect bearings"],
        "analystAssist": {
            "crossCheck": "Vibration spike aligns with mechanical loading; verify mount bolts.",
            "investigationLeads": ["Check coupling alignment", "Compare handheld analyzer"],
            "cautions": ["Sensor saturation possible"],
        },
    }
    out = assist.merge_analyst_into_card(diagnosis)
    assert "mount bolts" in out["summary"]
    assert "Check coupling alignment" in out["recommendedActions"]
    assert "Alpha" not in out["summary"]


def test_augment_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(assist.settings, "AGENT_ANALYST_LLM_ENABLED", False)
    out = assist.augment_diagnostic_with_analyst(
        tenant_id="t1",
        asset_id="a1",
        signals={"vibration.overall": 0.2},
        alpha={"can_proceed_to_physics": True},
        beta={"is_operating": True, "regime": "NORMAL"},
        gamma={"health_status": "WARNING", "health_score": 70},
        delta=None,
        result_summary={"health_status": "WARNING", "health_score": 70, "fault_count": 0, "fault_codes": []},
    )
    assert out is None


def test_augment_with_mocked_groq(monkeypatch):
    def _fake(**kwargs):
        return {
            "crossCheck": "Regime normal; vibration worth a walkdown.",
            "investigationLeads": ["Walkdown inspection"],
            "signalQualityNotes": "Signal path acceptable.",
        }

    monkeypatch.setattr(assist.settings, "AGENT_ANALYST_LLM_ENABLED", True)
    monkeypatch.setattr(assist.settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(assist, "chat_completions_json", _fake)

    out = assist.augment_diagnostic_with_analyst(
        tenant_id="greenbotz_platform",
        asset_id="g-sensor-01",
        signals={"vibration.overall": 0.15},
        alpha={"can_proceed_to_physics": True},
        beta={"is_operating": True},
        gamma={"health_status": "CRITICAL", "faults": []},
        delta={"description": "Inspect"},
        result_summary={
            "health_status": "CRITICAL",
            "health_score": 25,
            "operating_regime": "NORMAL",
            "fault_count": 1,
            "fault_codes": ["VIBRATION_API_LIMIT_EXCEEDED"],
        },
    )
    assert out is not None
    assert out["source"] == "groq_analyst_assist"
    assert "walkdown" in out["crossCheck"].lower()
