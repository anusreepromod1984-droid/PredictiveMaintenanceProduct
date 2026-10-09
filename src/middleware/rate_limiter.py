"""
Production Rate Limiter Middleware (Sliding-Window In-Memory)
=============================================================
Implements RFC 6585 / IETF draft-ietf-httpapi-ratelimit-headers.

Design:
  - Per-API-key OR per-IP sliding window counter using deque
  - Configurable limits per endpoint category (ingest vs read vs admin)
  - Returns X-RateLimit-* headers on every response
  - Emits 429 Too Many Requests with Retry-After when limit exceeded

Production note: swap _counters to Redis/Valkey for multi-pod horizontal scale.
"""

import time
from collections import defaultdict, deque
from typing import Dict, Deque, Callable
from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.utils.logger import get_logger

logger = get_logger("Middleware.RateLimiter")

# ---------------------------------------------------------------------------
# Configuration: (max_requests, window_seconds) per path-category prefix
# ---------------------------------------------------------------------------
_RATE_LIMITS: Dict[str, tuple] = {
    "/api/v1/telemetry/ingest":  (200, 60),   # 200 req/min for high-freq ingestion
    "/api/v1/simulation":        (30,  60),   # 30 req/min for simulation
    "/api/v1/tenants":           (60,  60),   # 60 req/min for tenant management
    "/api/v1":                   (300, 60),   # default: 300 req/min for all other APIs
    "/":                         (600, 60),   # 600 req/min global fallback
}


def _get_limit_for_path(path: str) -> tuple:
    for prefix in sorted(_RATE_LIMITS.keys(), key=lambda x: -len(x)):
        if path.startswith(prefix):
            return _RATE_LIMITS[prefix]
    return _RATE_LIMITS["/"]


class SlidingWindowRateLimiter(BaseHTTPMiddleware):
    """
    ASGI middleware: wraps every request with rate-limit enforcement.
    """
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        # {client_key: deque of request timestamps}
        self._windows: Dict[str, Deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        # Prefer API key header; fall back to forwarded IP or direct IP
        api_key = request.headers.get("x-api-key", "")
        if api_key:
            return f"apikey:{api_key[:16]}"  # truncated for storage
        forwarded = request.headers.get("x-forwarded-for", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        return f"ip:{ip}"

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        # Skip rate limiting for health checks and docs
        if path in ("/health", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)

        max_requests, window_seconds = _get_limit_for_path(path)
        client_key = f"{self._client_key(request)}:{path.split('/')[3] if len(path.split('/')) > 3 else 'root'}"

        now = time.monotonic()
        window = self._windows[client_key]

        # Evict timestamps outside sliding window
        while window and window[0] < now - window_seconds:
            window.popleft()

        if len(window) >= max_requests:
            oldest = window[0]
            retry_after = int(window_seconds - (now - oldest)) + 1
            logger.warning(f"Rate limit exceeded: client={client_key} path={path}")
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({max_requests} requests/{window_seconds}s). Retry after {retry_after}s.",
                headers={"Retry-After": str(retry_after)}
            )

        window.append(now)
        response: Response = await call_next(request)

        # Append standard rate-limit headers
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max_requests - len(window))
        response.headers["X-RateLimit-Window"] = str(window_seconds)
        return response
