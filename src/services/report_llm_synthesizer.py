"""
LLM-backed plant report Remarks (summary + findings) from structured report context.
Falls back to rule-based prose when LLM is unavailable.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from src.config import settings
from src.services.llm_client import chat_completions_json, extract_json_object
from src.services.fleet_query_service import _machine_rows
from src.services.plant_report_summarizer import _duration_from_date_range
from src.utils.logger import get_logger

logger = get_logger("Services.ReportLLM")

_SYSTEM_PROMPT = """You write the Remarks section for an industrial predictive maintenance report.
Use ONLY facts from the provided JSON context. Never invent metrics, dates, or machine names.
Do not mention AI agents, Alpha, Beta, Gamma, Delta, LangGraph, or internal software pipelines.
Write for plant managers and reliability engineers: clear, concise, actionable prose.
Respond with a single JSON object only (no markdown code fences):
{"summary": "<one paragraph, 80-160 words>", "findings": ["<bullet>", "..."]}
The summary should state overall fleet/asset condition, the main risks, and what to prioritize first.
Findings: 3 to 7 bullets, highest priority first; each bullet is one concrete observation or action.
If alert counts in KPIs differ from snapshot alerts, explain both without guessing causes."""


def _llm_configured() -> bool:
    return settings.report_llm_enabled


def _kpi_list(kpis: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not kpis:
        return out
    for k in kpis:
        if not isinstance(k, dict):
            continue
        label = str(k.get("label") or k.get("name") or k.get("id") or "KPI")
        val = k.get("value")
        if val is None:
            continue
        hint = k.get("hint")
        out.append({"label": label, "value": str(val), **({"hint": str(hint)} if hint else {})})
    return out


def _machine_context_blocks(
    payload: Dict[str, Any],
    selected_parameters: Optional[List[str]],
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for machine in _machine_rows(payload):
        mid = str(machine.get("machineId") or machine.get("machine_id") or "unknown")
        status = machine.get("status") if isinstance(machine.get("status"), dict) else {}
        alerts_raw = machine.get("alerts") if isinstance(machine.get("alerts"), list) else []
        alert_items: List[Dict[str, Any]] = []
        for a in alerts_raw[:15]:
            if not isinstance(a, dict):
                continue
            alert_items.append(
                {
                    "severity": a.get("severity") or a.get("level"),
                    "parameter": a.get("parameter") or a.get("paramName") or a.get("message"),
                    "value": a.get("value"),
                    "message": a.get("message"),
                }
            )

        parameter_highlights: List[Dict[str, Any]] = []
        history = machine.get("history") if isinstance(machine.get("history"), dict) else {}
        rows = history.get("rows") or []
        params = selected_parameters or []
        if not params and rows and isinstance(rows[-1], dict):
            params = [
                k
                for k, v in rows[-1].items()
                if k not in ("timestamp", "ts", "machineId", "machine_id") and isinstance(v, (int, float))
            ][:5]

        for param in params:
            vals = [
                float(r[param])
                for r in rows
                if isinstance(r, dict) and isinstance(r.get(param), (int, float)) and not isinstance(r.get(param), bool)
            ]
            if len(vals) < 2:
                continue
            parameter_highlights.append(
                {
                    "parameter": param,
                    "samples": len(vals),
                    "avg": round(sum(vals) / len(vals), 4),
                    "min": round(min(vals), 4),
                    "max": round(max(vals), 4),
                }
            )

        latest = machine.get("latest") if isinstance(machine.get("latest"), dict) else {}
        latest_clean = {
            k: v
            for k, v in list(latest.items())[:8]
            if k not in ("timestamp", "ts") and isinstance(v, (int, float, str))
        }

        blocks.append(
            {
                "machineId": mid,
                "connectionState": status.get("state") or status.get("connection") or status.get("label"),
                "alertsInSnapshot": alert_items,
                "parameterTrends": parameter_highlights,
                "latestReadings": latest_clean,
            }
        )
    return blocks


def build_report_context(
    *,
    title: str,
    report_type: Optional[str],
    company: Optional[str],
    machine_ids: List[str],
    date_range: Optional[Dict[str, Any]],
    timezone_name: Optional[str],
    selected_parameters: Optional[List[str]],
    kpis: Optional[List[Dict[str, Any]]],
    upstream_payload: Dict[str, Any],
) -> Dict[str, Any]:
    period_label = _duration_from_date_range(date_range, timezone_name)
    machines = _machine_context_blocks(upstream_payload, selected_parameters)
    snapshot_alert_count = sum(len(m.get("alertsInSnapshot") or []) for m in machines)

    return {
        "title": title,
        "reportType": report_type,
        "company": company,
        "machineIds": machine_ids,
        "period": {
            "label": period_label,
            "start": (date_range or {}).get("start") or (date_range or {}).get("from"),
            "end": (date_range or {}).get("end") or (date_range or {}).get("to"),
            "timezone": timezone_name,
        },
        "reportKpis": _kpi_list(kpis),
        "telemetrySnapshot": {
            "machinesReturned": len(machines),
            "alertsInSnapshot": snapshot_alert_count,
            "machines": machines,
        },
    }


def _normalize_llm_result(data: Dict[str, Any]) -> Tuple[str, List[str]]:
    summary = data.get("summary")
    findings = data.get("findings")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("LLM response missing summary string")
    if not isinstance(findings, list):
        findings = []
    bullets = [str(f).strip() for f in findings if f and str(f).strip()]
    if not bullets:
        bullets = ["Review alert history and confirm sensor readings against physical inspection."]
    return summary.strip(), bullets[:8]


def synthesize_with_llm(context: Dict[str, Any]) -> Dict[str, Any]:
    parsed = chat_completions_json(system_prompt=_SYSTEM_PROMPT, user_payload=context)
    summary, findings = _normalize_llm_result(parsed)
    return {"summary": summary, "findings": findings, "source": "llm"}


def _kpi_value(kpis: List[Dict[str, str]], label_substr: str) -> Optional[str]:
    needle = label_substr.lower()
    for k in kpis:
        if needle in k.get("label", "").lower():
            return k.get("value")
    return None


def synthesize_with_rules(context: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic fallback — no agent names, prioritizes KPIs and alerts."""
    title = context.get("title") or "Plant report"
    period = (context.get("period") or {}).get("label") or "the selected period"
    tz = (context.get("period") or {}).get("timezone")
    kpis = context.get("reportKpis") or []
    snap = context.get("telemetrySnapshot") or {}
    machines = snap.get("machines") or []
    n_machines = len(context.get("machineIds") or []) or len(machines)

    alerts_kpi = _kpi_value(kpis, "alert")
    online_kpi = _kpi_value(kpis, "online")
    past_limit = _kpi_value(kpis, "past") or _kpi_value(kpis, "limit")

    findings: List[str] = []
    for m in machines:
        mid = m.get("machineId") or "asset"
        for a in m.get("alertsInSnapshot") or []:
            param = a.get("parameter") or a.get("message") or "threshold"
            val = a.get("value")
            sev = a.get("severity") or "alert"
            suffix = f" (reading {val})" if val is not None else ""
            findings.append(f"{mid}: {param} — {sev}{suffix}")

    for m in machines:
        mid = m.get("machineId") or "asset"
        for t in m.get("parameterTrends") or []:
            pname = t.get("parameter")
            mx = t.get("max")
            if pname and mx is not None and any(x in str(pname).lower() for x in ("vibration", "fault", "rpm")):
                findings.append(
                    f"{mid}: {pname} peaked at {mx} over {t.get('samples')} samples in the window."
                )

    if alerts_kpi:
        findings.append(f"Report period recorded {alerts_kpi} alert events in total (see alert history for breakdown).")
    if past_limit:
        findings.append(f"{past_limit} parameter(s) exceeded configured limits during the period.")

    if not findings:
        findings.append("No critical deviations stood out in the telemetry snapshot; continue scheduled monitoring.")

    summary_bits = [
        f"{title} covers {n_machines} asset(s) over {period}.",
    ]
    if online_kpi:
        summary_bits.append(f"Connectivity for the period: {online_kpi}.")
    if alerts_kpi:
        summary_bits.append(f"Alert activity was high ({alerts_kpi} events); focus on repeat parameters first.")
    elif snap.get("alertsInSnapshot"):
        summary_bits.append("Active alert conditions appear in the latest telemetry snapshot.")
    else:
        summary_bits.append("No active alerts appeared in the latest telemetry snapshot.")
    if tz:
        summary_bits.append(f"Figures align to timezone {tz}.")
    summary_bits.append(
        "Prioritize physical inspection and limit review on assets with open or repeating alerts before the next shift."
    )

    return {
        "summary": " ".join(summary_bits),
        "findings": findings[:8],
        "source": "rules",
    }


def synthesize_report_insights(context: Dict[str, Any]) -> Dict[str, Any]:
    if _llm_configured():
        try:
            return synthesize_with_llm(context)
        except Exception as e:
            logger.warning(f"Report LLM synthesis failed ({e}); using rules fallback")
    return synthesize_with_rules(context)
