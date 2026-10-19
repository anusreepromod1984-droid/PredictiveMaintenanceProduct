"""
Resolve (tenant_id, asset_id) for dashboard machine ids with strict multi-tenant rules.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from src.middleware.auth import assert_tenant_access
from src.services.fleet_machine_resolver import resolve_fleet_machine


def resolve_machine_asset(
    machine_id: str,
    tenant_id: Optional[str],
    principal: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Returns (tenant_id, asset_id) for historian/buffer lookups.
    Raises HTTP 400/403/409 when scope cannot be determined safely.
    """
    mid = machine_id.strip()
    hint = tenant_id.strip() if tenant_id else None
    principal_tenant = principal.get("tenant_id")

    if hint:
        assert_tenant_access(principal, hint)
        resolved = resolve_fleet_machine(mid, hint)
        if resolved:
            return resolved[0], resolved[1]
        return hint.lower(), mid

    if principal_tenant and principal_tenant != "*":
        assert_tenant_access(principal, principal_tenant)
        resolved = resolve_fleet_machine(mid, principal_tenant)
        if resolved:
            return resolved[0], resolved[1]
        return principal_tenant.lower(), mid

    resolved = resolve_fleet_machine(mid, None)
    if resolved:
        assert_tenant_access(principal, resolved[0])
        return resolved[0], resolved[1]

    raise HTTPException(
        status_code=400,
        detail="tenant_id is required (query, X-Tenant-Id header, or JWT claim) for this machine",
    )


def fleet_ids_for_tenant(tenant_id: str) -> set[str]:
    """Upstream-facing machine ids registered under a tenant."""
    from src.registry.asset_registry import asset_twin_registry
    from src.services.fleet_machine_resolver import fleet_machine_id_from_metadata

    out: set[str] = set()
    for twin in asset_twin_registry.list_assets_by_tenant(tenant_id):
        fid = fleet_machine_id_from_metadata(twin.metadata)
        out.add((fid or twin.asset_id).lower())
    return out
