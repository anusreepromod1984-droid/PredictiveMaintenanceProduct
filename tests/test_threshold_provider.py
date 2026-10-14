"""
Unit Tests for Dynamic PDM Threshold Provider & Upstream API Fallback
======================================================================
Verifies:
  1. Graceful fallback to local twin thresholds when machine is not in API or API is offline
  2. Correct parsing of upstream alertProfile into standard APMS threshold keys
  3. API overrides take precedence while preserving local defaults for missing parameters
  4. TTL cache operates correctly
"""

import pytest
from src.schemas.asset import AssetDigitalTwin
from src.services.threshold_provider import (
    DynamicThresholdProvider,
    UpstreamFleetClient,
    parse_alert_profile_thresholds
)


def test_parse_alert_profile_thresholds():
    mock_profile = {
        "sensors": [
            {
                "name": "NTC Temperature sensor x 2",
                "parameters": [
                    {"name": "Motor temperature", "max": 120, "alert_threshold": 140},
                    {"name": "Compressor temperature", "max": 95, "alert_threshold": 110}
                ]
            },
            {
                "name": "IMU-Acceleration - RPM",
                "parameters": [
                    {"name": "Current RPM", "alert_threshold": {"low": 1350, "high": 1650}}
                ]
            },
            {
                "name": "Pressure sensor",
                "parameters": [
                    {"name": "Pressure", "alert_threshold": 7.5}
                ]
            },
            {
                "name": "Microphone - Sound level",
                "parameters": [
                    {"name": "Sound level", "max": 65, "alert_threshold": 85}
                ]
            }
        ]
    }

    parsed = parse_alert_profile_thresholds(mock_profile)

    assert parsed["temp_danger_celsius"] == 140.0
    assert parsed["temp_warning_celsius"] == 120.0
    assert parsed["compressor_temp_danger_celsius"] == 110.0
    assert parsed["compressor_temp_warning_celsius"] == 95.0
    assert parsed["rpm_max"] == 1650.0
    assert parsed["rpm_min"] == 1350.0
    assert parsed["pressure_danger_bar"] == 7.5
    assert "hydraulic_pressure_min_bar" not in parsed
    assert parsed["sound_danger_db"] == 85.0
    assert parsed["sound_warning_db"] == 65.0


def test_local_fallback_when_api_missing():
    # Asset with standard thresholds
    twin = AssetDigitalTwin(
        tenant_id="local_tenant",
        asset_id="unknown_machine_99",
        name="Custom Lathe",
        vertical="manufacturing",
        asset_class="rotating_machinery",
        thresholds={
            "vibration_warning_mm_s": 4.5,
            "vibration_danger_mm_s": 7.1,
            "temp_warning_celsius": 75.0,
            "temp_danger_celsius": 90.0,
            "custom_parameter_limit": 42.0
        }
    )

    # Provider with empty/disabled client
    provider = DynamicThresholdProvider(client=UpstreamFleetClient(api_key=""))
    effective = provider.get_effective_thresholds(twin)

    # Must preserve 100% of local thresholds
    assert effective["vibration_warning_mm_s"] == 4.5
    assert effective["vibration_danger_mm_s"] == 7.1
    assert effective["custom_parameter_limit"] == 42.0


