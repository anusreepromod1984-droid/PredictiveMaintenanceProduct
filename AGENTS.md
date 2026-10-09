# Multi-Tenant APMS Product Agent Context

This repository is the production **Multi-Tenant, Multi-Device, Multi-Parameter Predictive Maintenance System**.

## Key Guidelines:
- Never assume single-tenant IDs. Always index buffers, caches, and database entries with compound keys `(tenant_id, asset_id)` via `make_tenant_key()`.
- Telemetry ingestion is polymorphic (`IngestTelemetryPacket.signals: Dict[str, float]`). Never hardcode physical sensor parameter names into Pydantic models.
- Support multi-verticals (Heavy Mining e.g. Propel vs High-Tech Electronics e.g. Nest Group). Use `twin.signal_map` to resolve semantic roles.
- Run tests before any change: `PYTHONPATH=. ./venv/bin/python -m pytest tests/ -v`.
- Read `CONVERSATION_HANDOFF.md` for full background and architectural history.
