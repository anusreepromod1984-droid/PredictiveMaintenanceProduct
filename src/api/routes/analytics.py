"""
Production Analytics & Historian Query Routes
==============================================
Provides time-series queries, KPI trend analysis, and SBM model status.
"""

from fastapi import APIRouter, HTTPException, Query, Security
from typing import Optional, Dict, Any

from src.db.historian import platform_historian
from src.engines.sbm_engine import sbm_registry
from src.registry.asset_registry import asset_twin_registry
from src.utils.metrics import platform_metrics
from src.middleware.auth import get_current_principal, assert_tenant_access, require_role

router = APIRouter(tags=["Production Analytics"])


@router.get("/tenants/{tenant_id}/assets/{asset_id}/telemetry/history")
def get_historical_telemetry(
    tenant_id: str,
    asset_id: str,
    limit: int = Query(default=200, le=5000),
    since_epoch: Optional[float] = None,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    twin = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not twin:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found for tenant '{tenant_id}'")
    rows = platform_historian.get_telemetry_tail(tenant_id, asset_id, limit=limit, since_epoch=since_epoch)
    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "count": len(rows),
        "source": "persistent_historian",
        "telemetry": rows,
    }


@router.get("/tenants/{tenant_id}/assets/{asset_id}/diagnostics/history")
def get_diagnostic_history(
    tenant_id: str,
    asset_id: str,
    limit: int = Query(default=100, le=2000),
    since_epoch: Optional[float] = None,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    twin = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not twin:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found for tenant '{tenant_id}'")
    history = platform_historian.get_diagnostic_history(tenant_id, asset_id, limit=limit, since_epoch=since_epoch)
    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "count": len(history),
        "history": history,
    }


@router.get("/tenants/{tenant_id}/assets/{asset_id}/kpi/summary")
def get_asset_kpi_summary(
    tenant_id: str,
    asset_id: str,
    days: int = Query(default=30, le=365),
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    twin = asset_twin_registry.get_asset(tenant_id, asset_id)
    if not twin:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found for tenant '{tenant_id}'")
    kpi = platform_historian.get_kpi_summary(tenant_id, asset_id, days=days)
    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "asset_name": twin.name,
        "days_requested": days,
        "kpi_by_day": kpi,
    }


@router.get("/tenants/{tenant_id}/assets/{asset_id}/sbm/status")
def get_sbm_status(
    tenant_id: str,
    asset_id: str,
    principal: Dict[str, Any] = Security(get_current_principal),
):
    assert_tenant_access(principal, tenant_id)
    model = sbm_registry.get(tenant_id, asset_id)
    if not model:
        return {
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "sbm_status": "NOT_INITIALIZED",
            "message": "Model will auto-initialize on first telemetry packet.",
        }
    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "is_trained": model.is_trained,
        "n_samples_buffered": model.n_training_samples_buffered,
        "n_reference_samples": model._n_reference_samples if hasattr(model, "_n_reference_samples") else model.n_reference_samples,
        "signal_keys": model._signal_keys,
        "cusum_state": {
            k: {
                "cusum_pos": round(model._cusum_pos.get(k, 0.0), 3),
                "cusum_neg": round(model._cusum_neg.get(k, 0.0), 3),
                "alarm": model._cusum_alarm.get(k, False),
            }
            for k in model._signal_keys
        } if model.is_trained else {},
    }


@router.post("/tenants/{tenant_id}/assets/{asset_id}/sbm/reset-cusum")
def reset_sbm_cusum(
    tenant_id: str,
    asset_id: str,
    principal: Dict[str, Any] = Security(require_role("admin")),
):
    assert_tenant_access(principal, tenant_id)
    sbm_registry.reset_cusum(tenant_id, asset_id)
    return {"status": "ok", "message": f"CUSUM state reset for {tenant_id}::{asset_id}"}


@router.get("/platform/metrics/summary")
def platform_metrics_summary(principal: Dict[str, Any] = Security(require_role("admin"))):
    """JSON summary of platform operational metrics (alternative to Prometheus /metrics)."""
    return platform_metrics.get_summary()