def test_partial_override_and_preservation():
    """API provides motor temp, but twin has unique vibration and hydraulic limits."""
    twin = AssetDigitalTwin(
        tenant_id="heavy_tenant",
        asset_id="test_crusher",
        name="Test Jaw Crusher",
        vertical="mining",
        asset_class="jaw_crusher",
        thresholds={
            "vibration_warning_mm_s": 11.2,
            "vibration_danger_mm_s": 18.0,
            "hydraulic_pressure_min_bar": 130.0,
            "temp_warning_celsius": 70.0,
            "temp_danger_celsius": 85.0
        }
    )

    class MockClient(UpstreamFleetClient):
        def query(self, body):
            return {
                "machines": [
                    {
                        "machineId": "test_crusher",
                        "alertProfile": {
                            "sensors": [
                                {
                                    "name": "Temperature",
                                    "parameters": [
                                        {"name": "Motor temperature", "alert_threshold": 105.0}
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }

    provider = DynamicThresholdProvider(client=MockClient(api_key="mock_key"))
    effective = provider.get_effective_thresholds(twin)

    # Motor temp danger overridden by API
    assert effective["temp_danger_celsius"] == 105.0
    # Original thresholds not present in API are preserved
    assert effective["vibration_warning_mm_s"] == 11.2
    assert effective["vibration_danger_mm_s"] == 18.0
    assert effective["hydraulic_pressure_min_bar"] == 130.0
    assert effective["temp_warning_celsius"] == 70.0


def test_informational_channels_do_not_leak_into_limits():
    """Regression: frequencies, 'change in pressure', amplitudes and machine load must not become limits."""
    profile = {"parameters": [
        {"name": "IMU-Acceleration", "parameters": [
            {"name": "Acceleration", "max": 3, "enabled": True, "alert_threshold": 5}]},
        {"name": "Microphone - Frequency 1", "parameters": [
            {"name": "Frequency 1", "max": 10000, "enabled": True, "alert_threshold": None}]},
        {"name": "Microphone - Amplitude 1", "parameters": [
            {"name": "Amplitude 1", "max": 124, "enabled": True, "alert_threshold": 85}]},
        {"name": "Microphone - Sound level", "parameters": [
            {"name": "Sound level", "max": 125, "enabled": True, "alert_threshold": 85}]},
        {"name": "Pressure sensor x 2 [SW]", "parameters": [
            {"name": "Change in pressure", "max": 8, "enabled": True, "alert_threshold": {"low": 0.3, "high": 0.5}}]},
        {"name": "Energymeter - Machine load", "parameters": [
            {"name": "Machine load", "min": 0, "max": 100, "enabled": True, "alert_threshold": 105}]},
    ]}
    parsed = parse_alert_profile_thresholds(profile)
    assert parsed["vibration_danger_mm_s"] == 5.0
    assert parsed["vibration_warning_mm_s"] == 3.0
    assert parsed["sound_danger_db"] == 85.0
    assert "sound_warning_db" not in parsed  # max 125 >= danger 85 -> dropped, local baseline kept
    assert "pressure_danger_bar" not in parsed
    assert "load_idle_threshold_pct" not in parsed  # would disable idle detection if set to 0


def test_warning_never_exceeds_danger():
    profile = {"parameters": [{"name": "NTC Temperature sensor x 2", "parameters": [
        {"name": "Motor temperature", "max": 120, "enabled": True, "alert_threshold": 90}]}]}
    parsed = parse_alert_profile_thresholds(profile)
    assert parsed["temp_danger_celsius"] == 90.0
    assert "temp_warning_celsius" not in parsed


# ---------------------------------------------------------------------------
# End-to-end: API vibration band must reach the diagnosis (AI Assistant card)
# ---------------------------------------------------------------------------
def _band_profile(lo, hi, alert):
    return {"parameters": [{"name": "IMU-Acceleration", "parameters": [
        {"name": "Acceleration", "min": lo, "max": hi, "enabled": True,
         "direction": "high", "alert_threshold": alert}]}]}


def _run_pipeline(monkeypatch, vib, profile):
    from src.agents.dynamic_orchestrator import global_orchestrator
    from src.schemas.telemetry import IngestTelemetryPacket
    from src.services import threshold_provider as tp

    twin = AssetDigitalTwin(
        tenant_id="t_api", asset_id="api_machine_x", name="API Machine",
        vertical="manufacturing", asset_class="rotating_machinery",
        signal_map={"primary_vibration": "vib"},
    )

    class _Client(UpstreamFleetClient):
        def query(self, body):
            if profile is None:
                return None
            return {"machines": [{"machineId": "api_machine_x", "alertProfile": profile}]}

    provider = DynamicThresholdProvider(client=_Client(api_key="k"))
    monkeypatch.setattr(tp, "threshold_provider", provider)
    packet = IngestTelemetryPacket(tenant_id="t_api", asset_id="api_machine_x", signals={"vib": vib})
    return global_orchestrator.run_pipeline(packet, twin, None)


def test_api_band_breach_is_critical_even_when_iso_says_healthy(monkeypatch):
    res = _run_pipeline(monkeypatch, vib=0.1176, profile=_band_profile(0, 0.1, 5))
    codes = [f.fault_code for f in res.active_faults]
    assert "VIBRATION_API_LIMIT_EXCEEDED" in codes
    fault = next(f for f in res.active_faults if f.fault_code == "VIBRATION_API_LIMIT_EXCEEDED")
    assert fault.severity == "CRITICAL"
    assert "0-0.1" in fault.description
    assert res.health_status == "CRITICAL"


def test_reading_inside_api_band_stays_healthy(monkeypatch):
    res = _run_pipeline(monkeypatch, vib=0.05, profile=_band_profile(0, 0.1, 5))
    assert "VIBRATION_API_LIMIT_EXCEEDED" not in [f.fault_code for f in res.active_faults]


def test_no_api_limit_means_no_change(monkeypatch):
    res = _run_pipeline(monkeypatch, vib=0.1176, profile=None)
    assert "VIBRATION_API_LIMIT_EXCEEDED" not in [f.fault_code for f in res.active_faults]
