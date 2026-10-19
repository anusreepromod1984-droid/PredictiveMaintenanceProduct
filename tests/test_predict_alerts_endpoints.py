"""
Unit & Integration Tests for Prediction, Alerts, and Production Tenancy
======================================================================
"""

import time
from fastapi.testclient import TestClient
from src.api.main import app
from src.db.historian import platform_historian
from src.services.threshold_provider import UpstreamFleetClient

client = TestClient(app)


def test_predict_rul_history_nominal():
    # Seed a diagnostic record into historian for G-Sensor-01
    platform_historian.persist_diagnostic(
        tenant_id="greenbotz_platform",
        asset_id="g-sensor-01",
        result={
            "trace_id": "test_trace_rul_001",
            "timestamp": time.time(),
            "health_score": 98.5,
            "health_status": "HEALTHY",
            "operating_regime": "FULL_LOAD",
            "active_faults": [],
            "prognostics": {"rul_days": 169.0},
        }
    )

    resp = client.get("/api/v1/predict/rul_history/G-Sensor-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["machine_id"] == "G-Sensor-01"
    assert "points" in data
    assert len(data["points"]) >= 2
    assert "repair_window" in data
    assert "pm_tasks" in data
    assert data["latest_rul_days"] is not None


def test_predict_history_records():
    platform_historian.persist_diagnostic(
        tenant_id="propel_industries",
        asset_id="gbz-rx-1",
        result={
            "trace_id": "test_trace_hist_002",
            "timestamp": time.time(),
            "health_score": 85.0,
            "health_status": "DEGRADED",
            "operating_regime": "FULL_LOAD",
            "active_faults": [{"fault_code": "BPFI", "failure_domain": "BEARING"}],
            "prognostics": {"rul_days": 42.0},
        }
    )

    resp = client.get("/api/v1/predict/history/gbz-rx-1?limit=10&tenant_id=propel_industries")
    assert resp.status_code == 200
    records = resp.json()
    assert isinstance(records, list)
    assert any(r.get("trace_id") == "test_trace_hist_002" for r in records)


def test_alerts_test_and_cooldown_clear():
    # 1. Trigger test alert
    payload = {
        "machine_id": "G-Sensor-01",
        "severity": "CRITICAL",
        "defect_code": "BPFI",
        "defect_name": "Bearing Inner Race Spalling",
        "custom_note": "Production test dispatch",
    }
    resp = client.post("/api/v1/alerts/test", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DISPATCHED"
    assert data["machine_id"] == "G-Sensor-01"

    # 2. Check alert history audit log
    h_resp = client.get("/api/v1/alerts/history?machine_id=G-Sensor-01")
    assert h_resp.status_code == 200
    history = h_resp.json()
    assert isinstance(history, list)
    assert any(h["defect_code"] == "BPFI" for h in history)

    # 3. Clear cooldown
    c_resp = client.post("/api/v1/alerts/clear", json={"machine_id": "G-Sensor-01"})
    assert c_resp.status_code == 200
    assert c_resp.json()["cleared"] is True


def test_upstream_fleet_client_circuit_breaker():
    client_cb = UpstreamFleetClient(base_url="http://invalid-host-99999.test", api_key="test-key", timeout_s=0.1)
    assert client_cb.is_circuit_open is False

    # Simulate 3 failures to trigger circuit breaker
    client_cb._record_failure()
    client_cb._record_failure()
    assert client_cb.is_circuit_open is False
    client_cb._record_failure()

    # Circuit should now be open
    assert client_cb.is_circuit_open is True
    # Subsequent calls should return None immediately without making network calls
    assert client_cb.get_capabilities() is None
    assert client_cb.query({}) is None
