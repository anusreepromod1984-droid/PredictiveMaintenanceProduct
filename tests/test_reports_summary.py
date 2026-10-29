"""Plant report AI summary endpoint and fault-assistant reportSummary field."""

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import reports as reports_route
from src.services.report_summary_store import save_machine_report_summary

client = TestClient(app)


def test_post_reports_summary(monkeypatch):
    def _fake(**kwargs):
        return {
            "summary": "Test plant report narrative.",
            "findings": ["g-sensor-01: Agent consensus health HEALTHY (score 98)"],
            "graph": {
                "kind": "agent_insight",
                "pipeline": {"nodes": [{"id": "alpha"}], "edges": []},
                "series": [],
            },
            "meta": {"machinesEvaluated": kwargs["machine_ids"]},
        }

    monkeypatch.setattr(reports_route, "generate_report_summary", _fake)

    payload = {
        "company": "Greenbotz",
        "reportType": "daily",
        "title": "Daily Plant Report",
        "machineIds": ["g-sensor-01"],
        "tenantId": "greenbotz_platform",
        "dateRange": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-07T23:59:59Z"},
        "timezone": "Asia/Kolkata",
        "selectedParameters": ["imuAcceleration"],
        "kpis": [{"label": "Uptime", "value": "99.1%"}],
    }
    resp = client.post("/api/v1/reports/summary", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "Test plant report narrative."
    assert "g-sensor-01" in data["findings"][0]
    assert "graph" not in data


def test_post_reports_summary_platform_webhook_without_tenant_id(monkeypatch):
    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {
            "summary": "Platform webhook summary.",
            "findings": ["ok"],
            "graph": {"kind": "agent_insight"},
        }

    monkeypatch.setattr(reports_route, "generate_report_summary", _fake)

    payload = {
        "companySlug": "greenbotz-platform-pvt-ltd",
        "reportType": "FLEET_SUMMARY",
        "title": "Fleet summary — 1 machines",
        "machineIds": ["G-Sensor-01"],
        "from": "2026-10-22T06:56:20.212Z",
        "to": "2026-10-29T06:56:20.212Z",
        "timezone": "Asia/Calcutta",
        "fields": None,
        "kpis": [{"label": "Alerts", "value": "7706"}],
    }
    resp = client.post("/api/v1/reports/summary", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"] == "Platform webhook summary."
    assert captured["tenant_id"] == "greenbotz_platform"
    assert captured["date_range"]["start"] == payload["from"]


def test_fault_assistant_includes_report_summary():
    save_machine_report_summary(
        "greenbotz_platform",
        "g-sensor-01",
        summary="Latest weekly report summary text.",
        findings=["No critical alerts in period."],
        title="Weekly Report",
        report_type="weekly",
    )
    resp = client.post(
        "/api/v1/machines/g-sensor-01/fault-assistant?tenant_id=greenbotz_platform",
        json={"breaches": [{"parameter": "vibration.overall", "value": 0.15, "threshold": "outside 0.0–0.1"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "reportSummary" in data
    assert data["reportSummary"]["summary"] == "Latest weekly report summary text."
    assert data["reportSummary"]["findings"] == ["No critical alerts in period."]
