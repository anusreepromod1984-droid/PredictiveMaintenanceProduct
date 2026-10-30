"""
Explain a confirmed service using only numbers supplied in the comparison payload.
Does not recompute change percentages or claim the service caused a change.
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


def _overall_effect(rows: List[Dict[str, Any]], readiness: Optional[str], window: Dict[str, Any]) -> str:
    if readiness and str(readiness).upper() not in ("COMPLETE", ""):
        if str(readiness).upper() != "COMPLETE":
            return "TOO_EARLY"
    collected = window.get("afterDaysCollected")
    required = window.get("afterDaysRequired")
    if isinstance(collected, int) and isinstance(required, int) and collected < required:
        return "TOO_EARLY"
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
    effect = _overall_effect(rows, comparison.get("readiness"), window)

    points: List[str] = []
    concerns: List[str] = []

    if effect == "TOO_EARLY":
        collected = window.get("afterDaysCollected")
        required = window.get("afterDaysRequired")
        headline = "The after-service window is not complete yet, so no overall effect is stated."
        if collected is not None and required is not None:
            points.append(
                f"After-window days collected {collected} of {required} required."
            )
        return {
            "headline": headline[:300],
            "effect": "TOO_EARLY",
            "points": _clip(points),
            "concerns": [],
            "nextSteps": _clip([str(x) for x in (service.get("recommendations") or []) if x]),
        }

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
