"""Dashboard card endpoints.

The UI server calls these. They do not replace the tenant/asset diagnostic
routes; they map the product DiagnosticResult into the FaultDiagnosis card.
"""

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Security

from src.core.buffer import platform_buffer
from src.core.context import parse_tenant_key
from src.db.historian import platform_historian
from src.services.fleet_live_diagnosis import try_fleet_live_diagnosis
from src.services.fleet_machine_resolver import resolve_fleet_machine
from src.middleware.auth import get_current_principal, assert_tenant_access

router = APIRouter(prefix="/api/v1", tags=["Dashboard card"])


def _find(machine_id: str, tenant_id: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    wanted = machine_id.strip().lower()
    # 1. First check hot in-memory cache for exact tenant or any tenant matching asset
    with platform_buffer._lock:
        if tenant_id:
            key = make_tenant_key(tenant_id, machine_id)
            if key in platform_buffer._latest_diagnostics:
                return tenant_id, platform_buffer._latest_diagnostics[key]
        for key, diag in platform_buffer._latest_diagnostics.items():
            t, a = parse_tenant_key(key)
            if a.lower() == wanted:
                return t, diag

    # 2. Fall back to resolved tenant (including historian)
    resolved = resolve_fleet_machine(machine_id, tenant_id)
    if resolved:
        t_id, a_id, _twin, _tp = resolved
        diag = platform_buffer.get_latest_diagnostic(t_id, a_id)
        if diag:
            return t_id, diag

    if tenant_id:
        diag = platform_buffer.get_latest_diagnostic(tenant_id, machine_id)
        if diag:
            return tenant_id, diag

    return None, None


def _severity(health: str, fault_severity: str) -> str:
    token = (fault_severity or health or "").upper()
    if token in {"CRITICAL", "SENSOR_FAULT"}:
        return "critical"
    if token in {"WARNING", "DEGRADED"}:
        return "warning"
    return "good"


def _synthesize_breach_diagnosis(machine_id: str, breaches: List[Dict[str, Any]], telemetry: Dict[str, Any]) -> Dict[str, Any]:
    def is_vib(b: Dict[str, Any]) -> bool:
        field = str(b.get("paramName") or b.get("parameter") or b.get("key") or "").lower()
        return "vib" in field or "accel" in field

    vib_breach = next((b for b in breaches if is_vib(b)), None)
    target = vib_breach or (breaches[0] if breaches else {})
    param = target.get("paramName") or target.get("parameter") or target.get("key") or "vibration.overall"
    val = target.get("value")
    if val is None and telemetry:
        val = telemetry.get(param) or telemetry.get("vibration.overall") or telemetry.get("vibration") or 0.1
    elif val is None:
        val = 0.1

    raw_thresh = target.get("threshold")
    if isinstance(raw_thresh, dict):
        lo = raw_thresh.get("low", 0.0)
        hi = raw_thresh.get("high", 0.1)
        thresh = f"outside {lo:g}–{hi:g}"
    elif raw_thresh:
        thresh = str(raw_thresh)
    else:
        thresh = "outside 0.0–0.1"

    val_str = f"{val:g}" if isinstance(val, (int, float)) else str(val)
    is_v = "vib" in str(param).lower() or is_vib(target)
    headline = (
        f"Vibration {val_str} Outside Alert Range ({thresh}) Identified"
        if is_v
        else f"{param} {val_str} Outside Alert Range ({thresh}) Identified"
    )

    triage_steps = [
        "Perform emergency vibration inspection at bearing housings and mounting points.",
        "Check lubrication levels and verify mechanical alignment.",
        "Confirm reading with handheld analyzer and reduce load if necessary.",
    ]

    return {
        "machineId": machine_id,
        "archetype": "VIBRATION_API_LIMIT_EXCEEDED" if is_v else "ANOMALY_UNCLASSIFIED",
        "generatedAt": int(time.time() * 1000),
        "headline": headline,
        "severity": "critical",
        "summary": (
            f"• Platform Alert: Configured alert threshold breached for {param} ({val_str} vs {thresh}).\n"
            f"• Agent Gamma: Active limit exceedance detected on primary sensor.\n"
            f"• Immediate operator intervention recommended."
        ),
        "recommendedActions": triage_steps,
        "faultExplanation": {
            "whatIsIt": headline,
            "rootCause": f"Sensor '{param}' reading ({val_str}) breached configured alert band ({thresh}).",
            "riskImpact": "CRITICAL",
        },
        "repairOptions": [
            {
                "title": f"Emergency Triage: {headline}",
                "category": "Immediate Triage",
                "urgency": "Immediate",
                "steps": triage_steps,
                "estDowntime": "1-2 hours",
            }
        ],
        "pipelineDetails": {
            "cable_check": {"status": "VALID"},
            "electrical_health": {"isolated_failure_domain": "MECHANICAL"},
            "defect_localization": {
                "defect_code": "VIBRATION_API_LIMIT_EXCEEDED" if "vib" in str(param).lower() else "ANOMALY_UNCLASSIFIED",
                "defect_name": "Vibration Alert Limit Exceeded" if "vib" in str(param).lower() else "Alert Limit Exceeded",
            },
            "rul_prediction": {"rul_days": 7.0},
            "cmms_work_order": {
                "work_order_id": f"WO-EMERG-{machine_id.upper()[:6]}",
                "recommended_action": "Emergency Vibration Triage",
                "scheduled_repair_window": "Immediate Shift",
                "repair_crew": "Mechanical Maintenance",
                "ticket_type": "EMERGENCY_REPAIR",
            },
        },
    }


def to_frontend_diagnosis(machine_id: str, diag: Dict[str, Any]) -> Dict[str, Any]:
    health = str(diag.get("health_status") or "HEALTHY")
    faults: List[Dict[str, Any]] = [f for f in (diag.get("active_faults") or []) if isinstance(f, dict)]
    first = faults[0] if faults else {}
    code = str(first.get("fault_code") or "NORMAL")
    name = str(first.get("description") or code)
    prog = diag.get("prognostics") if isinstance(diag.get("prognostics"), dict) else {}
    action = diag.get("prescriptive_action") if isinstance(diag.get("prescriptive_action"), dict) else {}
    signal = diag.get("signal_integrity") if isinstance(diag.get("signal_integrity"), dict) else {}
    halted = health.upper() == "SENSOR_FAULT"
    is_defect = bool(faults) and code not in {"", "NORMAL", "NONE", "HEALTHY"}
    severity = _severity(health, str(first.get("severity") or ""))
    if halted:
        archetype = "SENSOR_FAULT"
        headline = "Sensor anomaly halted the diagnosis"
    elif is_defect:
        archetype = code
        headline = f"{name} ({code}) Identified"
    elif health.upper() == "IDLE" or str(diag.get("operating_regime") or "").upper() == "IDLE":
        archetype = "IDLE"
        headline = "Machine is idle"
        severity = "good"
    else:
        archetype = "NORMAL"
        headline = "All monitored parameters operating nominally"
        severity = "good"

    days = prog.get("rul_days")
    if code == "VIBRATION_API_LIMIT_EXCEEDED" and days is None:
        days = 7.0

    summary = [
        f"• Agent Alpha: {'Halted on signal integrity' if halted else 'Sensor path accepted'}.",
        "• Agent Beta: Health "
        + (f"{diag.get('health_score')}" if diag.get("health_score") is not None else "n/a")
        + f" ({health}).",
    ]
    if is_defect and days is not None:
        summary.append(f"• Agent Gamma: {name}. Remaining useful life {days} days.")
    elif not halted:
        summary.append("• Agent Gamma: No isolated defect on the latest run.")
    if action.get("work_order_id"):
        summary.append(f"• Agent Delta: Work order {action.get('work_order_id')}.")
    elif not halted:
        summary.append("• Agent Delta: No work order on this run.")

    actions: List[str] = []
    if action.get("description"):
        actions.append(str(action["description"]))
    elif code == "VIBRATION_API_LIMIT_EXCEEDED":
        actions = [
            "Inspect machine mounting and bearing housing immediately.",
            "Check lube level and confirm vibration amplitude with calibrated handheld analyzer.",
            "Plan immediate shift maintenance to prevent catastrophic trip."
        ]
    elif is_defect:
        actions.append("Inspect the diagnosed component and confirm the live reading before scheduling repair.")
    else:
        actions.append("Continue monitoring this asset.")

    cmms = None
    if action:
        cmms = {
            "work_order_id": action.get("work_order_id"),
            "recommended_action": action.get("description"),
            "scheduled_repair_window": action.get("scheduled_downtime_shift"),
            "repair_crew": action.get("target_system"),
            "ticket_type": action.get("action_type"),
        }
    elif is_defect or severity == "critical":
        cmms = {
            "work_order_id": f"WO-EMERG-{machine_id.upper()[:6]}",
            "recommended_action": actions[0] if actions else "Inspect asset",
            "scheduled_repair_window": "Immediate Shift",
            "repair_crew": "Mechanical Maintenance",
            "ticket_type": "EMERGENCY_REPAIR",
        }

    repair_opts = []
    if action.get("description"):
        repair_opts.append({
            "title": str(action.get("description")),
            "category": "Immediate Triage" if action.get("priority") in {"HIGH", "EMERGENCY"} or severity == "critical" else "Precision Repair",
            "urgency": "Immediate" if action.get("priority") in {"HIGH", "EMERGENCY"} or severity == "critical" else "Scheduled",
            "steps": [str(action.get("description"))],
            "estDowntime": action.get("scheduled_downtime_shift") or "1-2 hours",
        })
    elif is_defect or severity == "critical":
        repair_opts.append({
            "title": f"Emergency Triage: {name}",
            "category": "Immediate Triage",
            "urgency": "Immediate",
            "steps": actions,
            "estDowntime": "1-2 hours",
        })

    ts = diag.get("timestamp")
    generated_at = int(float(ts) * 1000) if isinstance(ts, (int, float)) else 0

    return {
        "machineId": machine_id,
        "archetype": archetype,
        "generatedAt": generated_at,
        "headline": headline,
        "severity": severity,
        "summary": "\n".join(summary),
        "recommendedActions": actions,
        "faultExplanation": {
            "whatIsIt": name if is_defect else headline,
            "rootCause": str(first.get("failure_domain") or "No isolated fault on this run."),
            "riskImpact": str(action.get("priority") or health),
        } if is_defect or halted else None,
        "repairOptions": repair_opts,
        "pipelineDetails": {
            "cable_check": {"status": "HALTED" if halted else "VALID", "fault_reason": signal.get("reason")},
            "electrical_health": {"isolated_failure_domain": first.get("failure_domain")},
            "defect_localization": {
                "defect_code": code if is_defect else "NORMAL",
                "defect_name": name if is_defect else "Normal",
            },
            "rul_prediction": {"rul_days": days} if days is not None else None,
            "cmms_work_order": cmms,
        },
    }


def _history_row(diag: Dict[str, Any]) -> Dict[str, Any]:
    faults = [f for f in (diag.get("active_faults") or []) if isinstance(f, dict)]
    first = faults[0] if faults else {}
    action = diag.get("prescriptive_action") if isinstance(diag.get("prescriptive_action"), dict) else {}
    prog = diag.get("prognostics") if isinstance(diag.get("prognostics"), dict) else {}
    ts = diag.get("iso_timestamp") or diag.get("timestamp")
    return {
        "trace_id": diag.get("trace_id"),
        "timestamp": ts if isinstance(ts, str) else None,
        "defect_code": first.get("fault_code"),
        "failing_component": first.get("failure_domain"),
        "rul_days": prog.get("rul_days"),
        "health_status": diag.get("health_status"),
        "work_order_id": action.get("work_order_id"),
    }


@router.post("/machines/{machine_id}/fault-assistant")
async def fault_assistant(
    machine_id: str,
    request: Request,
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Security(get_current_principal),
):
    resolved = resolve_fleet_machine(machine_id, tenant_id)
    if resolved:
        assert_tenant_access(principal, resolved[0])
    elif tenant_id:
        assert_tenant_access(principal, tenant_id)

    body: Dict[str, Any] = {}
    try:
        raw = await request.json()
        if isinstance(raw, dict):
            body = raw
    except Exception:
        body = {}

    breaches = [b for b in (body.get("breaches") or []) if isinstance(b, dict)]
    telemetry = body.get("telemetry") if isinstance(body.get("telemetry"), dict) else {}

    fleet_result, fleet_alerts, _fleet_block = try_fleet_live_diagnosis(machine_id, tenant_id)
    if fleet_alerts and not breaches:
        breaches = fleet_alerts
    if fleet_result is not None:
        res = to_frontend_diagnosis(machine_id, fleet_result.model_dump())
        if breaches and (
            res.get("severity") != "critical"
            or "NORMAL" in str(res.get("archetype"))
            or "STANDBY" in str(res.get("archetype"))
        ):
            return _synthesize_breach_diagnosis(machine_id, breaches, telemetry)
        return res

    # Check if telemetry has vibration exceeding API limits even if breaches list is empty
    if not breaches and telemetry:
        vib_val = telemetry.get("vibration.overall") or telemetry.get("vibration") or telemetry.get("vibration_rms")
        if isinstance(vib_val, (int, float)):
            from src.services.threshold_provider import DEFAULT_API_VIBRATION_BANDS
            band = DEFAULT_API_VIBRATION_BANDS.get(machine_id.strip().lower(), (0.0, 0.1))
            if not (band[0] <= float(vib_val) <= band[1]):
                breaches.append({
                    "parameter": "vibration.overall",
                    "value": float(vib_val),
                    "threshold": f"outside {band[0]:g}–{band[1]:g}"
                })

    tenant, diag = _find(machine_id, tenant_id)
    if not diag:
        if breaches or telemetry:
            return _synthesize_breach_diagnosis(machine_id, breaches, telemetry)
        raise HTTPException(status_code=404, detail=f"No diagnostic run for {machine_id}")

    res = to_frontend_diagnosis(machine_id, diag)

    # If active alert breaches are present, force elevation to Critical
    if breaches and (res.get("severity") != "critical" or "NORMAL" in str(res.get("archetype")) or "STANDBY" in str(res.get("archetype"))):
        synth = _synthesize_breach_diagnosis(machine_id, breaches, telemetry)
        return synth

    return res


@router.get("/predict/history/{machine_id}")
def diagnosis_history(
    machine_id: str,
    limit: int = 20,
    tenant_id: Optional[str] = Query(default=None),
    principal: Dict[str, Any] = Security(get_current_principal),
):
    resolved = resolve_fleet_machine(machine_id, tenant_id)
    if resolved:
        assert_tenant_access(principal, resolved[0])
    elif tenant_id:
        assert_tenant_access(principal, tenant_id)

    tenant, _diag = _find(machine_id, tenant_id)
    if not tenant:
        return []
    rows = platform_historian.get_diagnostic_history(tenant, machine_id, limit=limit)
    return [_history_row(row) for row in rows if isinstance(row, dict)]
