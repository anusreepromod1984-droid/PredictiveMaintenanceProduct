"""
Multi-Tenant Ring Buffer & Event Historian (v3.0 Production)
=============================================================
L1 hot-tier in-memory cache + write-through to persistent historian.

Architecture:
  - In-memory deque acts as L1 cache for ultra-fast agent lookups (recent N samples)
  - Every write also persists to IndustrialHistorian (SQLite → TimescaleDB in production)
  - Reads first try the in-memory buffer; fall back to historian for longer history
"""

import threading
from collections import deque
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src.core.context import make_tenant_key, parse_tenant_key
from src.utils.logger import get_logger

logger = get_logger("Core.Buffer")


class MultiTenantRingBuffer:
    """
    In-memory isolated ring buffer with write-through to the persistent historian.
    Data is indexed by compound key (tenant_id::asset_id).
    One tenant cannot view or mutate another tenant's telemetry or alerts.
    """
    def __init__(self, capacity_per_asset: int = 500):
        self.capacity = capacity_per_asset
        self._buffers: Dict[str, deque] = {}
        self._latest_diagnostics: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def push_telemetry(self, tenant_id: str, asset_id: str, packet: Dict[str, Any]) -> None:
        key = make_tenant_key(tenant_id, asset_id)
        with self._lock:
            if key not in self._buffers:
                self._buffers[key] = deque(maxlen=self.capacity)

            # Enrich packet with server ingestion timestamp if missing
            if "received_at" not in packet:
                packet["received_at"] = datetime.now(timezone.utc).isoformat()

            self._buffers[key].append(packet)

        # Write-through to persistent historian (non-blocking attempt)
        try:
            from src.db.historian import platform_historian
            platform_historian.persist_telemetry(tenant_id, asset_id, packet)
        except Exception as e:
            logger.warning(f"Historian write-through failed for {key}: {e}. Hot buffer intact.")

    def get_telemetry_tail(self, tenant_id: str, asset_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        key = make_tenant_key(tenant_id, asset_id)
        with self._lock:
            buf = self._buffers.get(key)
            if not buf:
                return []
            items = list(buf)
            return items[-limit:]

    def save_diagnostic(self, tenant_id: str, asset_id: str, diagnostic: Dict[str, Any]) -> None:
        key = make_tenant_key(tenant_id, asset_id)
        with self._lock:
            self._latest_diagnostics[key] = diagnostic

        # Write-through to historian
        try:
            from src.db.historian import platform_historian
            platform_historian.persist_diagnostic(tenant_id, asset_id, diagnostic)
        except Exception as e:
            logger.warning(f"Historian diagnostic write-through failed for {key}: {e}.")

    def get_latest_diagnostic(self, tenant_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
        key = make_tenant_key(tenant_id, asset_id)
        with self._lock:
            result = self._latest_diagnostics.get(key)
        # Fallback to historian if not in hot cache
        if result is None:
            try:
                from src.db.historian import platform_historian
                result = platform_historian.get_latest_diagnostic(tenant_id, asset_id)
            except Exception:
                pass
        return result

    def list_assets_for_tenant(self, tenant_id: str) -> List[str]:
        target = tenant_id.strip().lower()
        with self._lock:
            assets = []
            for k in self._buffers.keys():
                t_id, a_id = parse_tenant_key(k)
                if t_id == target:
                    assets.append(a_id)
            return sorted(list(set(assets)))

    def clear_for_tenant(self, tenant_id: str, asset_id: Optional[str] = None) -> int:
        target = tenant_id.strip().lower()
        cleared = 0
        with self._lock:
            if asset_id:
                key = make_tenant_key(target, asset_id)
                if key in self._buffers:
                    del self._buffers[key]
                    cleared += 1
                if key in self._latest_diagnostics:
                    del self._latest_diagnostics[key]
            else:
                keys_to_del = [k for k in self._buffers.keys() if parse_tenant_key(k)[0] == target]
                for k in keys_to_del:
                    del self._buffers[k]
                    cleared += 1
                diag_keys_to_del = [k for k in self._latest_diagnostics.keys() if parse_tenant_key(k)[0] == target]
                for k in diag_keys_to_del:
                    del self._latest_diagnostics[k]
        return cleared


# Singleton buffer instance for the platform
platform_buffer = MultiTenantRingBuffer()
