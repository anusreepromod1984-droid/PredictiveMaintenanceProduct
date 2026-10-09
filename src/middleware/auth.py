"""
Enterprise Authentication & Authorization Middleware
=====================================================
Supports two authentication mechanisms:
  1. API Key (X-API-Key header) - for machine-to-machine OT edge clients
  2. Bearer JWT  (Authorization: Bearer <token>) - for web/UI clients & operators

JWT Claims:
  {
    "sub":        "operator_jane",
    "tenant_id":  "propel_industries",  # scope access to single tenant
    "roles":      ["read", "ingest", "admin"],
    "exp":        <unix epoch>
  }

Production deployment: swap the hardcoded SECRET with Vault / AWS KMS / GCP Secret Manager.
"""

import os
import time
import hmac
import hashlib
import base64
import json
from typing import Optional, Dict, Any
from fastapi import HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader

from src.utils.logger import get_logger

logger = get_logger("Middleware.Auth")

# ---------------------------------------------------------------------------
# Configuration  (inject via env in production, never commit secrets to VCS)
# ---------------------------------------------------------------------------
_JWT_SECRET: str = os.environ.get("APMS_JWT_SECRET", "CHANGEME_USE_VAULT_IN_PRODUCTION_32CHARS_MIN")
_JWT_ALGORITHM: str = "HS256"
_JWT_EXPIRY_SECONDS: int = int(os.environ.get("APMS_JWT_EXPIRY", "86400"))  # 24 h

# Preloaded API keys per tenant  (replace with DB table in production)
_VALID_API_KEYS: Dict[str, str] = {
    k: v for k, v in (item.split("=", 1) for item in
                       os.environ.get("APMS_API_KEYS", "propel_industries=propel-dev-key-001,nest_group=nest-dev-key-002").split(",")
                       if "=" in item)
}

# ---------------------------------------------------------------------------
# Minimal HS256 JWT implementation (no extra dependency, easy to swap PyJWT)
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

def create_jwt(payload: Dict[str, Any]) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload.setdefault("iat", int(time.time()))
    payload.setdefault("exp", int(time.time()) + _JWT_EXPIRY_SECONDS)
    body = _b64url_encode(json.dumps(payload).encode())
    signing_input = f"{header}.{body}"
    sig = hmac.new(_JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"

def verify_jwt(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Malformed JWT token")
    header_b64, body_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{body_b64}"
    expected_sig = hmac.new(_JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    actual_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise HTTPException(status_code=401, detail="Invalid JWT signature")
    try:
        payload = json.loads(_b64url_decode(body_b64))
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed JWT payload")
    if payload.get("exp", 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="JWT token expired")
    return payload

# ---------------------------------------------------------------------------
# FastAPI Security Schemes
# ---------------------------------------------------------------------------
_bearer_scheme = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_current_principal(
    request: Request,
    api_key: Optional[str] = Security(_api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> Dict[str, Any]:
    """
    FastAPI dependency. Returns the principal dict on success.
    Raises HTTP 401 on authentication failure.
    Usage:
        @router.post("/...", dependencies=[Depends(get_current_principal)])
        def endpoint(): ...
    or with claims:
        @router.post("/...")
        def endpoint(principal: dict = Depends(get_current_principal)): ...
    """
    # 1. Try API Key authentication (OT edge / machine clients)
    if api_key:
        for tenant_id, stored_key in _VALID_API_KEYS.items():
            if hmac.compare_digest(api_key.strip(), stored_key.strip()):
                logger.debug(f"API Key auth OK for tenant={tenant_id}")
                return {"sub": f"api_key_{tenant_id}", "tenant_id": tenant_id, "roles": ["ingest", "read"]}
        raise HTTPException(status_code=401, detail="Invalid API Key")

    # 2. Try Bearer JWT authentication (operator / UI clients)
    if bearer:
        claims = verify_jwt(bearer.credentials)
        logger.debug(f"JWT auth OK for sub={claims.get('sub')}, tenant={claims.get('tenant_id')}")
        return claims

    # 3. No credentials – production should reject; dev mode allows anonymous read
    import os
    if os.environ.get("APMS_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return {"sub": "anonymous", "tenant_id": "*", "roles": ["ingest", "read", "admin"]}

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide X-API-Key header or Authorization: Bearer <token>."
    )


def require_role(required_role: str):
    """Dependency factory: enforce role membership beyond authentication."""
    def _check(principal: Dict[str, Any] = Security(get_current_principal)):
        if required_role not in principal.get("roles", []):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{required_role}' required. Your roles: {principal.get('roles')}"
            )
        return principal
    return _check


def assert_tenant_access(principal: Dict[str, Any], requested_tenant_id: str):
    """
    Ensure the principal may access the requested tenant.
    An admin (*) or an exact match is allowed.
    Raises HTTP 403 otherwise.
    """
    allowed = principal.get("tenant_id", "*")
    if allowed != "*" and allowed.lower() != requested_tenant_id.lower():
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Your token is scoped to tenant '{allowed}', not '{requested_tenant_id}'."
        )
