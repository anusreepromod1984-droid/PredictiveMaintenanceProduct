"""Fleet /ai/query service — MQTT extraction and history body shape."""

from src.services.fleet_query_service import FleetQueryService
from src.services.threshold_provider import UpstreamFleetClient


class _MockClient(UpstreamFleetClient):
    def __init__(self):
        super().__init__(api_key="test-key", base_url="http://fleet.test")

    def get_capabilities(self):
        return {"machines": [{"machineId": "g-sensor-01"}, {"machineId": "gbz-rx-1"}]}

    def query(self, body):
        if body.get("include") == ["history"]:
            return {
                "machines": [
                    {
                        "machineId": "g-sensor-01",
                        "history": {"rows": [{"timestamp": 1, "imuAcceleration": 0.05}]},
                    }
                ]
            }
        return {
            "machines": [
                {
                    "machineId": "g-sensor-01",
                    "mqtt": {
                        "url": "mqtt://broker.plant.local",
                        "port": 8883,
                        "clientId": "apms-edge-01",
                        "username": "edge",
                        "hasPassword": True,
                        "source": "override",
                    },
                    "topics": [
                        {
                            "name": "vibration",
                            "topicSuffix": "vibration",
                            "fullPath": "plant/g-sensor-01/sensor/vibration",
                        }
                    ],
                    "upstream": {"connected": True},
                }
            ]
        }


def test_extract_mqtt_from_query_payload():
    svc = FleetQueryService(client=_MockClient())
    payload = svc.query_connectivity(["g-sensor-01"])
    cfg = svc.extract_mqtt_for_machine(payload, "g-sensor-01")
    assert cfg is not None
    assert cfg["broker_host"] == "broker.plant.local"
    assert cfg["broker_port"] == 8883
    assert cfg["username"] == "edge"
    assert cfg["has_password"] is True
    assert "plant/g-sensor-01/sensor/vibration" in cfg["topics"]


def test_fetch_history_db_rows():
    svc = FleetQueryService(client=_MockClient())
    rows = svc.fetch_history(["g-sensor-01"], source="db", duration="7d", max_points=100)
    assert len(rows) == 1
    assert rows[0]["machineId"] == "g-sensor-01"
    assert rows[0]["imuAcceleration"] == 0.05


def test_list_visible_machine_ids_from_capabilities():
    svc = FleetQueryService(client=_MockClient())
    assert svc.list_visible_machine_ids() == ["g-sensor-01", "gbz-rx-1"]
