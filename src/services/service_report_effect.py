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
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.services.fleet_query_service import fleet_query_service
from src.services.fleet_telemetry_adapter import flatten_numeric_leaves
from src.utils.logger import get_logger

logger = get_logger("Services.ServiceReportEffect")

_ENRICH_VERDICT_BAND_PCT = 5.0

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
        has_before = before is not None and _fmt(before) != ""
        has_after = after is not None and _fmt(after) != ""
        if has_before and has_after:
            bits = [f"{name} averaged {_fmt(before)} before service and {_fmt(after)} after"]
        elif has_after:
            bits = [
                f"{name}: no pre-service average in the comparison window; "
                f"post-service average {_fmt(after)}"
            ]
        elif has_before:
            bits = [f"{name}: pre-service average {_fmt(before)}; no post-service average yet"]
        else:
            bits = [f"{name}: no before or after averages in the comparison payload"]
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


def _enrich_parse_ymd(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _enrich_row_date(row: Dict[str, Any]) -> Optional[date]:
    ts = row.get("timestamp") or row.get("ts")
    if isinstance(ts, (int, float)):
        epoch = float(ts) / 1000.0 if ts > 1e12 else float(ts)
        return datetime.fromtimestamp(epoch, tz=timezone.utc).date()
    if isinstance(ts, str) and ts.strip():
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return None


def _enrich_value_for_key(row: Dict[str, Any], key: str) -> Optional[float]:
    if not key:
        return None
    flat = flatten_numeric_leaves(row)
    if key in flat:
        return flat[key]
    alt = key.replace(".", "_")
    if alt in flat:
        return flat[alt]
    for k, v in flat.items():
        if k.endswith(key.split(".")[-1]) or key.endswith(k.split(".")[-1]):
            return v
    return None


def _enrich_mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _enrich_history_duration_days(before_start: date, after_end: date) -> str:
    days = max(1, (after_end - before_start).days + 1)
    return f"{min(days, 90)}d"


def _enrich_recompute_verdict(
    before: Optional[float],
    after: Optional[float],
    better_when: Optional[str],
) -> Tuple[Optional[float], str]:
    if before is None or after is None:
        return None, "UNAVAILABLE"
    if before == 0 and after == 0:
        return 0.0, "NO_CHANGE"
    if before == 0:
        return None, "UNAVAILABLE"
    pct = round((after - before) / abs(before) * 100.0, 1)
    if abs(pct) <= _ENRICH_VERDICT_BAND_PCT:
        return pct, "NO_CHANGE"
    lower_better = str(better_when or "lower").lower() == "lower"
    improved = (after < before) if lower_better else (after > before)
    if improved:
        return pct, "IMPROVED"
    worse = (after > before) if lower_better else (after < before)
    if worse:
        return pct, "WORSE"
    return pct, "CHANGED"


def enrich_comparison_from_upstream(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill missing comparison.beforeAfter[].before from POST /api/ai/query history.
    Reports should send complete averages; this backfill avoids all-UNAVAILABLE effect text.
    """
    comparison = body.get("comparison")
    if not isinstance(comparison, dict):
        return body
    rows = comparison.get("beforeAfter")
    if not isinstance(rows, list) or not rows:
        return body
    needs = any(
        isinstance(r, dict)
        and r.get("before") is None
        and (r.get("after") is not None or r.get("key"))
        for r in rows
    )
    if not needs or not fleet_query_service.is_configured():
        return body

    machine = body.get("machine") if isinstance(body.get("machine"), dict) else {}
    machine_id = str(machine.get("id") or "").strip()
    if not machine_id:
        return body

    window = comparison.get("window") if isinstance(comparison.get("window"), dict) else {}
    before_w = window.get("before") if isinstance(window.get("before"), dict) else {}
    after_w = window.get("after") if isinstance(window.get("after"), dict) else {}
    b0 = _enrich_parse_ymd(before_w.get("start"))
    b1 = _enrich_parse_ymd(before_w.get("end"))
    a0 = _enrich_parse_ymd(after_w.get("start"))
    a1 = _enrich_parse_ymd(after_w.get("end"))
    if not all([b0, b1, a0, a1]):
        logger.debug("Service effect enrich: comparison.window dates incomplete")
        return body

    duration = _enrich_history_duration_days(b0, a1)
    keys = [str(r.get("key")) for r in rows if isinstance(r, dict) and r.get("key")]
    try:
        history_rows = fleet_query_service.fetch_history(
            [machine_id],
            source="db",
            duration=duration,
            max_points=2000,
            fields=keys or None,
        )
    except Exception as e:
        logger.warning(f"Service effect enrich: history fetch failed ({e})")
        return body

    if not history_rows:
        logger.warning("Service effect enrich: no history rows from upstream")
        return body

    buckets: Dict[str, Dict[str, List[float]]] = {}
    for row in history_rows:
        if not isinstance(row, dict):
            continue
        d = _enrich_row_date(row)
        if not d:
            continue
        bucket: Optional[str] = None
        if b0 <= d <= b1:
            bucket = "before"
        elif a0 <= d <= a1:
            bucket = "after"
        if not bucket:
            continue
        for key in keys:
            val = _enrich_value_for_key(row, key)
            if val is None:
                continue
            buckets.setdefault(key, {"before": [], "after": []})[bucket].append(val)

    updated: List[Dict[str, Any]] = []
    filled = 0
    for r in rows:
        if not isinstance(r, dict):
            updated.append(r)
            continue
        row = dict(r)
        key = str(row.get("key") or "")
        series = buckets.get(key) or {}
        if row.get("before") is None and series.get("before"):
            row["before"] = round(_enrich_mean(series["before"]), 4)
            filled += 1
        if row.get("after") is None and series.get("after"):
            row["after"] = round(_enrich_mean(series["after"]), 4)
            filled += 1
        if row.get("before") is not None and row.get("after") is not None:
            if row.get("verdict") in (None, "", "UNAVAILABLE"):
                pct, verdict = _enrich_recompute_verdict(
                    float(row["before"]),
                    float(row["after"]),
                    row.get("betterWhen"),
                )
                if pct is not None:
                    row["changePct"] = pct
                row["verdict"] = verdict
        updated.append(row)

    if filled:
        logger.info(
            f"Service effect enrich: filled {filled} missing before/after averages "
            f"for machine={machine_id} from {len(history_rows)} history rows"
        )
        new_body = dict(body)
        new_body["comparison"] = {**comparison, "beforeAfter": updated}
        return new_body
    return body
