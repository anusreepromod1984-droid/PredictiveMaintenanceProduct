# Enterprise Multi-Tenant, Multi-Device, Multi-Parameter APMS Agent Platform

Industrial AI Predictive & Preventive Maintenance System engineered for disparate industrial client ecosystems.

---

## 🌟 Solution Highlights

### 1. Zero-Leak Multi-Tenant Isolation
* **Compound Identity**: Every asset, telemetry buffer, and diagnostic run is bound to `(tenant_id, asset_id)` compound keys.
* **Tenant Isolation**: Guarantees zero data leakage or state collision between divergent enterprise clients (e.g., **Propel Industries** vs **Nest Group**).
* **Pluggable CMMS**: Dispatches prescriptive actions to client-specific endpoints: **SAP PM** for heavy industries, **Webhooks/APIs** for high-tech manufacturing.

### 2. Multi-Device & Multi-Vertical Dynamic Adapters
The platform adapts dynamically across vertical machinery classes:
* **Heavy Quarry & Mining (Propel Industries)**:
  * Equipment: Jaw Crushers, Vibrating Screens, Slurry Pumps.
  * Physics: ISO 10816-6 severity evaluation, bearing thermal alarms, hydraulic clamping pressure monitoring, Weibull RUL countdown.
* **High-Tech Electronics & Cleanrooms (Nest Group)**:
  * Equipment: SMT Pick & Place Modular Mounters, Cleanroom Chillers, Reflow Ovens.
  * Physics: Pick nozzle vacuum decay detection, placement cycle time drift, linear servo motor torque friction analysis.

### 3. Dynamic Telemetry & Semantic Ingestion (Multi-Parameter)
Clients ingest arbitrary key-value telemetry payloads:
```json
{
  "tenant_id": "prop_industries",
  "asset_id": "jaw_crusher_01",
  "signals": {
    "drive_vib_de_rms": 14.8,
    "temp_bearing_c": 96.5,
    "motor_kw": 115.0,
    "hydraulic_toggle_bar": 115.0
  }
}
```
The **Asset Digital Twin** maps client parameter keys to standardized diagnostic roles (`primary_vibration`, `bearing_temp`, `power_load`, `nozzle_vacuum`, etc.).

---

## 🚀 Quick Start Guide

### 1. Run Unit Tests (100% Passing)
```bash
./venv/bin/python -m pytest tests/ -v
```

### 2. Start the Production FastAPI Server
```bash
./run.sh
```
* Interactive Swagger Docs: `http://localhost:8008/docs`
* Health Check: `http://localhost:8008/health`

### 3. Run Multi-Tenant Simulations via API
Trigger live pipeline simulations directly through HTTP:
```bash
# Propel Mining Crusher Simulation
curl -X POST http://localhost:8008/api/v1/simulation/propel_crusher

# Nest High-Tech SMT Simulation
curl -X POST http://localhost:8008/api/v1/simulation/nest_smt
```

---

## 🔌 API Endpoints Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Server health check. |
| `GET` | `/api/v1/tenants` | List all registered tenants. |
| `POST` | `/api/v1/tenants` | Register a new client tenant. |
| `GET` | `/api/v1/tenants/{tenant_id}/assets` | List digital twins for a tenant. |
| `POST` | `/api/v1/tenants/{tenant_id}/assets` | Register an asset digital twin with custom signal mappings. |
| `POST` | `/api/v1/telemetry/ingest` | Ingest dynamic telemetry packet and run LangGraph agents in real time. |
| `GET` | `/api/v1/tenants/{tenant_id}/assets/{asset_id}/history` | Fetch telemetry tail from isolated buffer. |
| `POST` | `/api/v1/simulation/propel_crusher` | Run live simulation for Propel mining machinery. |
| `POST` | `/api/v1/simulation/nest_smt` | Run live simulation for Nest SMT line. |
| `POST` | `/api/v1/simulation/cross_tenant_isolation_test` | Verify zero cross-talk between tenants. |
