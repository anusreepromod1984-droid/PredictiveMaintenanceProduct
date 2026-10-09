from fastapi import APIRouter, HTTPException
from typing import List
from src.schemas.tenant import TenantProfile
from src.registry.asset_registry import asset_twin_registry

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.get("", response_model=List[TenantProfile])
def list_all_tenants():
    return asset_twin_registry.list_tenants()


@router.post("", response_model=TenantProfile)
def create_tenant(tenant: TenantProfile):
    existing = asset_twin_registry.get_tenant(tenant.tenant_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Tenant '{tenant.tenant_id}' already exists")
    return asset_twin_registry.register_tenant(tenant)


@router.get("/{tenant_id}", response_model=TenantProfile)
def get_tenant_by_id(tenant_id: str):
    t = asset_twin_registry.get_tenant(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return t
