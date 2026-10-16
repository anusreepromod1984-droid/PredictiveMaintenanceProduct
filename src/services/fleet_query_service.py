"""
Upstream Fleet PDM — GET /ai/query/capabilities + POST /ai/query
================================================================
Resolves MQTT broker/topic details and live or stored telemetry/history
for machines visible to the configured API key.

Typical flow:
  1. GET capabilities → allowed includes + machine ids
  2. POST query with include: ["mqtt", "topics", "latest", "history", ...]
  3. Connect MQTT from returned broker fields; password stays in env if hasPassword=true
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.threshold_provider import UpstreamFleetClient
from src.utils.logger import get_logger

logger = get_logger("Services.FleetQuery")

DEFAULT_SNAPSHOT_INCLUDES = [
    "meta",
    "latest",
    "status",
    "activity",
    "alerts",
    "parameters",
]

CONNECTIVITY_INCLUDES = ["mqtt", "topics", "upstream", "client"]


def _machine_rows(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not payload or not isinstance(payload.get("machines"), list):
        return []
    return [m for m in payload["machines"] if isinstance(m, dict)]


def _find_machine(payload: Optional[Dict[str, Any]], machine_id: str) -> Optional[Dict[str, Any]]:
    want = machine_id.strip().lower()
    for m in _machine_rows(payload):
        mid = str(m.get("machineId") or m.get("machine_id") or "").lower()
        if mid == want:
            return m
    return None


class FleetQueryService:
    def __init__(self, client: Optional[UpstreamFleetClient] = None):
        self.client = client or UpstreamFleetClient()

    def is_configured(self) -> bool:
        return self.client.is_configured

    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        return self.client.get_capabilities()

    def query(self, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.client.query(body)

    def list_visible_machine_ids(self) -> List[str]:
        caps = self.get_capabilities() or {}
        machines = caps.get("machines") or caps.get("machineIds") or []
        if isinstance(machines, list):
            out: List[str] = []
            for item in machines:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict) and item.get("machineId"):
                    out.append(str(item["machineId"]))
            return out
        return []

    def query_snapshot(
        self,
        machine_ids: Optional[List[str]] = None,
        include: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        body: Dict[str, Any] = {"include": include or DEFAULT_SNAPSHOT_INCLUDES}
        if machine_ids:
            body["machineIds"] = machine_ids
        return self.query(body)

    def query_connectivity(
        self,
        machine_ids: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """MQTT broker, topic paths, upstream feed status, client profile."""
        body: Dict[str, Any] = {"include": CONNECTIVITY_INCLUDES}
        if machine_ids:
            body["machineIds"] = machine_ids
        return self.query(body)

    def extract_mqtt_for_machine(
        self,
        payload: Optional[Dict[str, Any]],
        machine_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Normalize mqtt + topics for one machine. Password is never in the API;
        use MQTT_PASSWORD in env when hasPassword is true.
        """
        row = _find_machine(payload, machine_id)
        if not row:
            return None

        mqtt = row.get("mqtt") if isinstance(row.get("mqtt"), dict) else {}
        topics_raw = row.get("topics") if isinstance(row.get("topics"), list) else []

        topic_paths: List[str] = []
        for t in topics_raw:
            if not isinstance(t, dict):
                continue
            path = t.get("fullPath") or t.get("full_path") or t.get("topic")
            if path:
                topic_paths.append(str(path))
            else:
                suffix = t.get("topicSuffix") or t.get("topic_suffix") or t.get("name")
                prefix = mqtt.get("topicPrefix") or mqtt.get("prefix") or ""
                if suffix and prefix:
                    topic_paths.append(f"{prefix.rstrip('/')}/{machine_id}/sensor/{suffix}")

        broker_host = (
            mqtt.get("brokerHost")
            or mqtt.get("broker_host")
            or mqtt.get("host")
        )
        broker_url = mqtt.get("url") or mqtt.get("brokerUrl") or mqtt.get("broker_url")
        if not broker_host and broker_url:
            broker_host = str(broker_url).replace("mqtt://", "").replace("ssl://", "").split(":")[0]

        port = mqtt.get("port") or mqtt.get("brokerPort") or 1883
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 1883

        return {
            "machine_id": machine_id,
            "broker_host": broker_host,
            "broker_port": port,
            "client_id": mqtt.get("clientId") or mqtt.get("client_id"),
            "username": mqtt.get("username"),
            "has_password": bool(mqtt.get("hasPassword") or mqtt.get("has_password")),
            "mqtt_source": mqtt.get("source"),
            "topics": topic_paths,
            "upstream": row.get("upstream"),
            "subscribe_wildcard": mqtt.get("subscribeWildcard") or f"apms/{machine_id}/#",
        }

    def fetch_history(
        self,
        machine_ids: Optional[List[str]] = None,
        *,
        source: str = "db",
        duration: str = "14d",
        max_points: int = 300,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Past readings from fleet API.
        history.source: \"live\" (recent buffer) or \"db\" (stored).
        """
        body: Dict[str, Any] = {
            "include": ["history"],
            "history": {
                "source": source,
                "duration": duration,
                "maxPoints": max_points,
            },
        }
        if machine_ids:
            body["machineIds"] = machine_ids
        if fields:
            body["fields"] = fields

        payload = self.query(body)
        rows: List[Dict[str, Any]] = []
        for machine in _machine_rows(payload):
            history = machine.get("history") if isinstance(machine.get("history"), dict) else {}
            mid = machine.get("machineId")
            for row in history.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                if mid and not row.get("machineId"):
                    row = {**row, "machineId": mid}
                rows.append(row)
        return rows

    def resolve_mqtt_env_override(
        self,
        machine_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        If FLEET_MQTT_FROM_QUERY is enabled, pull broker settings from the fleet API.
        Falls back to None when API missing or machine not found (use env MQTT_*).
        """
        import os

        if os.environ.get("FLEET_MQTT_FROM_QUERY", "").lower() not in ("1", "true", "yes"):
            return None
        if not self.is_configured():
            return None

        ids = [machine_id] if machine_id else self.list_visible_machine_ids()[:1]
        if not ids:
            logger.warning("Fleet MQTT: no visible machines from capabilities")
            return None

        payload = self.query_connectivity(ids)
        cfg = self.extract_mqtt_for_machine(payload, ids[0])
        if cfg and cfg.get("broker_host"):
            logger.info(
                f"Fleet MQTT resolved for {ids[0]}: {cfg['broker_host']}:{cfg['broker_port']} "
                f"({len(cfg.get('topics') or [])} topics)"
            )
        return cfg


fleet_query_service = FleetQueryService()
