"""
Map upstream fleet machineId (dashboard / POST /ai/query) to agent (tenant_id, asset_id).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set, Tuple

from src.config import settings
from src.registry.asset_registry import asset_twin_registry
from src.schemas.asset import AssetDigitalTwin
from src.schemas.tenant import TenantProfile


def fleet_machine_id_from_metadata(meta: Dict[str, Any]) -> Optional[str]:
    ids = fleet_machine_ids_from_metadata(meta)
    return next(iter(ids), None) if ids else None


def fleet_machine_ids_from_metadata(meta: Dict[str, Any]) -> Set[str]:
    """All platform/upstream ids that should resolve to this twin."""
    out: Set[str] = set()
    if not meta:
        return out
    for key in ("fleet_machine_id", "upstream_machine_id", "machineId", "machine_id"):
        val = meta.get(key)
        if val:
            out.add(str(val).strip().lower())
    for key in ("fleet_machine_aliases", "machine_aliases", "upstream_machine_ids"):
        val = meta.get(key)
        if isinstance(val, list):
            for item in val:
                if item:
                    out.add(str(item).strip().lower())
        elif isinstance(val, str) and val.strip():
            out.add(val.strip().lower())
    return out


_fleet_id_from_metadata = fleet_machine_id_from_metadata


def _env_fleet_alias_map() -> Dict[str, str]:
    """
    JSON map tenant_id:platform_machine_id -> asset_id (all keys lowercased).
    Example: {"greenbotz_platform:gbz-01": "g-sensor-01"}
    """
    raw = (getattr(settings, "FLEET_MACHINE_ALIAS_MAP", None) or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in parsed.items():
        if not k or not v:
            continue
        out[str(k).strip().lower()] = str(v).strip().lower()
    return out


def _resolve_env_alias(tenant_id: str, machine_id: str) -> Optional[Tuple[str, str, AssetDigitalTwin, Optional[TenantProfile]]]:
    key = f"{tenant_id.strip().lower()}:{machine_id.strip().lower()}"
    asset_id = _env_fleet_alias_map().get(key)
    if not asset_id:
        return None
    twin = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not twin:
        return None
    return tenant_id.strip().lower(), twin.asset_id, twin, asset_twin_registry.get_tenant(tenant_id)


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
        env_hit = _resolve_env_alias(tenant_hint_l, want)
        if env_hit:
            return env_hit
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
        meta_ids = fleet_machine_ids_from_metadata(twin.metadata or {})
        if want in meta_ids:
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
