"""
Map upstream fleet machineId (dashboard / POST /ai/query) to agent (tenant_id, asset_id).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.registry.asset_registry import asset_twin_registry
from src.schemas.asset import AssetDigitalTwin
from src.schemas.tenant import TenantProfile


def _fleet_id_from_metadata(meta: Dict[str, Any]) -> Optional[str]:
    if not meta:
        return None
    for key in ("fleet_machine_id", "upstream_machine_id", "machineId", "machine_id"):
        val = meta.get(key)
        if val:
            return str(val).strip().lower()
    return None


def resolve_fleet_machine(
    machine_id: str,
    tenant_hint: Optional[str] = None,
) -> Optional[Tuple[str, str, AssetDigitalTwin, Optional[TenantProfile]]]:
    """
    Returns (tenant_id, asset_id, twin, tenant_profile) or None.
    """
    want = machine_id.strip().lower()
    tenant_hint_l = tenant_hint.strip().lower() if tenant_hint else None

    if tenant_hint_l:
        twin = asset_twin_registry.get_asset(tenant_hint_l, machine_id)
        if twin:
            return tenant_hint_l, twin.asset_id, twin, asset_twin_registry.get_tenant(tenant_hint_l)

    for twin in asset_twin_registry.list_assets_by_tenant(tenant_hint_l) if tenant_hint_l else _all_assets():
        if twin.asset_id.lower() == want:
            t = asset_twin_registry.get_tenant(twin.tenant_id)
            return twin.tenant_id, twin.asset_id, twin, t
        meta_id = _fleet_id_from_metadata(twin.metadata)
        if meta_id == want:
            t = asset_twin_registry.get_tenant(twin.tenant_id)
            return twin.tenant_id, twin.asset_id, twin, t

    return None


def _all_assets():
    assets = []
    for t in asset_twin_registry.list_tenants():
        assets.extend(asset_twin_registry.list_assets_by_tenant(t.tenant_id))
    return assets
