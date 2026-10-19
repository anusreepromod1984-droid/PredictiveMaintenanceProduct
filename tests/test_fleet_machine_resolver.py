"""Fleet machineId → tenant/asset resolution."""

from src.registry.asset_registry import asset_twin_registry
from src.services.fleet_machine_resolver import resolve_fleet_machine


def test_resolve_by_asset_id():
    r = resolve_fleet_machine("g-sensor-01")
    assert r is not None
    tenant_id, asset_id, twin, _tp = r
    assert tenant_id == "greenbotz_platform"
    assert asset_id == "g-sensor-01"
    assert twin.metadata.get("fleet_machine_id") == "g-sensor-01"


def test_resolve_with_tenant_hint():
    r = resolve_fleet_machine("gbz-rx-1", "propel_industries")
    assert r is not None
    assert r[0] == "propel_industries"
    assert r[1] == "gbz-rx-1"


def test_resolve_unknown_returns_none():
    assert resolve_fleet_machine("no-such-machine-xyz") is None


def test_registry_delegate():
    r = asset_twin_registry.resolve_fleet_machine("g-sensor-01")
    assert r is not None
    assert r[1] == "g-sensor-01"
