"""
Groq second-opinion analysis for Alpha→Beta→Gamma→Delta pipeline output.

Does not change health scores, fault codes, or prescriptive actions — only adds
structured narrative hints for operators and the AI Assistant card.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.config import settings
from src.services.llm_client import chat_completions_json
from src.utils.logger import get_logger

logger = get_logger("Services.AgentAnalystAssist")

_SYSTEM_PROMPT = """You assist deterministic industrial diagnostic agents (signal quality, regime, fault isolation, prescriptive).
The JSON context includes their verdicts — you must NOT override or contradict:
- health_status, health_score, fault_code, severity, rul_days, work_order_id, or prescriptive text.
Your role is a second pair of eyes: interpret patterns, suggest extra checks, and note data limitations.

Do not name internal agent codenames (Alpha, Beta, Gamma, Delta) in the user-facing text; use plain roles:
signal quality, operating regime, defect analysis, maintenance planning.

Respond with a single JSON object only:
{
  "signalQualityNotes": "<1-2 sentences>",
  "regimeNotes": "<1-2 sentences>",
  "defectNotes": "<1-2 sentences>",
  "maintenanceNotes": "<1-2 sentences>",
  "crossCheck": "<short paragraph tying the picture together for an operator>",
  "investigationLeads": ["<concrete check>", "..."],
  "cautions": ["<limitation or false-alarm risk>", "..."]
}
investigationLeads: 2-5 items. cautions: 0-3 items. Use only facts from context."""


def _agent_analyst_on() -> bool:
    return settings.agent_analyst_llm_enabled


def _trim_state(state: Optional[Dict[str, Any]], keys: List[str]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    return {k: state[k] for k in keys if k in state}


def build_pipeline_context(
    *,
    tenant_id: str,
    asset_id: str,
    signals: Dict[str, float],
    alpha: Dict[str, Any],
    beta: Dict[str, Any],
    gamma: Dict[str, Any],
    delta: Optional[Dict[str, Any]],
    result_summary: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "tenantId": tenant_id,
        "assetId": asset_id,
        "latestSignals": {k: signals[k] for k in list(signals.keys())[:20]},
        "agentVerdicts": {
            "signalQuality": _trim_state(
                alpha,
                ["can_proceed_to_physics", "issues", "quality_score", "reason"],
            ),
            "operatingRegime": _trim_state(
                beta,
                ["is_operating", "regime", "confidence", "notes"],
            ),
            "defectAnalysis": _trim_state(
                gamma,
                ["health_status", "health_score", "faults", "prognostics", "sbm_analysis"],
            ),
            "maintenancePlan": delta if isinstance(delta, dict) else {},
        },
        "consolidatedResult": result_summary,
    }


def augment_diagnostic_with_analyst(
    *,
    tenant_id: str,
    asset_id: str,
    signals: Dict[str, float],
    alpha: Dict[str, Any],
    beta: Dict[str, Any],
    gamma: Dict[str, Any],
    delta: Optional[Dict[str, Any]],
    result_summary: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return analyst_assist dict or None if disabled / failed."""
    if not _agent_analyst_on():
        return None

    context = build_pipeline_context(
        tenant_id=tenant_id,
        asset_id=asset_id,
        signals=signals,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        delta=delta,
        result_summary=result_summary,
    )
    try:
        llm = chat_completions_json(
            system_prompt=_SYSTEM_PROMPT,
            user_payload=context,
            max_tokens=700,
            timeout_s=min(float(settings.REPORT_LLM_TIMEOUT_S or 50.0), 20.0),
        )
    except Exception as e:
        logger.warning(f"Agent analyst assist failed ({e}); pipeline result unchanged")
        return None

    assist: Dict[str, Any] = {
        "source": "groq_analyst_assist",
        "signalQualityNotes": _str(llm.get("signalQualityNotes")),
        "regimeNotes": _str(llm.get("regimeNotes")),
        "defectNotes": _str(llm.get("defectNotes")),
        "maintenanceNotes": _str(llm.get("maintenanceNotes")),
        "crossCheck": _str(llm.get("crossCheck")),
        "investigationLeads": _str_list(llm.get("investigationLeads")),
        "cautions": _str_list(llm.get("cautions")),
    }
    if not assist.get("crossCheck") and not assist.get("investigationLeads"):
        return None
    return assist


def _str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _str_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if x and str(x).strip()][:6]


def merge_analyst_into_card(diagnosis: Dict[str, Any]) -> Dict[str, Any]:
    """Apply cached pipeline analyst_assist to AI Assistant card fields (no extra LLM call)."""
    assist = diagnosis.get("analystAssist") or diagnosis.get("analyst_assist")
    if not isinstance(assist, dict):
        return diagnosis

    out = dict(diagnosis)
    parts: List[str] = []
    for key in ("crossCheck", "signalQualityNotes", "regimeNotes", "defectNotes", "maintenanceNotes"):
        text = assist.get(key)
        if text:
            parts.append(str(text))

    cautions = assist.get("cautions") or []
    if isinstance(cautions, list) and cautions:
        parts.append("Cautions: " + "; ".join(str(c) for c in cautions[:3]))

    if parts:
        out["summary"] = "\n\n".join(parts)

    leads = assist.get("investigationLeads") or []
    if isinstance(leads, list) and leads:
        existing = list(out.get("recommendedActions") or [])
        for lead in leads:
            s = str(lead).strip()
            if s and s not in existing:
                existing.append(s)
        out["recommendedActions"] = existing[:8]

    if assist.get("crossCheck") and not out.get("faultExplanation"):
        out["faultExplanation"] = {
            "whatIsIt": out.get("headline") or "Condition review",
            "rootCause": str(assist.get("defectNotes") or assist.get("crossCheck"))[:500],
            "riskImpact": str(out.get("severity") or "unknown"),
        }

    return out
