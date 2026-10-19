"""Production tenancy: analytics and settings require auth scope."""

from fastapi.testclient import TestClient

from src.api.main import app
from src.middleware.auth import create_jwt

client = TestClient(app)


def test_analytics_denied_cross_tenant():
    token = create_jwt({
        "sub": "nest_ops",
        "tenant_id": "nest_group",
        "roles": ["read"],
    })
    resp = client.get(
        "/api/v1/tenants/propel_industries/assets/jaw_crusher_01/telemetry/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_settings_alert_language_requires_tenant_query():
    resp = client.get("/api/v1/settings/alert-language")
    assert resp.status_code == 422
