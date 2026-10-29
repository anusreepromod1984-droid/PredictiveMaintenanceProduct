"""
Groq LLM enrichment for the dashboard AI Assistant (fault-assistant card).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.config import settings
from src.services.llm_client import chat_completions_json
from src.utils.logger import get_logger

logger = get_logger("Services.FaultAssistantLLM")

_SYSTEM_PROMPT = """You are an industrial reliability assistant on a machine monitoring dashboard.
Use ONLY facts from the JSON context (diagnosis, alert breaches, telemetry, optional plant report excerpt).
Never invent readings, thresholds, or machine names.
Do not mention Alpha, Beta, Gamma, Delta, LangGraph, or internal agent pipelines.
Write clear, operator-friendly language suitable for a side-panel AI Assistant card.
Respond with a single JSON object only:
{
  "headline": "<short title, max 120 chars>",
  "summary": "<2-4 sentences or short bullet lines using \\n between lines>",
  "recommendedActions": ["<action 1>", "..."],
  "faultExplanation": {
    "whatIsIt": "<plain description>",
    "rootCause": "<likely cause from facts only>",
    "riskImpact": "<CRITICAL|WARNING|good or short impact phrase>"
  }
}
Keep recommendedActions between 2 and 5 items, ordered by urgency.
If severity in context is good/idle, tone should be calm; if critical, be direct but not alarmist."""


def _assistant_llm_on() -> bool:
    return settings.assistant_llm_enabled


def build_assistant_context(
    *,
    machine_id: str,
    diagnosis: Dict[str, Any],
    breaches: Optional[List[Dict[str, Any]]] = None,
    telemetry: Optional[Dict[str, Any]] = None,
    report_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "machineId": machine_id,
        "severity": diagnosis.get("severity"),
        "archetype": diagnosis.get("archetype"),
        "baselineHeadline": diagnosis.get("headline"),
        "alertBreaches": breaches or [],
        "telemetry": telemetry or {},
        "deterministicSummary": diagnosis.get("summary"),
        "deterministicActions": diagnosis.get("recommendedActions") or [],
        "pipelineSnapshot": {
            "defectCode": (diagnosis.get("pipelineDetails") or {}).get("defect_localization"),
            "rulDays": ((diagnosis.get("pipelineDetails") or {}).get("rul_prediction") or {}).get("rul_days"),
        },
        "plantReportExcerpt": report_summary,
    }


def enrich_fault_assistant(
    diagnosis: Dict[str, Any],
    *,
    machine_id: str,
    breaches: Optional[List[Dict[str, Any]]] = None,
    telemetry: Optional[Dict[str, Any]] = None,
    report_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return diagnosis dict, optionally LLM-enriched for headline/summary/actions."""
    from src.services.agent_analyst_assist import merge_analyst_into_card

    if settings.ASSISTANT_USE_PIPELINE_ANALYST and (
        diagnosis.get("analystAssist") or diagnosis.get("analyst_assist")
    ):
        return merge_analyst_into_card(diagnosis)

    if not _assistant_llm_on():
        return diagnosis

    context = build_assistant_context(
        machine_id=machine_id,
        diagnosis=diagnosis,
        breaches=breaches,
        telemetry=telemetry,
        report_summary=report_summary,
    )
    try:
        llm = chat_completions_json(system_prompt=_SYSTEM_PROMPT, user_payload=context, max_tokens=800)
    except Exception as e:
        logger.warning(f"Fault assistant LLM enrichment failed ({e}); using deterministic card")
        return diagnosis

    out = dict(diagnosis)
    headline = llm.get("headline")
    if isinstance(headline, str) and headline.strip():
        out["headline"] = headline.strip()[:200]

    summary = llm.get("summary")
    if isinstance(summary, str) and summary.strip():
        out["summary"] = summary.strip()

    actions = llm.get("recommendedActions")
    if isinstance(actions, list):
        cleaned = [str(a).strip() for a in actions if a and str(a).strip()]
        if cleaned:
            out["recommendedActions"] = cleaned[:6]

    expl = llm.get("faultExplanation")
    if isinstance(expl, dict) and any(expl.get(k) for k in ("whatIsIt", "rootCause", "riskImpact")):
        prev = out.get("faultExplanation") if isinstance(out.get("faultExplanation"), dict) else {}
        out["faultExplanation"] = {
            "whatIsIt": str(expl.get("whatIsIt") or prev.get("whatIsIt") or out.get("headline") or ""),
            "rootCause": str(expl.get("rootCause") or prev.get("rootCause") or ""),
            "riskImpact": str(expl.get("riskImpact") or prev.get("riskImpact") or out.get("severity") or ""),
        }

    return out
