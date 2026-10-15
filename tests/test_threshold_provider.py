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


def test_env_file_threshold_resolution(monkeypatch):
    from src.services.threshold_provider import resolve_env_thresholds

    monkeypatch.setenv("VIBRATION_ALERT_BAND_MY_PUMP_01", "0.0,0.45")
    monkeypatch.setenv("VIBRATION_DANGER_MY_PUMP_01", "4.5")
    monkeypatch.setenv("MACHINE_THRESHOLDS_JSON", '{"custom_motor": {"vibration_band_max": 0.2, "temp_danger_celsius": 88.0}}')

    res1 = resolve_env_thresholds("my_pump_01")
    assert res1["vibration_band_min"] == 0.0
    assert res1["vibration_band_max"] == 0.45
    assert res1["vibration_danger_mm_s"] == 4.5

    res2 = resolve_env_thresholds("custom_motor")
    assert res2["vibration_band_max"] == 0.2
    assert res2["temp_danger_celsius"] == 88.0


def test_machine_spec_iso_standards_resolution():
    from src.services.threshold_provider import resolve_machine_spec_thresholds

    # Small machine < 15 kW -> ISO 20816 Class I (Warning 1.8, Danger 4.5)
    twin_small = AssetDigitalTwin(
        tenant_id="t1",
        asset_id="fan_01",
        name="Exhaust Fan 7.5kW",
        vertical="manufacturing",
        asset_class="centrifugal_fan",
        kinematics={"rated_kw": 7.5},
    )
    spec_small = resolve_machine_spec_thresholds(twin_small)
    assert spec_small["vibration_warning_mm_s"] == 1.8
    assert spec_small["vibration_danger_mm_s"] == 4.5

    # Medium machine 15-75 kW -> ISO 20816 Class II (Warning 2.8, Danger 7.1)
    twin_med = AssetDigitalTwin(
        tenant_id="t1",
        asset_id="pump_01",
        name="Feed Pump 37kW",
        vertical="water_treatment",
        asset_class="centrifugal_pump",
        kinematics={"rated_kw": 37.0},
    )
    spec_med = resolve_machine_spec_thresholds(twin_med)
    assert spec_med["vibration_warning_mm_s"] == 2.8
    assert spec_med["vibration_danger_mm_s"] == 7.1

    # Precision electronics / SMT
    twin_smt = AssetDigitalTwin(
        tenant_id="t2",
        asset_id="smt_pnp",
        name="Pick and Place Head",
        vertical="electronics_smt",
        asset_class="smt_pick_place",
    )
    spec_smt = resolve_machine_spec_thresholds(twin_smt)
    assert spec_smt["vibration_warning_mm_s"] == 0.8
    assert spec_smt["vibration_danger_mm_s"] == 1.5

    # Compressor -> ISO 10816-8
    twin_comp = AssetDigitalTwin(
        tenant_id="t3",
        asset_id="rotary_comp",
        name="Rotary Screw Compressor",
        vertical="industrial_gas",
        asset_class="compressor",
    )
    spec_comp = resolve_machine_spec_thresholds(twin_comp)
    assert spec_comp["vibration_warning_mm_s"] == 4.5
    assert spec_comp["vibration_danger_mm_s"] == 7.1
    assert spec_comp["compressor_temp_danger_celsius"] == 105.0


