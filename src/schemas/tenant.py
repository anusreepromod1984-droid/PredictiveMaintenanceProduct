"""
Tenant Definition & Settings Contracts
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class CMMSConfig(BaseModel):
    integration_type: str = Field(
        default="webhook",
        description="'sap_pm', 'ibm_maximo', 'webhook', 'email', 'sms'"
    )
    endpoint_url: Optional[str] = None
    api_key: Optional[str] = None
    notification_email: Optional[str] = None
    phone_number: Optional[str] = None
    auto_create_work_orders: bool = True


class TenantProfile(BaseModel):
    tenant_id: str = Field(..., description="Unique slug, e.g. 'propel_industries'")
    name: str = Field(..., description="Display name, e.g. 'Propel Industries Pvt Ltd'")
    vertical: str = Field(..., description="'heavy_machinery', 'electronics_smt', 'cleanroom', 'general_manufacturing'")
    active: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cmms_config: CMMSConfig = Field(default_factory=CMMSConfig)
    metadata: Dict[str, Any] = Field(default_factory=dict)
