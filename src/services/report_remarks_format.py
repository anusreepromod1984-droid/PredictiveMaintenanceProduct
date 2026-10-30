"""
Professional formatting for plant report Remarks (enterprise / APM-style).
Human units, KPI authority, disclaimer stripping, finding priority labels.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

_MS_IN_MIN = 60_000.0
_MS_IN_HOUR = 3_600_000.0
_MS_IN_DAY = 86_400_000.0

_DISCLAIMER_PATTERNS = (
    r"written by the ai service.*",
    r"check important findings against the figures.*",
    r"ai[- ]generated.*",
    r"verify against the figures.*",
)

_PRIORITY_HEAD = re.compile(r"^Priority\s*([123])\s*[—–\-:\.]+\s*", re.I)


def format_duration_ms(ms: Optional[Union[int, float]]) -> Optional[str]:
    if ms is None:
        return None
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    if v < _MS_IN_MIN:
        return f"{v / 1000:.1f} s"
    if v < _MS_IN_HOUR:
        return f"{v / _MS_IN_MIN:.1f} min"
    if v < _MS_IN_DAY:
        return f"{v / _MS_IN_HOUR:.1f} h"
    return f"{v / _MS_IN_DAY:.1f} days"


def format_duration_value(value: Any, key_hint: str = "") -> Any:
    """Convert likely millisecond fields to human strings; leave other values."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    key_l = key_hint.lower()
    if any(x in key_l for x in ("ms", "milli", "duration", "idle", "outage", "offline", "online", "elapsed")):
        if float(value) > 500:  # treat as ms if large or key hints ms
            human = format_duration_ms(float(value))
            if human:
                return human
    return value


def _humanize_obj(obj: Any, key: str = "") -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = format_duration_value(v, f"{key}.{k}")
            elif isinstance(v, (dict, list)):
                out[k] = _humanize_obj(v, k)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return [_humanize_obj(x, key) for x in obj[:30]]
    return obj


def format_period_display(
    start: Optional[str],
    end: Optional[str],
    timezone_name: Optional[str],
    period_label: Optional[str],
) -> str:
    def _fmt(iso: Optional[str]) -> Optional[str]:
        if not iso:
            return None
        try:
            dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y %H:%M UTC")
        except Exception:
            return str(iso)[:19]

    s, e = _fmt(start), _fmt(end)
    tz = f" ({timezone_name})" if timezone_name else ""
    if s and e:
        return f"{s} – {e}{tz}"
    if period_label:
        return f"{period_label}{tz}"
    return "selected period"


def build_operational_brief(context: Dict[str, Any]) -> Dict[str, Any]:
    """Structured brief for LLM — durations pre-humanized, KPIs marked authoritative."""
    period = context.get("period") or {}
    kpis = context.get("reportKpis") or []
    enrichment = _humanize_obj(context.get("upstreamEnrichment") or [])

    brief: Dict[str, Any] = {
        "reportTitle": context.get("title"),
        "reportType": context.get("reportType"),
        "reportingPeriod": format_period_display(
            period.get("start"),
            period.get("end"),
            period.get("timezone"),
            period.get("label"),
        ),
        "authoritativeKpis": kpis,
        "kpiNote": "Use authoritativeKpis for period totals (alerts, online %, past-limit counts).",
        "telemetrySnapshot": _humanize_obj(context.get("telemetrySnapshot") or {}),
        "operationalMetrics": enrichment,
        "formattingRules": [
            "Express durations in min, h, or days — never raw milliseconds.",
            "Align limit breaches with KPI hints and section 03 findings where provided.",
            "Use Priority 1 / 2 / 3 prefixes on finding bullets (1 = immediate).",
        ],
    }
    return brief


def _replace_ms_in_text(text: str) -> str:
    """Replace patterns like '2,097,702 ms' or '142785819 ms' with human duration."""

    def _sub(m: re.Match) -> str:
        raw = m.group(1).replace(",", "")
        try:
            ms = float(raw)
        except ValueError:
            return m.group(0)
        human = format_duration_ms(ms)
        return human or m.group(0)

    def _sub_million(m: re.Match) -> str:
        try:
            ms = float(m.group(1)) * 1_000_000.0
        except ValueError:
            return m.group(0)
        human = format_duration_ms(ms)
        return human or m.group(0)

    text = re.sub(
        r"~?\s*([\d.]+)\s*million\s*ms\b",
        _sub_million,
        text,
        flags=re.I,
    )
    text = re.sub(r"([\d,]+)\s*ms\b", _sub, text, flags=re.I)
    text = re.sub(
        r"\(\s*≈?\s*([\d,]+)\s*ms\s*\)",
        lambda m: f"({format_duration_ms(float(m.group(1).replace(',', ''))) or m.group(1)})",
        text,
    )
    return text


def strip_disclaimers(text: str) -> str:
    lines = []
    for line in text.splitlines():
        low = line.strip().lower()
        if any(re.search(p, low) for p in _DISCLAIMER_PATTERNS):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _dedupe_priority_prefix(bullet: str) -> Tuple[Optional[int], str]:
    """Strip chained 'Priority N —' prefixes; keep the first level only."""
    s = bullet.strip()
    levels: List[int] = []
    while True:
        m = _PRIORITY_HEAD.match(s)
        if not m:
            break
        levels.append(int(m.group(1)))
        s = s[m.end() :].strip()
    return (levels[0] if levels else None), s


def _is_disclaimer_only(text: str) -> bool:
    low = text.strip().lower()
    if not low:
        return True
    return any(re.search(p, low) for p in _DISCLAIMER_PATTERNS)


def normalize_finding_bullet(bullet: str, index: int) -> str:
    b = bullet.strip()
    b = _replace_ms_in_text(b)
    b = strip_disclaimers(b)
    if not b or _is_disclaimer_only(b):
        return ""
    level, body = _dedupe_priority_prefix(b)
    if not body:
        return ""
    if level is None:
        if index == 0:
            level = 1
        elif index <= 2:
            level = 2
        else:
            level = 3
    return f"Priority {level} — {body}"


def polish_remarks_output(
    summary: str,
    findings: List[str],
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    summary = strip_disclaimers(_replace_ms_in_text(summary.strip()))
    summary = re.sub(r"\s+", " ", summary)

    polished: List[str] = []
    for i, f in enumerate(findings):
        line = normalize_finding_bullet(f, i)
        if line and line not in polished:
            polished.append(line)
    if not polished:
        polished = [
            "Priority 2 — Review alert history and confirm sensor readings with a handheld instrument."
        ]

    return summary, polished[:12]


def enrich_context_for_professional_output(context: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap context sent to LLM with operationalBrief + style guide."""
    return {
        **context,
        "operationalBrief": build_operational_brief(context),
        "styleGuide": {
            "audience": "Plant manager and reliability engineer",
            "tone": "Formal, concise, decision-oriented (APM / CMMS executive summary)",
            "summaryStructure": (
                "Single cohesive executive paragraph: condition → key risk → connectivity/uptime → "
                "alert pattern → recommended focus before next shift."
            ),
            "findingStructure": "Each finding exactly one prefix: Priority N — then text (never repeat Priority twice).",
            "forbidden": ["milliseconds", "ms ", "AI disclaimers", "agent codenames", "duplicate Priority prefixes"],
        },
    }
