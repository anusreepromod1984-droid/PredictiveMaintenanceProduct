"""
Platform Observability & Metrics
==================================
Prometheus-compatible metrics endpoint for operational monitoring.

Metrics exposed at GET /metrics (Prometheus scrape format):
  - apms_telemetry_ingested_total{tenant_id, asset_id}
  - apms_pipeline_duration_ms{tenant_id, asset_id}
  - apms_health_score_gauge{tenant_id, asset_id}
  - apms_active_faults_gauge{tenant_id, asset_id, severity}
  - apms_sbm_anomaly_index{tenant_id, asset_id}
  - apms_rul_hours_gauge{tenant_id, asset_id}
  - apms_sensor_fault_total{tenant_id, asset_id}

In production: scrape with Prometheus + visualize in Grafana.
"""

import time
import threading
from collections import defaultdict
from typing import Dict, Any, List

from src.utils.logger import get_logger

logger = get_logger("Utils.Metrics")


class APMSMetrics:
    """
    Lightweight Prometheus-format metrics collector.
    Thread-safe counters and gauges per (tenant_id, asset_id).
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._start_time = time.time()

    # -----------------------------------------------------------------------
    # Recording methods (called by orchestrator and routes)
    # -----------------------------------------------------------------------
    def record_ingestion(self, tenant_id: str, asset_id: str):
        with self._lock:
            self._counters[f"apms_telemetry_ingested_total{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] += 1
            self._counters["apms_telemetry_ingested_total"] += 1

    def record_pipeline_duration(self, tenant_id: str, asset_id: str, duration_ms: float):
        label = f"tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\""
        with self._lock:
            self._histograms[f"apms_pipeline_duration_ms{{{label}}}"].append(duration_ms)
            self._histograms["apms_pipeline_duration_ms_all"].append(duration_ms)

    def set_health_score(self, tenant_id: str, asset_id: str, score: float):
        with self._lock:
            self._gauges[f"apms_health_score_gauge{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] = score

    def set_active_faults(self, tenant_id: str, asset_id: str, fault_count: int, critical_count: int):
        with self._lock:
            self._gauges[f"apms_active_faults_gauge{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] = fault_count
            self._gauges[f"apms_critical_faults_gauge{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] = critical_count

    def set_rul_hours(self, tenant_id: str, asset_id: str, rul_hours: float):
        with self._lock:
            self._gauges[f"apms_rul_hours_gauge{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] = rul_hours

    def set_sbm_anomaly_index(self, tenant_id: str, asset_id: str, anomaly_index: float):
        with self._lock:
            self._gauges[f"apms_sbm_anomaly_index{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] = anomaly_index

    def record_sensor_fault(self, tenant_id: str, asset_id: str):
        with self._lock:
            self._counters[f"apms_sensor_fault_total{{tenant_id=\"{tenant_id}\",asset_id=\"{asset_id}\"}}"] += 1

    # -----------------------------------------------------------------------
    # Prometheus text output
    # -----------------------------------------------------------------------
    def render_prometheus(self) -> str:
        lines = [
            "# HELP apms_uptime_seconds Platform uptime in seconds",
            "# TYPE apms_uptime_seconds counter",
            f"apms_uptime_seconds {round(time.time() - self._start_time, 1)}",
            "",
        ]

        with self._lock:
            # Counters
            lines.append("# TYPE apms_telemetry_ingested_total counter")
            for key, val in self._counters.items():
                lines.append(f"{key} {val}")
            lines.append("")

            # Gauges
            lines.append("# TYPE apms_health_score_gauge gauge")
            lines.append("# TYPE apms_active_faults_gauge gauge")
            lines.append("# TYPE apms_rul_hours_gauge gauge")
            lines.append("# TYPE apms_sbm_anomaly_index gauge")
            for key, val in self._gauges.items():
                lines.append(f"{key} {val}")
            lines.append("")

            # Histograms: expose p50, p90, p99
            lines.append("# TYPE apms_pipeline_duration_ms summary")
            for key, vals in self._histograms.items():
                if vals:
                    import statistics
                    sorted_vals = sorted(vals)
                    n = len(sorted_vals)
                    p50 = sorted_vals[int(n * 0.50)]
                    p90 = sorted_vals[int(n * 0.90)]
                    p99 = sorted_vals[int(n * 0.99)]
                    base = key.replace("{", "_").replace("}", "").replace("\"", "").replace(",", "_").replace("=", "_")
                    lines.append(f"{base}_p50_ms {round(p50, 2)}")
                    lines.append(f"{base}_p90_ms {round(p90, 2)}")
                    lines.append(f"{base}_p99_ms {round(p99, 2)}")
                    lines.append(f"{base}_count {n}")

        return "\n".join(lines)

    def get_summary(self) -> Dict[str, Any]:
        """JSON summary for /health endpoint."""
        with self._lock:
            total_ingested = self._counters.get("apms_telemetry_ingested_total", 0)
            all_durations = self._histograms.get("apms_pipeline_duration_ms_all", [])
            avg_ms = round(sum(all_durations) / len(all_durations), 2) if all_durations else 0.0
        return {
            "total_telemetry_ingested": total_ingested,
            "avg_pipeline_duration_ms": avg_ms,
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }


# Singleton
platform_metrics = APMSMetrics()
