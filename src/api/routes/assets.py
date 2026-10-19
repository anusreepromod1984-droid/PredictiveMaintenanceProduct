from fastapi import APIRouter, HTTPException, Security
from typing import List, Dict, Any
from src.schemas.asset import AssetDigitalTwin
from src.registry.asset_registry import asset_twin_registry
from src.middleware.auth import get_current_principal, assert_tenant_access

router = APIRouter(prefix="/tenants/{tenant_id}/assets", tags=["Assets"])


@router.get("", response_model=List[AssetDigitalTwin])
def list_tenant_assets(tenant_id: str, principal: Dict[str, Any] = Security(get_current_principal)):
    assert_tenant_access(principal, tenant_id)
    tenant = asset_twin_registry.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return asset_twin_registry.list_assets_by_tenant(tenant_id)


@router.post("", response_model=AssetDigitalTwin)
def create_or_update_asset(
    tenant_id: str,
    asset: AssetDigitalTwin,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    if asset.tenant_id.lower() != tenant_id.lower():
        raise HTTPException(status_code=400, detail="Tenant ID in payload does not match route parameter")
    try:
        return asset_twin_registry.register_asset(asset)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{asset_id}", response_model=AssetDigitalTwin)
def get_asset(tenant_id: str, asset_id: str, principal: Dict[str, Any] = Security(get_current_principal)):
    assert_tenant_access(principal, tenant_id)
    a = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' for tenant '{tenant_id}' not found")
    return a


@router.delete("/{asset_id}")
def delete_asset(tenant_id: str, asset_id: str, principal: Dict[str, Any] = Security(get_current_principal)):
    assert_tenant_access(principal, tenant_id)
    success = asset_twin_registry.delete_asset(tenant_id, asset_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found")
    return {"status": "DELETED", "asset_id": asset_id, "tenant_id": tenant_id}


@router.get("/{asset_id}/thresholds")
def get_asset_thresholds(tenant_id: str, asset_id: str, principal: Dict[str, Any] = Security(get_current_principal)):
    """
    Returns live effective thresholds for the asset twin.
    Fetches dynamic thresholds from upstream Fleet PDM API if available,
    falling back seamlessly to local twin baseline if unavailable or not configured.
    """
    assert_tenant_access(principal, tenant_id)
    a = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' for tenant '{tenant_id}' not found")

    from src.services.threshold_provider import threshold_provider
    effective = threshold_provider.get_effective_thresholds(a)
    cache_info = threshold_provider.get_cache_status(a.asset_id) or {}

    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "effective_thresholds": effective,
        "source": cache_info.get("source", "local_fallback"),
        "details": cache_info.get("details"),
        "cached_at": cache_info.get("ts"),
        "local_baseline_thresholds": a.thresholds
    }

