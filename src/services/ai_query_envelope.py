"""Normalize upstream PDM POST /api/ai/query JSON shapes."""

from __future__ import annotations

from typing import Any, Dict, Optional


def normalize_fleet_query_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Unwrap ``{ status, data: { machines, ... } }`` to inner ``data``.
    Legacy responses with top-level ``machines`` are returned unchanged.
    """
    if not payload or not isinstance(payload, dict):
        return payload
    if payload.get("status") is False:
        return None
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("machines"), list):
        return data
    return payload
