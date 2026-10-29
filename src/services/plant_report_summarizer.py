"""
Build plant report { summary, findings } from upstream POST /api/ai/query payloads.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.services.fleet_query_service import _machine_rows
from src.services.threshold_provider import UpstreamFleetClient
from src.utils.logger import get_logger

logger = get_logger("Services.PlantReportSummarizer")

REPORT_QUERY_TIMEOUT_S = 55.0


def _duration_from_date_range(date_range: Optional[Dict[str, Any]], timezone_name: Optional[str]) -> str:
    if not date_range or not isinstance(date_range, dict):
        return "14d"
    start = date_range.get("start") or date_range.get("from")
    end = date_range.get("end") or date_range.get("to")
    if not start or not end:
        return str(date_range.get("duration") or "14d")
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        days = max(1, int((e - s).total_seconds() // 86400) + 1)
        return f"{days}d"
    except Exception:
        return "14d"


def _build_query_body(
    machine_ids: List[str],
    date_range: Optional[Dict[str, Any]],
    timezone_name: Optional[str],
    selected_parameters: Optional[List[str]],
) -> Dict[str, Any]:
    duration = _duration_from_date_range(date_range, timezone_name)
    body: Dict[str, Any] = {
        "machineIds": machine_ids,
        "include": ["meta", "latest", "status", "activity", "alerts", "parameters", "history"],
        "history": {
            "source": "db",
            "duration": duration,
            "maxPoints": 500,
        },
    }
    # Upstream PDM /api/ai/query accepts history.duration only (not start/end/timezone).
    if selected_parameters:
        body["fields"] = selected_parameters
    return body


def _numeric_series(rows: List[Dict[str, Any]], field: str) -> List[float]:
    out: List[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = row.get(field)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def _alert_lines(machine_id: str, alerts: Any) -> List[str]:
    lines: List[str] = []
    if not isinstance(alerts, list):
        return lines
    for a in alerts[:20]:
        if not isinstance(a, dict):
            continue
        sev = a.get("severity") or a.get("level") or "ALERT"
        msg = a.get("message") or a.get("parameter") or a.get("paramName") or "threshold"
        val = a.get("value")
        suffix = f" ({val})" if val is not None else ""
        lines.append(f"{machine_id}: [{sev}] {msg}{suffix}")
    return lines


def _summarize_machine_row(
    machine: Dict[str, Any],
    selected_parameters: Optional[List[str]],
    kpis: Optional[List[Dict[str, Any]]],
) -> Tuple[List[str], List[str]]:
    findings: List[str] = []
    mid = str(machine.get("machineId") or machine.get("machine_id") or "unknown")
    status = machine.get("status")
    if isinstance(status, dict):
        state = status.get("state") or status.get("connection") or status.get("label")
        if state:
            findings.append(f"{mid}: operational status — {state}")

    findings.extend(_alert_lines(mid, machine.get("alerts")))

    history = machine.get("history") if isinstance(machine.get("history"), dict) else {}
    rows = history.get("rows") or []
    params = selected_parameters or []
    if not params and rows:
        sample = rows[-1] if isinstance(rows[-1], dict) else {}
        params = [
            k for k, v in sample.items()
            if k not in ("timestamp", "ts", "machineId", "machine_id") and isinstance(v, (int, float))
        ][:6]

    for param in params:
        series = _numeric_series(rows, param)
        if len(series) < 2:
            continue
        findings.append(
            f"{mid}: {param} avg {statistics.mean(series):.3g}, "
            f"min {min(series):.3g}, max {max(series):.3g} over {len(series)} samples"
        )

    if isinstance(kpis, list):
        for kpi in kpis:
            if not isinstance(kpi, dict):
                continue
            label = kpi.get("label") or kpi.get("name") or kpi.get("id") or "KPI"
            val = kpi.get("value")
            if val is not None:
                findings.append(f"Report KPI — {label}: {val}")

    latest = machine.get("latest") if isinstance(machine.get("latest"), dict) else {}
    if latest:
        parts = []
        for k, v in list(latest.items())[:5]:
            if isinstance(v, (int, float)):
                parts.append(f"{k}={v:g}")
        if parts:
            findings.append(f"{mid}: latest snapshot — {', '.join(parts)}")

    return findings, params


def generate_report_summary_legacy(
    *,
    company: Optional[str],
    report_type: Optional[str],
    title: Optional[str],
    machine_ids: List[str],
    date_range: Optional[Dict[str, Any]],
    timezone_name: Optional[str],
    selected_parameters: Optional[List[str]],
    kpis: Optional[List[Dict[str, Any]]],
    upstream_client: Optional[UpstreamFleetClient] = None,
) -> Dict[str, Any]:
    client = upstream_client or UpstreamFleetClient(timeout_s=REPORT_QUERY_TIMEOUT_S)
    if not client.is_configured:
        raise ValueError("Upstream fleet API not configured (AI_SERVICE_API_KEY)")

    body = _build_query_body(machine_ids, date_range, timezone_name, selected_parameters)
    payload = client.query(body)
    if not payload:
        raise RuntimeError("Upstream POST /api/ai/query failed or returned no data")

    all_findings: List[str] = []
    machines = _machine_rows(payload)
    if not machines:
        all_findings.append("No machine blocks returned for the requested period.")

    alert_count = 0
    for m in machines:
        machine_findings, _ = _summarize_machine_row(m, selected_parameters, kpis if len(machines) == 1 else None)
        all_findings.extend(machine_findings)
        alerts = m.get("alerts")
        if isinstance(alerts, list):
            alert_count += len(alerts)

    period_label = _duration_from_date_range(date_range, timezone_name)
    org = company or "Plant"
    rtype = (report_type or "operational").replace("_", " ")
    report_title = title or f"{org} {rtype} report"

    summary_parts = [
        f"{report_title} covers {len(machine_ids)} machine(s) over {period_label}.",
        f"Upstream telemetry aggregation returned {len(machines)} machine record(s) with {alert_count} alert event(s) in scope.",
    ]
    if timezone_name:
        summary_parts.append(f"Analysis timezone: {timezone_name}.")
    if alert_count:
        summary_parts.append("Review highlighted alert findings and parameter trends before the next production shift.")
    else:
        summary_parts.append("No active alert events were reported in the selected window; continue scheduled monitoring.")

    summary = " ".join(summary_parts)
    findings = all_findings[:25] if all_findings else ["No notable deviations detected in the selected parameters."]

    return {
        "summary": summary,
        "findings": findings,
        "graph": {},
        "meta": {
            "machinesQueried": machine_ids,
            "machinesReturned": len(machines),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


def generate_report_summary(
    *,
    company: Optional[str],
    report_type: Optional[str],
    title: Optional[str],
    machine_ids: List[str],
    date_range: Optional[Dict[str, Any]],
    timezone_name: Optional[str],
    selected_parameters: Optional[List[str]],
    kpis: Optional[List[Dict[str, Any]]],
    tenant_id: Optional[str] = None,
    upstream_client: Optional[UpstreamFleetClient] = None,
) -> Dict[str, Any]:
    """Agent LangGraph report pipeline (insight graph + agent-derived summary)."""
    from src.agents.report_insight_orchestrator import report_insight_orchestrator

    period_label = _duration_from_date_range(date_range, timezone_name)
    org = company or "Plant"
    rtype = (report_type or "operational").replace("_", " ")
    report_title = title or f"{org} {rtype} report"

    meta = {
        "tenant_id": tenant_id,
        "company": company,
        "report_type": report_type,
        "title": report_title,
        "date_range": date_range,
        "timezone": timezone_name,
        "selected_parameters": selected_parameters,
        "kpis": kpis,
        "period_label": period_label,
        "machine_ids": machine_ids,
    }
    try:
        return report_insight_orchestrator.run(meta, machine_ids)
    except Exception as e:
        logger.warning(f"Agent report graph failed ({e}); falling back to legacy summarizer")
        legacy = generate_report_summary_legacy(
            company=company,
            report_type=report_type,
            title=title,
            machine_ids=machine_ids,
            date_range=date_range,
            timezone_name=timezone_name,
            selected_parameters=selected_parameters,
            kpis=kpis,
            upstream_client=upstream_client,
        )
        legacy["meta"] = legacy.get("meta") or {}
        legacy["meta"]["fallback"] = "legacy_summarizer"
        return legacy
