"""
Persistent Industrial Historian - Tiered Time-Series Storage
=============================================================
Production Design:
  - Hot Tier  (last 7 days):   SQLite on local disk (dev) | TimescaleDB / ClickHouse (prod)
  - Cold Tier (long-term):     Parquet on S3/GCS via Apache Iceberg (plug-in via ETL job)

The SQL schema is designed for zero-effort migration to TimescaleDB by swapping
the DSN and calling `SELECT create_hypertable('telemetry_events', 'ts_epoch')`.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Dict, Any, List, Optional
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("DB.Historian")

# Default DB path – overridden in production via $HISTORIAN_DB_PATH
_DEFAULT_DB_PATH = Path("./data/apms_historian.db")


class IndustrialHistorian:
    """
    Write-through persistent historian.
    Exposes the same interface as MultiTenantRingBuffer so callers can swap
    without changing business logic.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path or _DEFAULT_DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()
        logger.info(f"Historian initialized at {self._db_path.resolve()}")

    # ------------------------------------------------------------------
    # Schema Bootstrap
    # ------------------------------------------------------------------
    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA cache_size=-16000;

                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id   TEXT    NOT NULL,
                    asset_id    TEXT    NOT NULL,
                    ts_epoch    REAL    NOT NULL,
                    received_at TEXT    NOT NULL,
                    signals     TEXT    NOT NULL,   -- JSON blob
                    metadata    TEXT    DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_te_tenant_asset_ts
                    ON telemetry_events(tenant_id, asset_id, ts_epoch DESC);

                CREATE TABLE IF NOT EXISTS diagnostic_results (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id       TEXT    NOT NULL,
                    asset_id        TEXT    NOT NULL,
                    trace_id        TEXT    NOT NULL UNIQUE,
                    ts_epoch        REAL    NOT NULL,
                    received_at     TEXT    NOT NULL,
                    health_score    REAL,
                    health_status   TEXT,
                    operating_regime TEXT,
                    faults          TEXT    DEFAULT '[]',  -- JSON
                    prognostics     TEXT    DEFAULT NULL,  -- JSON
                    prescriptive    TEXT    DEFAULT NULL,  -- JSON
                    full_result     TEXT    NOT NULL       -- full JSON
                );
                CREATE INDEX IF NOT EXISTS idx_dr_tenant_asset_ts
                    ON diagnostic_results(tenant_id, asset_id, ts_epoch DESC);

                CREATE TABLE IF NOT EXISTS asset_health_kpi (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id           TEXT NOT NULL,
                    asset_id            TEXT NOT NULL,
                    date_bucket         TEXT NOT NULL,     -- YYYY-MM-DD
                    min_health_score    REAL,
                    max_health_score    REAL,
                    avg_health_score    REAL,
                    fault_count         INTEGER DEFAULT 0,
                    critical_count      INTEGER DEFAULT 0,
                    UNIQUE(tenant_id, asset_id, date_bucket)
                );
            """)
            conn.commit()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Telemetry Writes
    # ------------------------------------------------------------------
    def persist_telemetry(
        self,
        tenant_id: str,
        asset_id: str,
        packet: Dict[str, Any]
    ) -> None:
        ts = packet.get("timestamp", time.time())
        received_at = packet.get("received_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        signals = json.dumps(packet.get("signals", {}))
        meta = json.dumps({k: v for k, v in packet.items() if k not in ("signals", "tenant_id", "asset_id", "timestamp", "received_at", "waveform")})
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO telemetry_events
                       (tenant_id, asset_id, ts_epoch, received_at, signals, metadata)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (tenant_id.lower(), asset_id.lower(), ts, received_at, signals, meta)
                )
                conn.commit()

    def get_telemetry_tail(
        self,
        tenant_id: str,
        asset_id: str,
        limit: int = 100,
        since_epoch: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        query = """SELECT ts_epoch, received_at, signals, metadata
                   FROM telemetry_events
                   WHERE tenant_id=? AND asset_id=?"""
        params: list = [tenant_id.lower(), asset_id.lower()]
        if since_epoch:
            query += " AND ts_epoch > ?"
            params.append(since_epoch)
        query += " ORDER BY ts_epoch DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(query, params).fetchall()
        result = []
        for r in reversed(rows):
            rec = {"timestamp": r["ts_epoch"], "received_at": r["received_at"]}
            rec["signals"] = json.loads(r["signals"])
            rec.update(json.loads(r["metadata"] or "{}"))
            result.append(rec)
        return result

    # ------------------------------------------------------------------
    # Diagnostic Writes
    # ------------------------------------------------------------------
    def persist_diagnostic(self, tenant_id: str, asset_id: str, result: Dict[str, Any]) -> None:
        ts = result.get("timestamp", time.time())
        received_at = result.get("iso_timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        faults = json.dumps(result.get("active_faults", []))
        prognostics = json.dumps(result.get("prognostics")) if result.get("prognostics") else None
        prescriptive = json.dumps(result.get("prescriptive_action")) if result.get("prescriptive_action") else None
        full_result = json.dumps(result)
        trace_id = result.get("trace_id", f"apms_{int(ts)}")
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO diagnostic_results
                       (tenant_id, asset_id, trace_id, ts_epoch, received_at,
                        health_score, health_status, operating_regime,
                        faults, prognostics, prescriptive, full_result)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tenant_id.lower(), asset_id.lower(), trace_id,
                        ts, received_at,
                        result.get("health_score"), result.get("health_status"),
                        result.get("operating_regime"),
                        faults, prognostics, prescriptive, full_result
                    )
                )
                conn.commit()
        # Roll up KPI bucket
        self._upsert_kpi(tenant_id, asset_id, ts, result)

    def _upsert_kpi(self, tenant_id: str, asset_id: str, ts_epoch: float, result: Dict[str, Any]):
        import datetime
        date_bucket = datetime.datetime.fromtimestamp(ts_epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        hs = result.get("health_score", 100.0)
        faults = result.get("active_faults", [])
        critical_count = sum(1 for f in faults if isinstance(f, dict) and f.get("severity") == "CRITICAL")
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO asset_health_kpi
                        (tenant_id, asset_id, date_bucket, min_health_score, max_health_score,
                         avg_health_score, fault_count, critical_count)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(tenant_id, asset_id, date_bucket) DO UPDATE SET
                        min_health_score = MIN(min_health_score, excluded.min_health_score),
                        max_health_score = MAX(max_health_score, excluded.max_health_score),
                        avg_health_score = (avg_health_score + excluded.avg_health_score) / 2.0,
                        fault_count      = fault_count + excluded.fault_count,
                        critical_count   = critical_count + excluded.critical_count
                """, (tenant_id.lower(), asset_id.lower(), date_bucket, hs, hs, hs,
                      len(faults), critical_count))
                conn.commit()

    def get_latest_diagnostic(self, tenant_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """SELECT full_result FROM diagnostic_results
                       WHERE tenant_id=? AND asset_id=? ORDER BY ts_epoch DESC LIMIT 1""",
                    (tenant_id.lower(), asset_id.lower())
                ).fetchone()
        return json.loads(row["full_result"]) if row else None

    def get_diagnostic_history(
        self,
        tenant_id: str,
        asset_id: str,
        limit: int = 100,
        since_epoch: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        query = """SELECT full_result FROM diagnostic_results
                   WHERE tenant_id=? AND asset_id=?"""
        params: list = [tenant_id.lower(), asset_id.lower()]
        if since_epoch:
            query += " AND ts_epoch > ?"
            params.append(since_epoch)
        query += " ORDER BY ts_epoch DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(query, params).fetchall()
        return [json.loads(r["full_result"]) for r in reversed(rows)]

    def get_kpi_summary(self, tenant_id: str, asset_id: str, days: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    """SELECT date_bucket, min_health_score, max_health_score,
                              avg_health_score, fault_count, critical_count
                       FROM asset_health_kpi
                       WHERE tenant_id=? AND asset_id=?
                       ORDER BY date_bucket DESC LIMIT ?""",
                    (tenant_id.lower(), asset_id.lower(), days)
                ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def list_assets_for_tenant(self, tenant_id: str) -> List[str]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT asset_id FROM telemetry_events WHERE tenant_id=? ORDER BY asset_id",
                    (tenant_id.lower(),)
                ).fetchall()
        return [r["asset_id"] for r in rows]

    def vacuum(self, tenant_id: str, asset_id: str, retain_days: int = 90) -> int:
        """Prune telemetry older than retain_days from hot tier (cold tier handles archival separately)."""
        cutoff = time.time() - (retain_days * 86400)
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM telemetry_events WHERE tenant_id=? AND asset_id=? AND ts_epoch < ?",
                    (tenant_id.lower(), asset_id.lower(), cutoff)
                )
                conn.commit()
        return cur.rowcount


# Singleton
platform_historian = IndustrialHistorian()
