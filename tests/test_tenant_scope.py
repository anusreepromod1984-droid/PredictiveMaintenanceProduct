"""Tenant-scoped platform machine resolution (reports, assistant, LangGraph)."""

from src.api.tenant_scope import resolve_scoped_machine


def test_resolve_scoped_machine_uses_registered_twin():
    t_id, asset_id, twin, _tp = resolve_scoped_machine("g-sensor-01", "greenbotz_platform")
    assert t_id == "greenbotz_platform"
    assert asset_id == "g-sensor-01"
    assert twin.metadata.get("fleet_machine_id") == "g-sensor-01"


def test_resolve_scoped_machine_ephemeral_for_platform_id():
    t_id, asset_id, twin, tp = resolve_scoped_machine("gbz-01", "greenbotz_platform")
    assert t_id == "greenbotz_platform"
    assert asset_id == "gbz-01"
    assert twin.metadata.get("platform_scoped") is True
    assert tp is not None
    assert twin.pipeline_config.get("diagnostic_engines")