def test_four_tier_resolution_hierarchy(monkeypatch):
    """
    Tier 1 (API) overrides Tier 2 (Env)
    Tier 2 (Env) overrides Tier 3 (Machine Spec)
    Tier 3 (Machine Spec) overrides Tier 4 (Twin Baseline)
    """
    twin = AssetDigitalTwin(
        tenant_id="t_prec",
        asset_id="prec_asset_01",
        name="Feed Pump 45kW",
        vertical="general",
        asset_class="centrifugal_pump",
        kinematics={"rated_kw": 45.0},
        thresholds={"vibration_warning_mm_s": 9.9, "vibration_danger_mm_s": 15.0},
    )

    # 1. No API, no Env -> Machine spec applies (Class II: warning 2.8, danger 7.1)
    provider_spec = DynamicThresholdProvider(client=UpstreamFleetClient(api_key=""))
    eff1 = provider_spec.get_effective_thresholds(twin)
    assert eff1["vibration_warning_mm_s"] == 2.8
    assert eff1["vibration_danger_mm_s"] == 7.1

    # 2. Env set -> overrides Machine spec
    monkeypatch.setenv("VIBRATION_WARNING_PREC_ASSET_01", "3.2")
    provider_env = DynamicThresholdProvider(client=UpstreamFleetClient(api_key=""))
    eff2 = provider_env.get_effective_thresholds(twin)
    assert eff2["vibration_warning_mm_s"] == 3.2

    # 3. API set -> overrides Env
    class MockApiClient(UpstreamFleetClient):
        def query(self, body):
            return {
                "machines": [
                    {
                        "machineId": "prec_asset_01",
                        "alertProfile": {
                            "sensors": [
                                {
                                    "name": "IMU-Acceleration",
                                    "parameters": [
                                        {"name": "Acceleration", "max": 1.2, "alert_threshold": 2.1}
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }

    provider_api = DynamicThresholdProvider(client=MockApiClient(api_key="valid_key"))
    eff3 = provider_api.get_effective_thresholds(twin)
    assert eff3["vibration_warning_mm_s"] == 1.2
    assert eff3["vibration_danger_mm_s"] == 2.1


def test_extract_machine_details():
    from src.services.threshold_provider import extract_machine_details

    # Test extraction from explicit twin fields
    twin1 = AssetDigitalTwin(
        tenant_id="t1",
        asset_id="atlas_ga37",
        name="Screw Compressor",
        vertical="industrial",
        asset_class="compressor",
        make="Atlas Copco",
        model="GA-37",
        year_of_manufacture=2019,
        kinematics={"rated_kw": 37.0, "mounting_type": "flexible"},
    )
    details1 = extract_machine_details(twin1)
    assert details1["make"] == "Atlas Copco"
    assert details1["model"] == "GA-37"
    assert details1["year"] == 2019
    assert details1["power_kw"] == 37.0
    assert details1["mounting"] == "flexible"

    # Test extraction from name string and regex parsing
    twin2 = AssetDigitalTwin(
        tenant_id="t2",
        asset_id="siemens_fan_01",
        name="Siemens 1LE1 Exhaust Fan 45kW 2015",
        vertical="manufacturing",
        asset_class="fan",
    )
    details2 = extract_machine_details(twin2)
    assert details2["make"] == "Siemens"
    assert details2["model"] == "1LE1"
    assert details2["year"] == 2015
    assert details2["power_kw"] == 45.0


def test_apply_machine_age_adjustment():
    from src.services.threshold_provider import apply_machine_age_adjustment

    base = {
        "vibration_warning_mm_s": 4.5,
        "vibration_danger_mm_s": 7.1,
        "temp_warning_celsius": 80.0,
        "temp_danger_celsius": 95.0,
    }

    # 1. Brand new machine (e.g. 2025, 1 year old) -> commissioning tolerance factor 0.85
    new_m = apply_machine_age_adjustment(base, year_of_make=2025, current_year=2026)
    assert new_m["age_years"] == 1.0
    assert new_m["age_adjustment_factor"] == 0.85
    assert new_m["vibration_warning_mm_s"] == 3.82  # 4.5 * 0.85
    assert new_m["vibration_band_max"] == 3.82
    assert new_m["vibration_danger_mm_s"] == 7.1  # danger ceiling maintained

    # 2. Prime nominal service (e.g. 2020, 6 years old) -> factor 1.0
    prime_m = apply_machine_age_adjustment(base, year_of_make=2020, current_year=2026)
    assert prime_m["age_years"] == 6.0
    assert prime_m["age_adjustment_factor"] == 1.0
    assert prime_m["vibration_warning_mm_s"] == 4.5

    # 3. Mature aged service (e.g. 2012, 14 years old) -> +2% per yr over 10 (+8% -> 1.08)
    aged_m = apply_machine_age_adjustment(base, year_of_make=2012, current_year=2026)
    assert aged_m["age_years"] == 14.0
    assert aged_m["age_adjustment_factor"] == 1.08
    assert aged_m["vibration_warning_mm_s"] == 4.86  # 4.5 * 1.08
    assert aged_m["vibration_band_max"] == 4.86

    # 4. Vintage machine (e.g. 2000, 26 years old) -> factor capped at 1.30, thermal derating
    vintage_m = apply_machine_age_adjustment(base, year_of_make=2000, current_year=2026)
    assert vintage_m["age_years"] == 26.0
    assert vintage_m["age_adjustment_factor"] == 1.30  # capped at +30%
    assert vintage_m["vibration_warning_mm_s"] == 5.85  # 4.5 * 1.30
    assert vintage_m["temp_danger_celsius"] == 92.0  # 95 - (26 - 20) * 0.5


def test_oem_spec_catalog_and_make_resolution():
    from src.services.threshold_provider import resolve_machine_spec_thresholds

    # Kaeser Compressor
    twin_kaeser = AssetDigitalTwin(
        tenant_id="t1",
        asset_id="comp_kaeser",
        name="Kaeser BSD 75 Screw Compressor",
        vertical="industrial",
        asset_class="compressor",
        year_of_manufacture=2018,  # 8 years old -> nominal factor 1.0
    )
    kaeser_t = resolve_machine_spec_thresholds(twin_kaeser)
    assert kaeser_t["vibration_warning_mm_s"] == 4.0
    assert kaeser_t["vibration_danger_mm_s"] == 6.5
    assert kaeser_t["compressor_temp_warning_celsius"] == 90.0
    assert kaeser_t["compressor_temp_danger_celsius"] == 105.0

    # Propel Crusher
    twin_propel = AssetDigitalTwin(
        tenant_id="t2",
        asset_id="crusher_propel",
        name="Propel AV-4 Cone Crusher",
        vertical="heavy_machinery",
        asset_class="crusher",
        year_of_manufacture=2019,
    )
    propel_t = resolve_machine_spec_thresholds(twin_propel)
    assert propel_t["vibration_warning_mm_s"] == 7.1
    assert propel_t["vibration_danger_mm_s"] == 11.2


def test_internet_spec_mock_service(monkeypatch):
    from src.services.threshold_provider import (
        DynamicThresholdProvider,
        UpstreamFleetClient,
        resolve_machine_spec_thresholds,
    )
    import httpx

    # Test direct spec_url in metadata
    class MockResponse:
        status_code = 200
        def json(self):
            return {
                "vibration_warning_mm_s": 2.2,
                "vibration_danger_mm_s": 5.0,
                "temp_warning_celsius": 70.0,
                "temp_danger_celsius": 85.0,
            }

    def mock_get(url, *args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    twin_online = AssetDigitalTwin(
        tenant_id="t_online",
        asset_id="custom_turbo_chiller",
        name="Custom Centrifugal Chiller",
        vertical="hvac",
        asset_class="chiller",
        metadata={"spec_url": "https://specs.industrial-oem.com/models/chiller-700.json"},
    )

    t_online = resolve_machine_spec_thresholds(twin_online)
    assert t_online["vibration_warning_mm_s"] == 2.2
    assert t_online["vibration_danger_mm_s"] == 5.0
    assert t_online["vibration_band_max"] == 2.2


def test_end_to_end_make_and_year_threshold_provider():
    twin_aged = AssetDigitalTwin(
        tenant_id="t_mfg",
        asset_id="aged_compressor_01",
        name="Atlas Copco GA-37 2012 Screw Compressor",
        vertical="industrial",
        asset_class="compressor",
        year_of_manufacture=2012,
    )

    provider = DynamicThresholdProvider(client=UpstreamFleetClient(api_key=""))
    eff = provider.get_effective_thresholds(twin_aged)

    # 2026 - 2012 = 14 years old -> factor = 1.0 + (14-10)*0.02 = 1.08
    # 4.5 * 1.08 = 4.86 mm/s
    assert eff["vibration_warning_mm_s"] == 4.86
    assert eff["vibration_danger_mm_s"] == 7.1
    assert eff["compressor_temp_danger_celsius"] == 110.0
    assert eff["vibration_band_max"] == 4.86

    # Cache status checks
    cache_info = provider.get_cache_status("aged_compressor_01")
    assert cache_info is not None
    assert cache_info["source"] == "internet_machine_spec"
    assert cache_info["details"]["make"] == "Atlas Copco"
    assert cache_info["details"]["year"] == 2012
    assert cache_info["details"]["power_kw"] == 37.0


