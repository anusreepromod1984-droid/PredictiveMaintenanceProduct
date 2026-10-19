from fastapi import APIRouter, HTTPException, Security
from typing import List, Dict, Any

from src.schemas.tenant import TenantProfile
from src.registry.asset_registry import asset_twin_registry
from src.middleware.auth import get_current_principal, assert_tenant_access, require_role

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.get("", response_model=List[TenantProfile])
def list_all_tenants(principal: Dict[str, Any] = Security(get_current_principal)):
    allowed = principal.get("tenant_id", "*")
    if allowed == "*":
        return asset_twin_registry.list_tenants()
    t = asset_twin_registry.get_tenant(allowed)
    return [t] if t else []


@router.post("", response_model=TenantProfile)
def create_tenant(
    tenant: TenantProfile,
    principal: Dict[str, Any] = Security(require_role("admin")),
):
    if principal.get("tenant_id") != "*":
        raise HTTPException(status_code=403, detail="Platform admin required to register tenants")
    existing = asset_twin_registry.get_tenant(tenant.tenant_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Tenant '{tenant.tenant_id}' already exists")
    return asset_twin_registry.register_tenant(tenant)


@router.get("/{tenant_id}", response_model=TenantProfile)
def get_tenant_by_id(
    tenant_id: str,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    t = asset_twin_registry.get_tenant(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return t
