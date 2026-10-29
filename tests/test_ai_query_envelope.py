from src.services.ai_query_envelope import normalize_fleet_query_payload
from src.services.fleet_query_service import _machine_rows
from src.services.plant_report_summarizer import _build_query_body


def test_normalize_pdm_envelope():
    raw = {
        "status": True,
        "data": {"count": 1, "machines": [{"machineId": "G-Sensor-01"}]},
    }
    inner = normalize_fleet_query_payload(raw)
    assert inner is not None
    assert inner["count"] == 1
    assert _machine_rows(raw)[0]["machineId"] == "G-Sensor-01"


def test_build_query_body_omits_unsupported_history_fields():
    body = _build_query_body(
        ["G-Sensor-01"],
        {"start": "2026-10-22T06:56:20.212Z", "end": "2026-10-29T06:56:20.212Z"},
        "Asia/Calcutta",
        None,
    )
    history = body["history"]
    assert history["duration"] == "8d"
    assert "start" not in history
    assert "end" not in history
    assert "timezone" not in history
