"""
Resolve (tenant_id, asset_id) for dashboard machine ids with strict multi-tenant rules.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from src.middleware.auth import assert_tenant_access
from src.registry.asset_registry import asset_twin_registry
from src.schemas.asset import AssetDigitalTwin
from src.schemas.tenant import TenantProfile
from src.services.fleet_machine_resolver import resolve_fleet_machine
from src.utils.logger import get_logger

logger = get_logger("API.TenantScope")


def resolve_scoped_machine(
    machine_id: str,
    tenant_id: str,
) -> Tuple[str, str, AssetDigitalTwin, Optional[TenantProfile]]:
    """
    Map a dashboard / platform machineId to tenant + twin for LangGraph (Alpha→Delta).

    Registered twins win (signal_map, thresholds). Otherwise build a tenant-scoped
    ephemeral twin so live fleet telemetry can still run the diagnostic pipeline.
    """
    mid = machine_id.strip()
    if not mid:
        raise HTTPException(status_code=400, detail="machine_id is required")
    want_tenant = tenant_id.strip().lower()

    tenant_profile = asset_twin_registry.get_tenant(want_tenant)
    resolved = resolve_fleet_machine(mid, want_tenant)
    if resolved:
        t_id, asset_id, twin, tp = resolved
        if t_id.lower() != want_tenant:
            raise HTTPException(
                status_code=403,
                detail=f"Machine '{mid}' belongs to another tenant",
            )
        return t_id, asset_id, twin, tp or tenant_profile

    vertical = tenant_profile.vertical if tenant_profile else "heavy_machinery"
    twin = AssetDigitalTwin(
        tenant_id=want_tenant,
        asset_id=mid,
        name=mid,
        asset_class="industrial_asset",
        vertical=vertical,
        metadata={
            "fleet_machine_id": mid.lower(),
            "upstream_machine_id": mid,
            "platform_scoped": True,
        },
        signal_map={},
        thresholds={},
        pipeline_config={
            "quality_strategy": "digital_range_check",
            "regime_detection": "load_threshold",
            "diagnostic_engines": ["iso_vibration", "weibull_rul"],
        },
    )
    logger.info(f"Platform-scoped machine '{mid}' under tenant '{want_tenant}' (ephemeral twin)")
    return want_tenant, mid, twin, tenant_profile


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
        t_id, asset_id, _, _ = resolve_scoped_machine(mid, hint)
        return t_id, asset_id

    if principal_tenant and principal_tenant != "*":
        assert_tenant_access(principal, principal_tenant)
        t_id, asset_id, _, _ = resolve_scoped_machine(mid, principal_tenant)
        return t_id, asset_id

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
    from src.services.fleet_machine_resolver import fleet_machine_ids_from_metadata

    out: set[str] = set()
    for twin in asset_twin_registry.list_assets_by_tenant(tenant_id):
        meta = twin.metadata or {}
        ids = fleet_machine_ids_from_metadata(meta)
        if ids:
            out.update(ids)
        out.add(twin.asset_id.lower())
    return out


def validate_tenant_scoped_machine_ids(tenant_id: str, machine_ids: list[str]) -> None:
    """
    Ensure machine ids are non-empty and consistent with tenant scope.

    Plant reports arrive from the platform already scoped to the logged-in company
    (companySlug → tenant_id) with machineIds from that org's asset register. When no
    agent twin is pre-registered, accept the platform machine id under that tenant —
    same rule as resolve_machine_asset for historian/fault-assistant paths.
    """
    if not machine_ids:
        raise HTTPException(status_code=400, detail="machineIds must be a non-empty list")
    want_tenant = tenant_id.strip().lower()
    for mid in machine_ids:
        if not mid or not str(mid).strip():
            raise HTTPException(status_code=400, detail="machineIds must not contain empty entries")
        resolved = resolve_fleet_machine(mid, want_tenant)
        if resolved:
            t_id, _, _, _ = resolved
            if t_id.lower() != want_tenant:
                raise HTTPException(
                    status_code=403,
                    detail=f"Machine '{mid}' belongs to another tenant",
                )
            continue
        logger.info(
            f"Tenant-scoped platform machine '{mid}' (no agent twin) accepted for tenant '{want_tenant}'"
        )
