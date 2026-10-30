"""Professional plant report Remarks formatting."""

from src.services.report_remarks_format import (
    format_duration_ms,
    polish_remarks_output,
    strip_disclaimers,
    _replace_ms_in_text,
    build_operational_brief,
)


def test_format_duration_ms():
    assert format_duration_ms(45_000) == "45.0 s"
    assert format_duration_ms(2_097_702) == "35.0 min"
    assert format_duration_ms(142_785_819) == "1.7 days"


def test_replace_ms_in_text():
    s = "longest outage 2,097,702 ms during the period."
    out = _replace_ms_in_text(s)
    assert "ms" not in out.lower()
    assert "35.0 min" in out

    s2 = "the longest lasting ~2.5 million ms"
    out2 = _replace_ms_in_text(s2)
    assert "million" not in out2.lower()
    assert "ms" not in out2.lower()


def test_strip_disclaimers():
    text = "Good summary.\nWritten by the AI service from this report's data."
    assert "Written by" not in strip_disclaimers(text)
    assert "Good summary" in strip_disclaimers(text)


def test_polish_remarks_adds_priority_and_fixes_ms():
    summary = "Asset saw 142785819 ms idle."
    findings = ["Check vibration (0.12 vs 0.1 limit)."]
    s, f = polish_remarks_output(summary, findings)
    assert "ms" not in s.lower()
    assert f[0].startswith("Priority 1 —")


def test_build_operational_brief_humanizes():
    ctx = {
        "title": "Fleet summary",
        "period": {"start": "2026-10-23T05:24:00Z", "end": "2026-10-30T05:24:00Z", "timezone": "Asia/Calcutta"},
        "reportKpis": [{"label": "Alerts", "value": "14507"}],
        "telemetrySnapshot": {"machines": []},
        "upstreamEnrichment": [{"machineId": "G-Sensor-01", "uptime": {"idleDurationMs": 142785819}}],
    }
    brief = build_operational_brief(ctx)
    assert brief["authoritativeKpis"][0]["value"] == "14507"
    assert "ms" not in str(brief["operationalMetrics"]).lower() or "days" in str(brief["operationalMetrics"])
