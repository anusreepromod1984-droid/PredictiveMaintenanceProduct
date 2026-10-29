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
    CORS_ORIGINS: str = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,https://predictive-maintenance-system.vercel.app"
        if os.environ.get("ENVIRONMENT", "production") == "production"
        else "*",
    )

    # -----------------------------------------------------------------------
    # Production: Persistent Historian
    # -----------------------------------------------------------------------
    # Set HISTORIAN_DSN or DATABASE_URL or HISTORIAN_DB_PATH to a TimescaleDB/PostgreSQL DSN for cloud deployments.
    # Example: "postgresql://user:pass@tsdb-host:5432/apms"
    # Leave as default for local SQLite (dev/staging)
    HISTORIAN_DB_PATH: str = os.environ.get(
        "HISTORIAN_DSN",
        os.environ.get("DATABASE_URL", os.environ.get("HISTORIAN_DB_PATH", "./data/apms_historian.db")),
    )
    HISTORIAN_RETAIN_DAYS: int = int(os.environ.get("HISTORIAN_RETAIN_DAYS", "90"))

    # -----------------------------------------------------------------------
    # Security (JWT + API Keys)
    # -----------------------------------------------------------------------
    # APMS_AUTH_DISABLED=true disables auth entirely for local development. Defaults to false in production.
    AUTH_DISABLED: bool = os.environ.get(
        "APMS_AUTH_DISABLED",
        "false" if os.environ.get("ENVIRONMENT", "production") == "production" else "true",
    ).lower() in ("1", "true", "yes")
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
    ENABLE_SIMULATION_ROUTES: bool = os.environ.get(
        "ENABLE_SIMULATION_ROUTES",
        "true" if os.environ.get("ENVIRONMENT", "production") != "production" else "false",
    ).lower() in ("1", "true", "yes")

    # -----------------------------------------------------------------------
    # Upstream Fleet / PDM AI Query API (Dynamic Thresholds & Metadata)
    # -----------------------------------------------------------------------
    AI_SERVICE_BASE_URL: str = os.environ.get("AI_SERVICE_BASE_URL", "http://168.144.37.186:4000")
    AI_SERVICE_API_KEY: str = os.environ.get("AI_SERVICE_API_KEY", "")
    AI_SERVICE_TIMEOUT_S: float = float(os.environ.get("AI_SERVICE_TIMEOUT_S", "5.0"))
    # JSON object: PDM companySlug -> APMS tenant_id (merged with built-in defaults in reports route)
    COMPANY_SLUG_TENANT_MAP: str = os.environ.get("COMPANY_SLUG_TENANT_MAP", "")
    FLEET_MQTT_FROM_QUERY: bool = os.environ.get("FLEET_MQTT_FROM_QUERY", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    FLEET_MQTT_MACHINE_ID: str = os.environ.get("FLEET_MQTT_MACHINE_ID", "")
    THRESHOLD_CACHE_TTL_SEC: float = float(os.environ.get("THRESHOLD_CACHE_TTL_SEC", "300.0"))

    # Plant report Remarks — LLM via Groq (OpenAI-compatible POST .../chat/completions)
    REPORT_LLM_ENABLED: bool = os.environ.get("REPORT_LLM_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    REPORT_LLM_API_KEY: str = os.environ.get("REPORT_LLM_API_KEY", "")
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    REPORT_LLM_BASE_URL: str = os.environ.get("REPORT_LLM_BASE_URL", "https://api.groq.com/openai/v1")
    # llama-3.3-70b-versatile retired on Groq free/dev tier (2026-08-16) → use current OSS/Qwen ids
    REPORT_LLM_MODEL: str = os.environ.get("REPORT_LLM_MODEL", "openai/gpt-oss-120b")
    REPORT_LLM_FALLBACK_MODELS: str = os.environ.get(
        "REPORT_LLM_FALLBACK_MODELS",
        "qwen/qwen3-32b,openai/gpt-oss-20b",
    )
    REPORT_LLM_TIMEOUT_S: float = float(os.environ.get("REPORT_LLM_TIMEOUT_S", "50"))

    def report_llm_api_key(self) -> str:
        return (self.REPORT_LLM_API_KEY or self.GROQ_API_KEY or self.OPENAI_API_KEY or "").strip()

    ASSISTANT_LLM_ENABLED: bool = os.environ.get("ASSISTANT_LLM_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    AGENT_ANALYST_LLM_ENABLED: bool = os.environ.get("AGENT_ANALYST_LLM_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    ASSISTANT_USE_PIPELINE_ANALYST: bool = os.environ.get("ASSISTANT_USE_PIPELINE_ANALYST", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    @property
    def report_llm_enabled(self) -> bool:
        return self.REPORT_LLM_ENABLED and bool(self.report_llm_api_key())

    @property
    def assistant_llm_enabled(self) -> bool:
        return self.ASSISTANT_LLM_ENABLED and bool(self.report_llm_api_key())

    @property
    def agent_analyst_llm_enabled(self) -> bool:
        return self.AGENT_ANALYST_LLM_ENABLED and bool(self.report_llm_api_key())

    # -----------------------------------------------------------------------
    # Machine Specs & Internet Search Agent
    # -----------------------------------------------------------------------
    INTERNET_SPEC_SEARCH_ENABLED: bool = os.environ.get("INTERNET_SPEC_SEARCH_ENABLED", "true").lower() in ("1", "true", "yes")
    INTERNET_SPEC_SEARCH_URL: str = os.environ.get("INTERNET_SPEC_SEARCH_URL", "")
    INTERNET_SPEC_SEARCH_API_KEY: str = os.environ.get("INTERNET_SPEC_SEARCH_API_KEY", "")
    INTERNET_SPEC_TIMEOUT_S: float = float(os.environ.get("INTERNET_SPEC_TIMEOUT_S", "4.0"))

    def get_cors_origins(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }


settings = Settings()
