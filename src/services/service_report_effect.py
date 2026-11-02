"""
Explain a confirmed service using only numbers supplied in the comparison payload.
Does not recompute change percentages or claim the service caused a change.

The platform (reports + ai/query) should build comparison.window, for example:
  - before: 7 days ending the day before serviceDate
  - after: serviceDate through min(today, serviceDate + 90 days) for a post-service summary
    (e.g. upload a report from 3 months ago → ~90 days of "after" averages in beforeAfter)
APMS narrates those windows; it does not fetch telemetry.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _fmt(n: Any) -> str:
    if isinstance(n, bool) or n is None:
        return ""
    if isinstance(n, float):
        return json.dumps(n)
    if isinstance(n, int):
        return str(n)
    return str(n)


def _num_blob(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def numbers_are_grounded(text: str, payload: Dict[str, Any]) -> bool:
    blob = _num_blob(payload)
    for token in _NUM.findall(text):
        if token not in blob:
            return False
    return True


def _comparison_window_complete(readiness: Optional[str], window: Dict[str, Any]) -> bool:
    if str(readiness or "").upper() == "COMPLETE":
        return True
    collected = window.get("afterDaysCollected")
    required = window.get("afterDaysRequired")
    if isinstance(collected, int) and isinstance(required, int) and required > 0:
        return collected >= required
    return False


def _post_service_period_summary(window: Dict[str, Any], service: Dict[str, Any]) -> Optional[str]:
    """
    Human context for AI ANALYSIS: which dates the before/after averages cover.
    All dates must already appear in the request JSON (number/date grounding).
    """
    before_w = window.get("before") if isinstance(window.get("before"), dict) else {}
    after_w = window.get("after") if isinstance(window.get("after"), dict) else {}
    bs, be = before_w.get("start"), before_w.get("end")
    a_start, a_end = after_w.get("start"), after_w.get("end")
    svc = service.get("serviceDate")

    chunks: List[str] = []
    if svc:
        chunks.append(f"Service date {svc}.")
    if bs and be:
        chunks.append(f"Pre-service baseline window: {bs} through {be}.")
    if a_start and a_end:
        collected = window.get("afterDaysCollected")
        if isinstance(collected, int) and collected > 7:
            chunks.append(
                f"Post-service performance summary: telemetry averages from {a_start} through {a_end} "
                f"({collected} days after service)."
            )
        else:
            chunks.append(f"Post-service telemetry averages: {a_start} through {a_end}.")
    return " ".join(chunks) if chunks else None


def _partial_window_note(window: Dict[str, Any]) -> Optional[str]:
    collected = window.get("afterDaysCollected")
    required = window.get("afterDaysRequired")
    if not isinstance(collected, int) or not isinstance(required, int) or required <= 0:
        return None
    if collected >= required:
        return None
    return (
        f"After-service window: {collected} of {required} days collected so far; "
        f"averages may change as more telemetry arrives."
    )


def _overall_effect(rows: List[Dict[str, Any]]) -> str:
    improved = sum(1 for r in rows if r.get("verdict") == "IMPROVED")
    worse = sum(1 for r in rows if r.get("verdict") == "WORSE")
    if improved == 0 and worse == 0:
        return "NO_CHANGE"
    if improved > worse:
        return "IMPROVED"
    if worse > improved:
        return "WORSE"
    return "NO_CHANGE"


def _clip(items: List[str], n: int = 8) -> List[str]:
    out = []
    for s in items:
        s = s.strip()
        if s and s not in out:
            out.append(s[:500])
        if len(out) >= n:
            break
    return out


def build_service_effect(body: Dict[str, Any]) -> Dict[str, Any]:
    comparison = body.get("comparison") if isinstance(body.get("comparison"), dict) else {}
    service = body.get("service") if isinstance(body.get("service"), dict) else {}
    window = comparison.get("window") if isinstance(comparison.get("window"), dict) else {}
    rows = [r for r in (comparison.get("beforeAfter") or []) if isinstance(r, dict)]
    window_complete = _comparison_window_complete(comparison.get("readiness"), window)
    partial_note = _partial_window_note(window)

    points: List[str] = []
    concerns: List[str] = []

    if not rows:
        collected = window.get("afterDaysCollected")
        required = window.get("afterDaysRequired")
        headline = "No parameter comparison was supplied yet."
        if collected is not None and required is not None:
            points.append(f"After-window days collected {collected} of {required} required.")
        return {
            "headline": headline[:300],
            "effect": "TOO_EARLY",
            "points": _clip(points),
            "concerns": [],
            "nextSteps": _clip([str(x) for x in (service.get("recommendations") or []) if x]),
        }

    effect = _overall_effect(rows)
    period_summary = _post_service_period_summary(window, service)
    if period_summary:
        points.append(period_summary)
    if partial_note:
        points.append(partial_note)

    improved = [r for r in rows if r.get("verdict") == "IMPROVED"]
    worse = [r for r in rows if r.get("verdict") == "WORSE"]

    def _row_sentence(r: Dict[str, Any]) -> str:
        name = r.get("parameter") or r.get("key") or "Parameter"
        before, after = r.get("before"), r.get("after")
        pct = r.get("changePct")
        verdict = r.get("verdict")
        bits = [f"{name} was {_fmt(before)} before and {_fmt(after)} after"]
        if pct is not None:
            bits.append(f"changePct {_fmt(pct)}")
        if verdict:
            bits.append(f"verdict {verdict}")
        return ", ".join(bits) + "."

    for r in rows:
        sentence = _row_sentence(r)
        if r.get("verdict") == "WORSE":
            concerns.append(sentence)
        else:
            points.append(sentence)

    alerts = comparison.get("alerts") if isinstance(comparison.get("alerts"), dict) else {}
    before_a, after_a = alerts.get("before"), alerts.get("after")
    if before_a is None and after_a is not None:
        concerns.append(
            f"Alerts before the service were not recorded, so the {_fmt(after_a)} alerts after cannot be compared."
        )
    elif before_a is not None and after_a is not None:
        points.append(f"Alerts were {_fmt(before_a)} before and {_fmt(after_a)} after.")

    for check in comparison.get("readingChecks") or []:
        if not isinstance(check, dict):
            continue
        result = check.get("result")
        reading = check.get("reading") or "reading"
        reported = check.get("reported") if isinstance(check.get("reported"), dict) else {}
        sensor = check.get("sensor") if isinstance(check.get("sensor"), dict) else None
        if result in ("UNIT_UNKNOWN", "DIFFERENT_MEASURE", "NOT_MONITORED"):
            unit = reported.get("unit")
            val = reported.get("value")
            msg = f"{reading} reported {_fmt(val)}"
            if unit:
                msg += f" {unit}"
            if result == "UNIT_UNKNOWN":
                msg += " cannot be compared: the sensor reports no unit."
            elif result == "DIFFERENT_MEASURE":
                msg += " cannot be compared: it is a different measure from the sensor."
            else:
                msg += " is not monitored on the sensor."
            if sensor and sensor.get("value") is not None:
                msg += f" Sensor value {_fmt(sensor.get('value'))}."
            concerns.append(msg)
        elif result == "DIFFERENT_VALUE" and sensor:
            concerns.append(
                f"{reading} reported {_fmt(reported.get('value'))} while the sensor shows {_fmt(sensor.get('value'))}."
            )

    if improved and worse:
        headline = (
            f"{improved[0].get('parameter') or 'One parameter'} verdict IMPROVED "
            f"(changePct {_fmt(improved[0].get('changePct'))}); "
            f"{worse[0].get('parameter') or 'another'} verdict WORSE "
            f"(changePct {_fmt(worse[0].get('changePct'))})."
        )
    elif improved:
        headline = (
            f"{improved[0].get('parameter') or 'Parameter'} verdict IMPROVED, "
            f"changePct {_fmt(improved[0].get('changePct'))}."
        )
    elif worse:
        headline = (
            f"{worse[0].get('parameter') or 'Parameter'} verdict WORSE, "
            f"changePct {_fmt(worse[0].get('changePct'))}."
        )
    else:
        headline = "No parameter moved outside the no-change band in the supplied comparison."

    if partial_note and not window_complete:
        headline = f"{partial_note} {headline}"[:300]

    next_steps = [str(x) for x in (service.get("recommendations") or []) if x]
    result = {
        "headline": headline[:300],
        "effect": effect,
        "points": _clip(points),
        "concerns": _clip(concerns),
        "nextSteps": _clip(next_steps),
    }
    joined = " ".join([result["headline"], *result["points"], *result["concerns"], *result["nextSteps"]])
    if not numbers_are_grounded(joined, body):
        # Drop any sentence that introduced a number not in the request.
        result["points"] = [p for p in result["points"] if numbers_are_grounded(p, body)]
        result["concerns"] = [c for c in result["concerns"] if numbers_are_grounded(c, body)]
        result["nextSteps"] = [s for s in result["nextSteps"] if numbers_are_grounded(s, body)]
        if not numbers_are_grounded(result["headline"], body):
            result["headline"] = "Comparison figures are listed in the points below."
    return result
