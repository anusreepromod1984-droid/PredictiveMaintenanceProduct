"""Dashboard card endpoints.

The UI server calls these. They do not replace the tenant/asset diagnostic
routes; they map the product DiagnosticResult into the FaultDiagnosis card.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from src.core.buffer import platform_buffer
from src.core.context import parse_tenant_key
from src.db.historian import platform_historian

router = APIRouter(prefix="/api/v1", tags=["Dashboard card"])


def _find(machine_id: str, tenant_id: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    wanted = machine_id.strip().lower()
    if tenant_id:
        diag = platform_buffer.get_latest_diagnostic(tenant_id, machine_id)
        return tenant_id, diag
    with platform_buffer._lock:
        for key, diag in platform_buffer._latest_diagnostics.items():
            tenant, asset = parse_tenant_key(key)
            if asset.lower() == wanted:
                return tenant, diag
    return None, None


def _severity(health: str, fault_severity: str) -> str:
    token = (fault_severity or health or "").upper()
    if token in {"CRITICAL", "SENSOR_FAULT"}:
        return "critical"
    if token in {"WARNING", "DEGRADED"}:
        return "warning"
    return "good"


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
        "repairOptions": [
            {
                "title": str(action.get("description") or "Inspect"),
                "category": "Immediate Triage" if action.get("priority") in {"HIGH", "EMERGENCY"} else "Precision Repair",
                "urgency": "Immediate" if action.get("priority") in {"HIGH", "EMERGENCY"} else "Scheduled",
                "steps": [str(action.get("description"))] if action.get("description") else [],
                "estDowntime": action.get("scheduled_downtime_shift"),
            }
        ] if action.get("description") else [],
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
def fault_assistant(machine_id: str, tenant_id: Optional[str] = Query(default=None)):
    tenant, diag = _find(machine_id, tenant_id)
    if not diag:
        raise HTTPException(status_code=404, detail=f"No diagnostic run for {machine_id}")
    return to_frontend_diagnosis(machine_id, diag)


@router.get("/predict/history/{machine_id}")
def diagnosis_history(machine_id: str, limit: int = 20, tenant_id: Optional[str] = Query(default=None)):
    tenant, _diag = _find(machine_id, tenant_id)
    if not tenant:
        return []
    rows = platform_historian.get_diagnostic_history(tenant, machine_id, limit=limit)
    return [_history_row(row) for row in rows if isinstance(row, dict)]
