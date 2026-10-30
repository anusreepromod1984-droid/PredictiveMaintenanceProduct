"""Platform service-report analyze and effect webhooks."""

from fastapi.testclient import TestClient

from src.api.main import app
from src.services.service_report_effect import build_service_effect, numbers_are_grounded

client = TestClient(app)

_EFFECT = {
    "documentId": "doc-1",
    "serviceRecordId": "rec-1",
    "companySlug": "propel-industries-pvt-ltd",
    "machine": {"id": "vibe-rx-123", "name": "VIBE - RX", "model": "VIBE - RX"},
    "service": {
        "serviceDate": "2026-10-24",
        "type": "PREVENTIVE",
        "vendor": "ABC Motors",
        "workDone": ["Bearings replaced"],
        "partsReplaced": [{"name": "Bearing 6205-2RS", "qty": 2}],
        "issuesFound": [],
        "recommendations": ["Re-check alignment in 30 days"],
        "nextServiceDue": "2027-01-24",
    },
    "comparison": {
        "readiness": "COMPLETE",
        "window": {
            "before": {"start": "2026-10-17", "end": "2026-10-23"},
            "after": {"start": "2026-10-24", "end": "2026-10-30"},
            "afterDaysCollected": 7,
            "afterDaysRequired": 7,
        },
        "beforeAfter": [
            {
                "key": "vibration.overall",
                "parameter": "Vibration overall",
                "unit": None,
                "before": 5.101,
                "after": 4.801,
                "changePct": -5.9,
                "verdict": "IMPROVED",
                "betterWhen": "lower",
                "beforeHours": 32,
                "afterHours": 34,
            },
            {
                "key": "energyMeter.pf",
                "parameter": "Power factor",
                "before": 1.322,
                "after": 0.921,
                "changePct": -30.3,
                "verdict": "WORSE",
                "betterWhen": "higher",
            },
        ],
        "alerts": {"before": None, "after": 19},
        "readingChecks": [
            {
                "reading": "Vibration (after)",
                "reported": {"value": 1.8, "unit": "mm/s"},
                "sensor": {"parameter": "Vibration overall", "value": 4.801, "unit": None},
                "result": "UNIT_UNKNOWN",
            }
        ],
    },
}


def test_effect_quotes_only_request_numbers():
    out = build_service_effect(_EFFECT)
    assert out["effect"] == "NO_CHANGE"
    blob = " ".join([out["headline"], *out["points"], *out["concerns"], *out["nextSteps"]])
    assert numbers_are_grounded(blob, _EFFECT)
    assert "5.101" in blob
    assert "4.801" in blob
    assert "-5.9" in blob
    assert "UNIT" not in out["effect"]
    assert any("not recorded" in c or "cannot be compared" in c for c in out["concerns"])
    # "30 days" contains 30, which is not a number in the request, so that step is dropped.
    assert all(numbers_are_grounded(s, _EFFECT) for s in out["nextSteps"])


def test_effect_too_early():
    body = dict(_EFFECT)
    body["comparison"] = dict(_EFFECT["comparison"])
    body["comparison"]["readiness"] = "PARTIAL"
    body["comparison"]["window"] = {
        "afterDaysCollected": 3,
        "afterDaysRequired": 7,
    }
    out = build_service_effect(body)
    assert out["effect"] == "TOO_EARLY"
    assert numbers_are_grounded(out["points"][0], body)


def test_analyze_endpoint_returns_bare_object(monkeypatch):
    from src.api.routes import service_reports as route

    def _fake(body):
        return {
            "serviceDate": "2026-10-24",
            "vendor": "ABC Motors Service",
            "technician": None,
            "type": "PREVENTIVE",
            "workDone": ["Bearings replaced"],
            "partsReplaced": [],
            "readings": [],
            "issuesFound": [],
            "recommendations": [],
            "nextServiceDue": None,
            "cost": None,
            "summary": "Bearings replaced.",
            "machineIdentity": None,
            "confidence": {"serviceDate": 0.95},
        }

    monkeypatch.setattr(route, "analyze_service_report", _fake)
    resp = client.post(
        "/api/v1/service-reports/analyze",
        json={
            "documentId": "f8c3b432-6f61-4fc3-8bfa-aec3ddcc0148",
            "companySlug": "propel-industries-pvt-ltd",
            "machine": {"id": "vibe-rx-123", "name": "VIBE - RX", "model": "VIBE - RX"},
            "files": [
                {
                    "url": "https://example.com/a.pdf",
                    "type": "application/pdf",
                    "name": "a.pdf",
                    "size": 100,
                }
            ],
            "hints": {"serviceDate": "2026-10-24", "vendor": None},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "PREVENTIVE"
    assert "status" not in data


def test_effect_endpoint():
    resp = client.post("/api/v1/service-reports/effect", json=_EFFECT)
    assert resp.status_code == 200
    data = resp.json()
    assert data["effect"] in {"IMPROVED", "NO_CHANGE", "WORSE", "TOO_EARLY"}
    assert "headline" in data
