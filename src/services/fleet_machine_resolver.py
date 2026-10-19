"""
Map upstream fleet machineId (dashboard / POST /ai/query) to agent (tenant_id, asset_id).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.registry.asset_registry import asset_twin_registry
from src.schemas.asset import AssetDigitalTwin
from src.schemas.tenant import TenantProfile


def fleet_machine_id_from_metadata(meta: Dict[str, Any]) -> Optional[str]:
    if not meta:
        return None
    for key in ("fleet_machine_id", "upstream_machine_id", "machineId", "machine_id"):
        val = meta.get(key)
        if val:
            return str(val).strip().lower()
    return None


_fleet_id_from_metadata = fleet_machine_id_from_metadata


def resolve_fleet_machine(
    machine_id: str,
    tenant_hint: Optional[str] = None,
) -> Optional[Tuple[str, str, AssetDigitalTwin, Optional[TenantProfile]]]:
    """
    Returns (tenant_id, asset_id, twin, tenant_profile) or None.
    Returns None when multiple tenants share the same fleet machine id (ambiguous).
    """
    want = machine_id.strip().lower()
    tenant_hint_l = tenant_hint.strip().lower() if tenant_hint else None

    if tenant_hint_l:
        twin = asset_twin_registry.get_asset(tenant_hint_l, machine_id)
        if twin:
            return tenant_hint_l, twin.asset_id, twin, asset_twin_registry.get_tenant(tenant_hint_l)

    matches: list[Tuple[str, str, AssetDigitalTwin, Optional[TenantProfile]]] = []
    candidates = (
        asset_twin_registry.list_assets_by_tenant(tenant_hint_l)
        if tenant_hint_l
        else _all_assets()
    )
    for twin in candidates:
        if twin.asset_id.lower() == want:
            t = asset_twin_registry.get_tenant(twin.tenant_id)
            matches.append((twin.tenant_id, twin.asset_id, twin, t))
            continue
        meta_id = fleet_machine_id_from_metadata(twin.metadata)
        if meta_id == want:
            t = asset_twin_registry.get_tenant(twin.tenant_id)
            matches.append((twin.tenant_id, twin.asset_id, twin, t))

    if not matches:
        return None
    if len(matches) > 1:
        tenants = sorted({m[0] for m in matches})
        if tenant_hint_l:
            filtered = [m for m in matches if m[0].lower() == tenant_hint_l]
            if len(filtered) == 1:
                return filtered[0]
        return None
    return matches[0]


def list_registered_fleet_machine_ids(tenant_id: Optional[str] = None) -> list[str]:
    """Distinct fleet/upstream machine ids known to the agent registry."""
    ids: set[str] = set()
    if tenant_id:
        twins = asset_twin_registry.list_assets_by_tenant(tenant_id)
    else:
        twins = _all_assets()
    for twin in twins:
        fid = fleet_machine_id_from_metadata(twin.metadata)
        ids.add((fid or twin.asset_id).lower())
    return sorted(ids)


def _all_assets():
    assets = []
    for t in asset_twin_registry.list_tenants():
        assets.extend(asset_twin_registry.list_assets_by_tenant(t.tenant_id))
    return assets
