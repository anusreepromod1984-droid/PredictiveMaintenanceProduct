from fastapi import APIRouter, HTTPException, Security
from typing import Dict, Any
from src.core.buffer import platform_buffer
from src.agents.dynamic_orchestrator import global_orchestrator
from src.middleware.auth import get_current_principal, assert_tenant_access

router = APIRouter(prefix="/tenants/{tenant_id}/assets/{asset_id}", tags=["Diagnostics"])


@router.get("/latest_diagnostic")
def get_latest_diagnostic(
    tenant_id: str,
    asset_id: str,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    diag = platform_buffer.get_latest_diagnostic(tenant_id, asset_id)
    if not diag:
        raise HTTPException(status_code=404, detail="No diagnostic runs on record for this asset")
    return diag


@router.post("/clear_order")
def clear_active_work_order(
    tenant_id: str,
    asset_id: str,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    cleared = global_orchestrator.delta.clear_work_order(tenant_id, asset_id)
    return {"cleared": cleared, "tenant_id": tenant_id, "asset_id": asset_id}
