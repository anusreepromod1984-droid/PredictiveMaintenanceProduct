"""
Dynamic In-Memory & Persistent Asset Digital Twin Repository
Provides thread-safe registration, updates, and lookups for multi-tenant assets.
"""

import threading
from typing import Dict, List, Optional
from src.core.context import make_tenant_key
from src.schemas.tenant import TenantProfile
from src.schemas.asset import AssetDigitalTwin
from src.registry.preloaded_assets import PRELOADED_TENANTS, PRELOADED_ASSETS
from src.utils.logger import get_logger

logger = get_logger("Registry.Asset")


class AssetTwinRegistry:
    def __init__(self):
        self._tenants: Dict[str, TenantProfile] = {}
        self._assets: Dict[str, AssetDigitalTwin] = {}
        self._lock = threading.RLock()
        self._seed_defaults()

    def _seed_defaults(self):
        with self._lock:
            for t in PRELOADED_TENANTS:
                self._tenants[t.tenant_id.lower()] = t
            for a in PRELOADED_ASSETS:
                k = make_tenant_key(a.tenant_id, a.asset_id)
                self._assets[k] = a
        logger.info(f"Initialized registry with {len(self._tenants)} tenants and {len(self._assets)} preloaded assets")

    # Tenant CRUD
    def register_tenant(self, tenant: TenantProfile) -> TenantProfile:
        with self._lock:
            self._tenants[tenant.tenant_id.lower()] = tenant
            return tenant

    def get_tenant(self, tenant_id: str) -> Optional[TenantProfile]:
        with self._lock:
            return self._tenants.get(tenant_id.strip().lower())

    def list_tenants(self) -> List[TenantProfile]:
        with self._lock:
            return list(self._tenants.values())

    # Asset CRUD
    def register_asset(self, asset: AssetDigitalTwin) -> AssetDigitalTwin:
        from src.services.fleet_machine_resolver import fleet_machine_id_from_metadata

        key = make_tenant_key(asset.tenant_id, asset.asset_id)
        fid = fleet_machine_id_from_metadata(asset.metadata or {})
        with self._lock:
            if fid:
                for existing in self._assets.values():
                    if existing.tenant_id.lower() == asset.tenant_id.lower() and existing.asset_id.lower() == asset.asset_id.lower():
                        continue
                    other_fid = fleet_machine_id_from_metadata(existing.metadata or {})
                    if other_fid == fid:
                        raise ValueError(
                            f"fleet_machine_id '{fid}' already registered for tenant "
                            f"{existing.tenant_id} asset {existing.asset_id}"
                        )
            self._assets[key] = asset
            logger.info(f"Registered asset twin: {key} ({asset.name})")
            return asset

    def get_asset(self, tenant_id: str, asset_id: str) -> Optional[AssetDigitalTwin]:
        key = make_tenant_key(tenant_id, asset_id)
        with self._lock:
            return self._assets.get(key)

    def list_assets_by_tenant(self, tenant_id: str) -> List[AssetDigitalTwin]:
        target = tenant_id.strip().lower()
        with self._lock:
            return [a for a in self._assets.values() if a.tenant_id.lower() == target]

    def delete_asset(self, tenant_id: str, asset_id: str) -> bool:
        key = make_tenant_key(tenant_id, asset_id)
        with self._lock:
            if key in self._assets:
                del self._assets[key]
                return True
            return False

    def resolve_fleet_machine(self, machine_id: str, tenant_hint: Optional[str] = None):
        from src.services.fleet_machine_resolver import resolve_fleet_machine

        return resolve_fleet_machine(machine_id, tenant_hint)


# Global singleton repository
asset_twin_registry = AssetTwinRegistry()
