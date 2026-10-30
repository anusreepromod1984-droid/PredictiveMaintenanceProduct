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
from src.services.report_remarks_format import (
    enrich_context_for_professional_output,
    polish_remarks_output,
)
from src.utils.logger import get_logger

logger = get_logger("Services.ReportLLM")

_SYSTEM_PROMPT = """You write the Remarks section for an enterprise predictive maintenance / APM fleet report
(Siemens MindSphere, GE APM, SKF @ptitude-style executive summary).

Use ONLY facts from operationalBrief and authoritativeKpis in the JSON context.
authoritativeKpis override telemetry for period totals (alert counts, online %, past-limit counts).
Never invent metrics, dates, or machine names. Never use raw milliseconds — use min, h, or days only.
Do not mention AI, agents, Alpha/Beta/Gamma/Delta, or internal pipelines.
Do not add disclaimers ("written by AI", "verify against figures").

Respond with a single JSON object only:
{"summary": "<one executive paragraph, 120-200 words, formal tone>", "findings": ["<bullet>", "..."]}

Summary (one paragraph): reporting period (from reportingPeriod), fleet/asset condition, uptime/connectivity
vs current online state, dominant alert/parameter pattern, operational implication, focus before next shift.

Findings: 5-10 bullets. Start each with "Priority 1 —", "Priority 2 —", or "Priority 3 —" (1=immediate).
Include limit/threshold values when known. Reference past-limit KPIs and parameterTrends max/avg.
Explain high period alert counts vs snapshot alert count when both exist.
If data is missing, state the gap — do not guess."""


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


def _machine_enrichment_blocks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for machine in _machine_rows(payload):
        mid = str(machine.get("machineId") or machine.get("machine_id") or "unknown")
        block: Dict[str, Any] = {"machineId": mid}
        for key in ("summary", "activity", "uptime", "utilisation", "alertHistory"):
            val = machine.get(key)
            if val is not None:
                if isinstance(val, list):
                    block[key] = val[:25]
                elif isinstance(val, dict):
                    block[key] = val
                else:
                    block[key] = val
        if len(block) > 1:
            blocks.append(block)
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
        "upstreamEnrichment": _machine_enrichment_blocks(upstream_payload),
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
    return summary.strip(), bullets[:12]


def synthesize_with_llm(context: Dict[str, Any]) -> Dict[str, Any]:
    llm_context = enrich_context_for_professional_output(context)
    parsed = chat_completions_json(
        system_prompt=_SYSTEM_PROMPT,
        user_payload=llm_context,
        max_tokens=1600,
        temperature=0.35,
    )
    summary, findings = _normalize_llm_result(parsed)
    summary, findings = polish_remarks_output(summary, findings, context)
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
    period_info = context.get("period") or {}
    period = period_info.get("label") or "the selected period"
    tz = period_info.get("timezone")
    start = period_info.get("start")
    end = period_info.get("end")
    kpis = context.get("reportKpis") or []
    snap = context.get("telemetrySnapshot") or {}
    machines = snap.get("machines") or []
    n_machines = len(context.get("machineIds") or []) or len(machines)

    alerts_kpi = _kpi_value(kpis, "alert")
    online_kpi = _kpi_value(kpis, "online")
    avg_online = _kpi_value(kpis, "average online") or _kpi_value(kpis, "avg online")
    past_limit = _kpi_value(kpis, "past") or _kpi_value(kpis, "limit")
    open_alerts = _kpi_value(kpis, "open") or _kpi_value(kpis, "still open")

    findings: List[str] = []
    for m in machines:
        mid = m.get("machineId") or "asset"
        state = m.get("connectionState")
        if state:
            findings.append(f"{mid}: connection/status — {state}.")
        for a in m.get("alertsInSnapshot") or []:
            param = a.get("parameter") or a.get("message") or "threshold"
            val = a.get("value")
            sev = a.get("severity") or "alert"
            suffix = f" (reading {val})" if val is not None else ""
            findings.append(f"{mid}: {param} — {sev}{suffix} — treat as priority inspection if still active.")

        for t in m.get("parameterTrends") or []:
            pname = t.get("parameter")
            avg = t.get("avg")
            mn = t.get("min")
            mx = t.get("max")
            samples = t.get("samples")
            if not pname or mx is None:
                continue
            pname_l = str(pname).lower()
            if any(x in pname_l for x in ("vibration", "fault", "rpm", "motor", "pressure")):
                findings.append(
                    f"{mid}: {pname} — avg {avg}, min {mn}, max {mx} ({samples} samples); "
                    f"compare peak to configured alert/limit bands in section 03."
                )

    for k in kpis:
        hint = k.get("hint")
        if hint:
            findings.append(f"Report KPI ({k.get('label')}): {k.get('value')} — {hint}.")

    if alerts_kpi:
        findings.append(
            f"Period alert volume: {alerts_kpi} events — review alert history for repeat parameters "
            f"(often vibration-related when limits are tight)."
        )
    if open_alerts:
        findings.append(f"Open alerts at report time: {open_alerts} — acknowledge and close root cause before next shift.")
    if past_limit:
        findings.append(f"Parameters past limit in period: {past_limit} — align with findings table F-01/F-02 actions.")

    if not findings:
        findings.append("No critical deviations stood out in the telemetry snapshot; continue scheduled monitoring.")

    period_window = f"{start} to {end}" if start and end else period
    summary_parts = [
        f"{title} summarises {n_machines} asset(s) over {period_window}"
        + (f" ({tz})." if tz else "."),
    ]
    if online_kpi or avg_online:
        conn = online_kpi or "see asset register"
        avg_part = f" Average online time {avg_online}." if avg_online else ""
        summary_parts.append(
            f"Connectivity: {conn} for the reporting window.{avg_part} "
            f"If online counts are low at generation time, confirm whether the asset was delayed vs truly offline."
        )
    if alerts_kpi:
        summary_parts.append(
            f"Alert load was very high ({alerts_kpi} events in the period). "
            f"This usually indicates sustained threshold crossings (often vibration.overall) rather than a single spike — "
            f"use alert history to find the first crossing and whether limits need tuning vs mechanical fault."
        )
    elif snap.get("alertsInSnapshot"):
        summary_parts.append("Latest telemetry snapshot still shows active alert conditions.")
    if past_limit:
        summary_parts.append(
            f"{past_limit} parameter(s) exceeded configured limits — prioritise mechanical inspection, "
            f"mounting, and sensor validation on those channels."
        )
    summary_parts.append(
        "Before the next shift: inspect assets with open or repeating alerts, confirm readings with a handheld instrument, "
        "and document whether limits should be adjusted after root-cause is found."
    )

    summary, findings = polish_remarks_output(" ".join(summary_parts), findings[:12], context)
    return {
        "summary": summary,
        "findings": findings,
        "source": "rules",
    }


def synthesize_report_insights(context: Dict[str, Any]) -> Dict[str, Any]:
    if _llm_configured():
        try:
            return synthesize_with_llm(context)
        except Exception as e:
            logger.warning(f"Report LLM synthesis failed ({e}); using rules fallback")
    return synthesize_with_rules(context)
