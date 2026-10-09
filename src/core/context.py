"""
Multi-Tenant Context & Identity Enforcement
Ensures strict boundary isolation across disparate industrial clients.
"""

from typing import List, Optional, Tuple
from pydantic import BaseModel, Field


def make_tenant_key(tenant_id: str, asset_id: str) -> str:
    """Generates a globally unique compound key: tenant_id::asset_id."""
    clean_tenant = tenant_id.strip().lower()
    clean_asset = asset_id.strip()
    return f"{clean_tenant}::{clean_asset}"


def parse_tenant_key(compound_key: str) -> Tuple[str, str]:
    """Extracts (tenant_id, asset_id) from compound key."""
    parts = compound_key.split("::", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], ""


class TenantContext(BaseModel):
    """
    Immutable execution context passed into every Agent node.
    Enforces that diagnostics, thresholds, and work orders are scoped
    exclusively to the requesting client.
    """
    tenant_id: str = Field(..., description="Unique client ID, e.g., 'propel_industries' or 'nest_group'")
    client_name: str = Field(..., description="Human-readable enterprise name")
    vertical: str = Field(..., description="Industry vertical, e.g., 'heavy_machinery', 'electronics_smt'")
    asset_id: str = Field(..., description="Asset identifier within tenant")
    asset_class: str = Field(..., description="Asset classification, e.g., 'jaw_crusher', 'smt_pick_place'")
    capabilities: List[str] = Field(default_factory=list, description="Enabled diagnostic engines")

    @property
    def compound_key(self) -> str:
        return make_tenant_key(self.tenant_id, self.asset_id)
