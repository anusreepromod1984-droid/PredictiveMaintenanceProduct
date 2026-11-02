"""KPI-only fallback when upstream /api/ai/query fails."""

from src.services.plant_report_summarizer import generate_report_summary
from src.services.threshold_provider import UpstreamFleetClient


class _FailingUpstream(UpstreamFleetClient):
    def __init__(self):
        super().__init__(base_url="https://example.test", api_key="test-key", timeout_s=1.0)

    def query(self, body):  # noqa: ARG002
        return None


def test_generate_report_summary_kpi_fallback_when_upstream_empty():
    client = _FailingUpstream()
    result = generate_report_summary(
        company="Greenbotz",
        report_type="FLEET_SUMMARY",
        title="Fleet summary — 1 machines",
        machine_ids=["G-Sensor-01"],
        date_range={"start": "2026-10-22T00:00:00Z", "end": "2026-10-29T00:00:00Z"},
        timezone_name="Asia/Calcutta",
        selected_parameters=None,
        kpis=[{"label": "Alerts", "value": "7706"}, {"label": "Online now", "value": "1 / 1"}],
        tenant_id="greenbotz_platform",
        upstream_client=client,
    )
    assert result["summary"].strip()
    assert isinstance(result["findings"], list) and result["findings"]
    assert result["meta"].get("upstreamDegraded") is True
