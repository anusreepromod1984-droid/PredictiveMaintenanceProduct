"""
Asynchronous Industrial Stream Consumer
=========================================
Provides a unified abstraction for real-time telemetry ingestion from:
  1. MQTT Broker (Sparkplug B / raw JSON) - for OT edge clients
  2. Kafka / Confluent - for cloud-scale streaming
  3. HTTP REST (current, retained as fallback)

Architecture Decision:
  - The consumer runs as a background asyncio task started at app startup.
  - When a message arrives, it calls the same LangGraph orchestrator pipeline
    used by the REST endpoint, guaranteeing identical physics execution.
  - Optional broker integration: set MQTT_BROKER_HOST or KAFKA_BOOTSTRAP_SERVERS
    environment variables. Without them the consumer is a no-op.

Production guide:
  - OT Edge: configure Ignition/Kepware → MQTT broker (EMQX/Mosquitto) → this consumer
  - Cloud: configure Kafka Connect OPC-UA Source Connector → Kafka topic → this consumer
"""

import asyncio
import json
import os
import time
from typing import Dict, Any, Optional, Callable, Awaitable

from src.utils.logger import get_logger

logger = get_logger("Ingestion.StreamConsumer")


# ---------------------------------------------------------------------------
# Message envelope (normalized across MQTT / Kafka / REST)
# ---------------------------------------------------------------------------
def parse_sparkplug_b_payload(raw: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse Sparkplug B / JSON flat packet into APMS IngestTelemetryPacket dict.
    Extend this for Protobuf Sparkplug B parsing if needed.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
        # Normalize Sparkplug B metrics list to signals dict
        if "metrics" in payload and isinstance(payload["metrics"], list):
            signals = {m["name"]: m["value"] for m in payload["metrics"] if "name" in m and "value" in m}
            payload["signals"] = signals
            payload.pop("metrics", None)
        # Ensure required keys
        if "tenant_id" not in payload or "asset_id" not in payload:
            # Attempt to parse from topic prefix if available
            pass
        payload.setdefault("timestamp", time.time())
        return payload
    except Exception as e:
        logger.error(f"Failed to parse Sparkplug B payload: {e}")
        return None


# ---------------------------------------------------------------------------
# MQTT Consumer (requires paho-mqtt)
# ---------------------------------------------------------------------------
class MQTTConsumer:
    """
    Async wrapper around paho-mqtt for Sparkplug B / JSON telemetry.
    Topic pattern: apms/{tenant_id}/{asset_id}/telemetry
    """

    def __init__(
        self,
        broker_host: str,
        broker_port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        on_packet: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.username = username
        self.password = password
        self.on_packet = on_packet
        self._client = None
        self._loop = None

    def _try_import_paho(self):
        try:
            import paho.mqtt.client as mqtt
            return mqtt
        except ImportError:
            logger.warning("paho-mqtt not installed. MQTT consumer disabled. Install with: pip install paho-mqtt")
            return None

    async def start(self):
        mqtt = self._try_import_paho()
        if not mqtt:
            return

        self._loop = asyncio.get_event_loop()
        client = mqtt.Client(client_id="apms-consumer-01", clean_session=True)

        if self.username:
            client.username_pw_set(self.username, self.password)

        def on_message(c, userdata, msg):
            payload = parse_sparkplug_b_payload(msg.payload)
            if payload:
                # Parse tenant/asset from topic if not in payload
                parts = msg.topic.split("/")
                if len(parts) >= 4:
                    payload.setdefault("tenant_id", parts[1])
                    payload.setdefault("asset_id", parts[2])
                if self.on_packet and asyncio.iscoroutinefunction(self.on_packet):
                    asyncio.run_coroutine_threadsafe(self.on_packet(payload), self._loop)

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                logger.info(f"MQTT connected to {self.broker_host}:{self.broker_port}")
                c.subscribe("apms/#")
            else:
                logger.error(f"MQTT connection failed: rc={rc}")

        def on_disconnect(c, userdata, rc):
            logger.warning(f"MQTT disconnected: rc={rc} — reconnecting in 5s")

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect

        self._client = client
        try:
            client.connect_async(self.broker_host, self.broker_port)
            client.loop_start()
            logger.info(f"MQTT consumer started for {self.broker_host}:{self.broker_port}")
        except Exception as e:
            logger.error(f"MQTT start failed: {e}")

    async def stop(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("MQTT consumer stopped")


# ---------------------------------------------------------------------------
# Kafka Consumer (requires aiokafka)
# ---------------------------------------------------------------------------
class KafkaConsumer:
    """
    Async Kafka consumer for streaming telemetry from OPC UA Source Connectors.
    Topics: apms-telemetry-{tenant_id}  or  apms-telemetry (multi-tenant)
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str = "apms-telemetry",
        group_id: str = "apms-consumer-group",
        on_packet: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.on_packet = on_packet
        self._consumer = None

    async def start(self):
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            logger.warning("aiokafka not installed. Kafka consumer disabled. Install with: pip install aiokafka")
            return

        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        )
        await self._consumer.start()
        logger.info(f"Kafka consumer started: bootstrap={self.bootstrap_servers} topic={self.topic}")
        asyncio.create_task(self._consume_loop())

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                packet = msg.value
                if self.on_packet and isinstance(packet, dict):
                    try:
                        await self.on_packet(packet)
                    except Exception as e:
                        logger.error(f"Kafka packet processing error: {e}")
        except Exception as e:
            logger.error(f"Kafka consumer loop error: {e}")

    async def stop(self):
        if self._consumer:
            await self._consumer.stop()
            logger.info("Kafka consumer stopped")


# ---------------------------------------------------------------------------
# StreamConsumerManager - lifecycle managed by FastAPI startup/shutdown
# ---------------------------------------------------------------------------
class StreamConsumerManager:
    """
    Reads environment config and starts the appropriate consumer(s).
    Injected into FastAPI lifespan.
    """

    def __init__(self):
        self._consumers = []

    async def startup(self, on_packet: Callable[[Dict[str, Any]], Awaitable[None]]):
        # MQTT
        mqtt_host = os.environ.get("MQTT_BROKER_HOST", "")
        if mqtt_host:
            mqtt_consumer = MQTTConsumer(
                broker_host=mqtt_host,
                broker_port=int(os.environ.get("MQTT_BROKER_PORT", "1883")),
                username=os.environ.get("MQTT_USERNAME"),
                password=os.environ.get("MQTT_PASSWORD"),
                on_packet=on_packet,
            )
            await mqtt_consumer.start()
            self._consumers.append(mqtt_consumer)
            logger.info("MQTT consumer registered")

        # Kafka
        kafka_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
        if kafka_servers:
            kafka_consumer = KafkaConsumer(
                bootstrap_servers=kafka_servers,
                topic=os.environ.get("KAFKA_TOPIC", "apms-telemetry"),
                group_id=os.environ.get("KAFKA_GROUP_ID", "apms-consumer-group"),
                on_packet=on_packet,
            )
            await kafka_consumer.start()
            self._consumers.append(kafka_consumer)
            logger.info("Kafka consumer registered")

        if not self._consumers:
            logger.info("No streaming brokers configured. Running REST-only ingestion mode.")

    async def shutdown(self):
        for consumer in self._consumers:
            try:
                if hasattr(consumer, "stop"):
                    await consumer.stop()
            except Exception as e:
                logger.error(f"Consumer shutdown error: {e}")


stream_consumer_manager = StreamConsumerManager()
