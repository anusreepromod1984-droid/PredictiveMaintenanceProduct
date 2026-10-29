from fastapi.testclient import TestClient

from src.api.main import app
from src.core.buffer import platform_buffer

client = TestClient(app)
import pytest
from src.config import settings

@pytest.fixture(autouse=True)
def _disable_live_llm(monkeypatch):
    monkeypatch.setattr(settings, "ASSISTANT_LLM_ENABLED", False)


def test_fault_assistant_synthesizes_breach_when_no_buffer():
    payload = {
        "motorFaults": [],
        "telemetry": {"vibration.overall": 0.15},
        "breaches": [
            {
                "parameter": "vibration.overall",
                "value": 0.15,
                "threshold": "outside 0.0–0.1",
            }
        ],
    }
    resp = client.post("/api/v1/machines/G-Sensor-01/fault-assistant", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["severity"] == "critical"
    assert data["archetype"] == "VIBRATION_API_LIMIT_EXCEEDED"
    assert "0.15" in data["headline"]
    assert "outside 0.0–0.1" in data["headline"]
    assert len(data["repairOptions"]) > 0
    assert data["repairOptions"][0]["urgency"] == "Immediate"
    assert data["repairOptions"][0]["category"] == "Immediate Triage"


def test_fault_assistant_overrides_nominal_diagnostic_when_breach_present():
    # Insert a nominal diagnostic into the buffer
    nominal_diag = {
        "trace_id": "test-trace-01",
        "timestamp": 1728900000.0,
        "health_score": 98.0,
        "health_status": "HEALTHY",
        "operating_regime": "STANDBY",
        "active_faults": [],
        "prescriptive_action": {},
        "prognostics": {"rul_days": 180.0},
    }
    platform_buffer.save_diagnostic("greenbotz_platform", "g-sensor-01", nominal_diag)

    # Calling with active alert breach
    payload = {
        "breaches": [
            {
                "parameter": "vibration.overall",
                "value": 0.1,
                "threshold": "outside 0.0–0.1",
            }
        ],
        "telemetry": {"vibration.overall": 0.1},
    }
    resp = client.post("/api/v1/machines/G-Sensor-01/fault-assistant", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["severity"] == "critical"
    assert data["archetype"] == "VIBRATION_API_LIMIT_EXCEEDED"
    assert "Immediate Triage" in data["repairOptions"][0]["category"]


def test_fault_assistant_provides_repair_options_for_vibration_exceeded_diag():
    exceeded_diag = {
        "trace_id": "test-trace-02",
        "timestamp": 1728900000.0,
        "health_score": 25.0,
        "health_status": "CRITICAL",
        "operating_regime": "RUNNING",
        "active_faults": [
            {
                "fault_code": "VIBRATION_API_LIMIT_EXCEEDED",
                "description": "Vibration 0.1 mm/s is outside the configured alert range 0-0.1 mm/s",
                "severity": "CRITICAL",
                "failure_domain": "MECHANICAL",
            }
        ],
        "prescriptive_action": {},
        "prognostics": None,
    }
    platform_buffer.save_diagnostic("greenbotz_platform", "g-sensor-01", exceeded_diag)

    resp = client.post("/api/v1/machines/G-Sensor-01/fault-assistant", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["severity"] == "critical"
    assert data["archetype"] == "VIBRATION_API_LIMIT_EXCEEDED"
    assert len(data["repairOptions"]) > 0
    assert data["repairOptions"][0]["urgency"] == "Immediate"
    assert data["pipelineDetails"]["rul_prediction"]["rul_days"] == 7.0
