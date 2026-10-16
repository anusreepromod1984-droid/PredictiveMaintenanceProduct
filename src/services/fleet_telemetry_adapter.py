"""
Convert fleet /ai/query latest & parameters into IngestTelemetryPacket.signals.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _get_nested(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.replace("/", ".").split("."):
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def flatten_numeric_leaves(obj: Any, prefix: str = "") -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten_numeric_leaves(v, key))
            else:
                n = _num(v)
                if n is not None:
                    out[key] = n
                    out[str(k)] = n
    return out


def latest_row_from_machine_payload(machine: Dict[str, Any]) -> Dict[str, Any]:
    latest = machine.get("latest")
    if isinstance(latest, dict):
        return latest
    params = machine.get("parameters")
    if isinstance(params, dict):
        return params
    return {}


def fleet_row_to_signals(row: Dict[str, Any], twin: Optional[AssetDigitalTwin] = None) -> Dict[str, float]:
    flat = flatten_numeric_leaves(row)
    signals: Dict[str, float] = {}

    if twin:
        for _role, client_key in twin.signal_map.items():
            if client_key in flat:
                signals[client_key] = flat[client_key]
                continue
            nested = _get_nested(row, client_key)
            n = _num(nested)
            if n is not None:
                signals[client_key] = n

    for k, v in flat.items():
        if k not in signals.values() and k not in signals:
            signals[k.replace(".", "_")] = v

    return signals


def build_ingest_packet(
    tenant_id: str,
    asset_id: str,
    row: Dict[str, Any],
    twin: Optional[AssetDigitalTwin] = None,
) -> IngestTelemetryPacket:
    ts = row.get("timestamp")
    if isinstance(ts, (int, float)):
        epoch = float(ts) / 1000.0 if ts > 1e12 else float(ts)
    else:
        epoch = time.time()

    signals = fleet_row_to_signals(row, twin)
    return IngestTelemetryPacket(
        tenant_id=tenant_id,
        asset_id=asset_id,
        timestamp=epoch,
        signals=signals,
    )


def alerts_to_breaches(alerts: Any) -> List[Dict[str, Any]]:
    if not isinstance(alerts, list):
        return []
    breaches: List[Dict[str, Any]] = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        breaches.append({
            "paramName": a.get("paramName") or a.get("parameter") or a.get("key") or "alert",
            "value": a.get("value"),
            "threshold": a.get("threshold"),
            "unit": a.get("unit"),
            "direction": a.get("direction") or "high",
        })
    return breaches
