"""
Request Tracing Middleware
==========================
Injects and propagates X-Request-ID across every request/response for
distributed tracing (compatible with OpenTelemetry / Jaeger / Cloud Trace).
"""

import uuid
import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from typing import Callable

from src.utils.logger import get_logger

logger = get_logger("Middleware.RequestID")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Stamps every request with a unique X-Request-ID and logs latency."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or f"apms-{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id

        t0 = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

        logger.info(
            f"[{request_id}] {request.method} {request.url.path} → "
            f"{response.status_code} ({elapsed_ms}ms)"
        )
        return response
