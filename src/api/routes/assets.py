from fastapi import APIRouter, HTTPException
from typing import List
from src.schemas.asset import AssetDigitalTwin
from src.registry.asset_registry import asset_twin_registry

router = APIRouter(prefix="/tenants/{tenant_id}/assets", tags=["Assets"])


@router.get("", response_model=List[AssetDigitalTwin])
def list_tenant_assets(tenant_id: str):
    tenant = asset_twin_registry.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return asset_twin_registry.list_assets_by_tenant(tenant_id)


@router.post("", response_model=AssetDigitalTwin)
def create_or_update_asset(tenant_id: str, asset: AssetDigitalTwin):
    if asset.tenant_id.lower() != tenant_id.lower():
        raise HTTPException(status_code=400, detail="Tenant ID in payload does not match route parameter")
    return asset_twin_registry.register_asset(asset)


@router.get("/{asset_id}", response_model=AssetDigitalTwin)
def get_asset(tenant_id: str, asset_id: str):
    a = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' for tenant '{tenant_id}' not found")
    return a


@router.delete("/{asset_id}")
def delete_asset(tenant_id: str, asset_id: str):
    success = asset_twin_registry.delete_asset(tenant_id, asset_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found")
    return {"status": "DELETED", "asset_id": asset_id, "tenant_id": tenant_id}
