"""
Enterprise Predictive Maintenance System (APMS) - Product Core
Configuration Module
"""

import os
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Enterprise Multi-Tenant APMS Product"
    APP_VERSION: str = "3.0.0-production"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False

    # Server Binding
    HOST: str = "0.0.0.0"
    PORT: int = int(os.environ.get("PORT", "8008"))

    # In-memory hot buffer size per (tenant, asset) - acts as L1 cache above historian
    RING_BUFFER_CAPACITY: int = 500

    # Telemetry freshness tolerance in seconds
    STALE_SENSOR_THRESHOLD_SEC: float = 30.0

    # CORS
    CORS_ORIGINS: str = "*"

    # -----------------------------------------------------------------------
    # Production: Persistent Historian
    # -----------------------------------------------------------------------
    # Set HISTORIAN_DB_PATH to a TimescaleDB/PostgreSQL DSN for cloud deployments.
    # Example: "postgresql+psycopg2://user:pass@tsdb-host:5432/apms"
    # Leave as default for local SQLite (dev/staging)
    HISTORIAN_DB_PATH: str = os.environ.get("HISTORIAN_DB_PATH", "./data/apms_historian.db")
    HISTORIAN_RETAIN_DAYS: int = int(os.environ.get("HISTORIAN_RETAIN_DAYS", "90"))

    # -----------------------------------------------------------------------
    # Security (JWT + API Keys)
    # -----------------------------------------------------------------------
    # APMS_AUTH_DISABLED=true disables auth entirely for local development
    AUTH_DISABLED: bool = os.environ.get("APMS_AUTH_DISABLED", "true").lower() in ("1", "true", "yes")
    JWT_SECRET: str = os.environ.get("APMS_JWT_SECRET", "CHANGEME_USE_VAULT_IN_PRODUCTION_32CHARS")
    JWT_EXPIRY_SECONDS: int = int(os.environ.get("APMS_JWT_EXPIRY", "86400"))

    # -----------------------------------------------------------------------
    # Streaming Ingestion (optional)
    # -----------------------------------------------------------------------
    MQTT_BROKER_HOST: str = os.environ.get("MQTT_BROKER_HOST", "")
    MQTT_BROKER_PORT: int = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
    MQTT_USERNAME: str = os.environ.get("MQTT_USERNAME", "")
    MQTT_PASSWORD: str = os.environ.get("MQTT_PASSWORD", "")
    KAFKA_BOOTSTRAP_SERVERS: str = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
    KAFKA_TOPIC: str = os.environ.get("KAFKA_TOPIC", "apms-telemetry")
    KAFKA_GROUP_ID: str = os.environ.get("KAFKA_GROUP_ID", "apms-consumer-group")

    # -----------------------------------------------------------------------
    # SBM Engine Settings
    # -----------------------------------------------------------------------
    SBM_MIN_TRAIN_SAMPLES: int = int(os.environ.get("SBM_MIN_TRAIN_SAMPLES", "30"))
    SBM_REFERENCE_SAMPLES: int = int(os.environ.get("SBM_REFERENCE_SAMPLES", "100"))
    SBM_CUSUM_THRESHOLD: float = float(os.environ.get("SBM_CUSUM_THRESHOLD", "5.0"))

    # -----------------------------------------------------------------------
    # Observability
    # -----------------------------------------------------------------------
    ENABLE_METRICS_ENDPOINT: bool = os.environ.get("ENABLE_METRICS", "true").lower() in ("1", "true", "yes")

    def get_cors_origins(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }


settings = Settings()
