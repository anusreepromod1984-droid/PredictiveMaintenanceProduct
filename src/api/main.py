"""
Multi-Tenant APMS Product - FastAPI Application Entrypoint (v3.0 Production)
=============================================================================
Production-grade upgrades:
  - JWT + API-Key authentication middleware
  - Sliding-window rate limiter
  - X-Request-ID distributed tracing middleware
  - Prometheus /metrics endpoint
  - Async FastAPI lifespan: MQTT/Kafka consumer startup + graceful shutdown
  - Security headers (CSP, X-Frame-Options, HSTS)
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from src.config import settings
from src.utils.logger import get_logger
from src.utils.metrics import platform_metrics
from src.middleware.rate_limiter import SlidingWindowRateLimiter
from src.middleware.request_id import RequestIDMiddleware

from src.api.routes.tenants import router as tenants_router
from src.api.routes.assets import router as assets_router
from src.api.routes.telemetry import router as telemetry_router
from src.api.routes.diagnostics import router as diagnostics_router
from src.api.routes.simulation import router as simulation_router
from src.api.routes.analytics import router as analytics_router
from src.api.routes.fault_assistant import router as fault_assistant_router

logger = get_logger("API.Main")


# ---------------------------------------------------------------------------
# Async Lifespan: startup & graceful shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    FastAPI lifespan context manager.
    Startup:  Initialize historian schema, start stream consumers.
    Shutdown: Gracefully stop MQTT/Kafka consumers, flush pending metrics.
    """
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # 1. Ensure historian DB schema is initialized
    try:
        from src.db.historian import platform_historian
        logger.info(f"Historian initialized at: {settings.HISTORIAN_DB_PATH}")
    except Exception as e:
        logger.error(f"Historian init failed: {e}")

    # 2. Define async packet handler (routes to LangGraph orchestrator)
    async def on_stream_packet(packet_dict: dict):
        try:
            from src.schemas.telemetry import IngestTelemetryPacket
            from src.registry.asset_registry import asset_twin_registry
            from src.agents.dynamic_orchestrator import global_orchestrator
            packet = IngestTelemetryPacket(**packet_dict)
            twin = asset_twin_registry.get_asset(packet.tenant_id, packet.asset_id)
            if twin:
                tenant_profile = asset_twin_registry.get_tenant(packet.tenant_id)
                global_orchestrator.run_pipeline(packet, twin, tenant_profile)
        except Exception as e:
            logger.error(f"Stream packet processing error: {e}")

    # 3. Start MQTT / Kafka consumers if configured
    try:
        from src.ingestion.stream_consumer import stream_consumer_manager
        await stream_consumer_manager.startup(on_stream_packet)
    except Exception as e:
        logger.warning(f"Stream consumer startup error (REST ingestion still available): {e}")

    logger.info(f"{settings.APP_NAME} is ONLINE on port {settings.PORT}")
    yield  # Application runs here

    # --- SHUTDOWN ---
    logger.info("Shutting down stream consumers...")
    try:
        from src.ingestion.stream_consumer import stream_consumer_manager
        await stream_consumer_manager.shutdown()
    except Exception as e:
        logger.error(f"Stream consumer shutdown error: {e}")

    logger.info(f"{settings.APP_NAME} shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Multi-Tenant, Multi-Device, Multi-Parameter Industrial Predictive Maintenance Agent System. "
        "Provides physics-based diagnostics, spectral vibration analysis, "
        "Similarity-Based Modeling (SBM), and adaptive Weibull prognostics "
        "across heavy machinery and electronics manufacturing verticals."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    contact={
        "name": "APMS Platform Engineering",
        "email": "platform-eng@apms.ai",
    },
    license_info={
        "name": "Proprietary – Enterprise License Required",
    },
)

# ---------------------------------------------------------------------------
# Middleware Stack (order matters: outer → inner)
# ---------------------------------------------------------------------------
# 1. Request ID (outermost - stamps all requests)
app.add_middleware(RequestIDMiddleware)

# 2. Rate Limiter
app.add_middleware(SlidingWindowRateLimiter)

# 3. CORS
cors_origins = settings.get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


# 4. Security Headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ---------------------------------------------------------------------------
# System Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health_check():
    """Platform liveness + readiness probe (Kubernetes-compatible)."""
    metrics_summary = platform_metrics.get_summary()
    try:
        from src.engines.sbm_engine import sbm_registry
        sbm_status = sbm_registry.status()
    except Exception:
        sbm_status = []

    return {
        "status": "ONLINE",
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "auth_mode": "disabled" if settings.AUTH_DISABLED else "jwt_apikey",
        "historian": settings.HISTORIAN_DB_PATH,
        "streaming": {
            "mqtt_configured": bool(settings.MQTT_BROKER_HOST),
            "kafka_configured": bool(settings.KAFKA_BOOTSTRAP_SERVERS),
        },
        "metrics": metrics_summary,
        "sbm_models": sbm_status,
    }


@app.get("/metrics", tags=["System"], response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus scrape endpoint. Expose to Prometheus + visualize in Grafana."""
    if not settings.ENABLE_METRICS_ENDPOINT:
        return PlainTextResponse("# Metrics endpoint disabled", status_code=403)
    return PlainTextResponse(
        platform_metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4"
    )


# ---------------------------------------------------------------------------
# Auth Token Endpoint (dev convenience – production uses your Identity Provider)
# ---------------------------------------------------------------------------
@app.post("/auth/token", tags=["Security"])
def issue_dev_token(tenant_id: str, sub: str = "operator", roles: str = "ingest,read"):
    """
    Issues a development JWT token.
    In production, replace with your enterprise OIDC / OAuth2 provider (Okta, Entra ID, etc.)
    """
    from src.middleware.auth import create_jwt
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": [r.strip() for r in roles.split(",")]
    }
    token = create_jwt(payload)
    return {
        "access_token": token,
        "token_type": "bearer",
        "tenant_id": tenant_id,
        "note": "Development token only. Use your enterprise IDP in production."
    }


# ---------------------------------------------------------------------------
# Mount Modular Routers
# ---------------------------------------------------------------------------
api_prefix = "/api/v1"
app.include_router(tenants_router, prefix=api_prefix)
app.include_router(assets_router, prefix=api_prefix)
app.include_router(telemetry_router, prefix=api_prefix)
app.include_router(diagnostics_router, prefix=api_prefix)
app.include_router(simulation_router, prefix=api_prefix)
app.include_router(analytics_router, prefix=api_prefix)
# Routes already include /api/v1 — same paths the UI server proxies to.
app.include_router(fault_assistant_router)

logger.info(f"Initialized {settings.APP_NAME} v{settings.APP_VERSION} — API prefix: {api_prefix}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=port)
