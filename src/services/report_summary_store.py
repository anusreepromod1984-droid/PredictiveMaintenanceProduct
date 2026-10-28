"""
Latest plant report AI summaries per (tenant_id, machine_id) for fault-assistant.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from src.core.context import make_tenant_key, parse_tenant_key

_lock = threading.RLock()
_by_key: Dict[str, Dict[str, Any]] = {}


def save_machine_report_summary(
    tenant_id: str,
    machine_id: str,
    *,
    summary: str,
    findings: List[str],
    title: Optional[str] = None,
    report_type: Optional[str] = None,
    company: Optional[str] = None,
    period: Optional[Dict[str, Any]] = None,
    graph: Optional[Dict[str, Any]] = None,
) -> None:
    key = make_tenant_key(tenant_id, machine_id.strip().lower())
    record = {
        "summary": summary,
        "findings": findings,
        "title": title,
        "reportType": report_type,
        "company": company,
        "machineId": machine_id,
        "tenant_id": tenant_id,
        "period": period,
        "generatedAt": int(time.time() * 1000),
        "graph": graph or {},
    }
    with _lock:
        _by_key[key] = record


def get_latest_for_machine(tenant_id: str, machine_id: str) -> Optional[Dict[str, Any]]:
    key = make_tenant_key(tenant_id, machine_id.strip().lower())
    with _lock:
        rec = _by_key.get(key)
        return dict(rec) if rec else None


def get_latest_for_asset(tenant_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """Lookup by asset id if stored under fleet machine id alias."""
    want = asset_id.strip().lower()
    with _lock:
        for k, rec in _by_key.items():
            t, mid = parse_tenant_key(k)
            if t.lower() != tenant_id.lower():
                continue
            if mid == want or str(rec.get("machineId", "")).lower() == want:
                return dict(rec)
    return None
