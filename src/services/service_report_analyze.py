"""Extract a ServiceExtraction object from uploaded service documents via Groq."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional

from src.config import settings
from src.services.llm_client import chat_completions_text, extract_json_object
from src.services.service_document_text import files_to_prompt_blocks
from src.utils.logger import get_logger

logger = get_logger("Services.ServiceReportAnalyze")

_TYPES = {"PREVENTIVE", "CORRECTIVE", "BREAKDOWN", "INSPECTION", "CALIBRATION", "OTHER"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALIDATION_STATUS = {"valid", "invalid", "uncertain"}

_PROMPT = """You classify uploaded files and extract fields ONLY when the document is a machine service report or job card.

STEP 1 — Classify the document (read the file, not the user context block):
- documentKind: "service_report" for maintenance/service/job-card documents about equipment work.
- Otherwise use a specific label such as "invoice", "hotel_bill", "resume", "equipment_manual", "other", or "unknown".
- Invoices with parts/vendor are NOT service_report (use "invoice" or "other").
- classificationConfidence: 0 to 1 for documentKind.
- documentValidationStatus (optional): "valid" | "invalid" | "uncertain" — use "invalid" for clear non-service documents.

STEP 2 — Extract service fields ONLY from document text/images (never from user context):
- If documentKind is not service_report, return null/[] for all service fields and confidence {}.
- serviceDate, vendor, technician, type, workDone, parts, readings, issues, recommendations, cost, summary, machineIdentity must appear ON THE PAGE or be null/[].
- Do NOT copy machine id/name/model from the context block into machineIdentity. machineIdentity only if the document names equipment (name, model, or serial).
- Do NOT copy serviceDate or vendor from user hints.

Dates must be YYYY-MM-DD. type: PREVENTIVE, CORRECTIVE, BREAKDOWN, INSPECTION, CALIBRATION, OTHER, or null.
confidence: include ONLY keys for fields that have a non-null/non-empty value (never confidence 1.0 on null fields).

Reply with one JSON object, no markdown:
{
  "documentKind": "service_report",
  "classificationConfidence": 0.9,
  "documentValidationStatus": "valid",
  "serviceDate": null,
  "vendor": null,
  "technician": null,
  "type": null,
  "workDone": [],
  "partsReplaced": [],
  "readings": [],
  "issuesFound": [],
  "recommendations": [],
  "nextServiceDue": null,
  "cost": null,
  "summary": null,
  "machineIdentity": null,
  "confidence": {}
}
"""


def _empty() -> Dict[str, Any]:
    return {
        "documentKind": "unknown",
        "classificationConfidence": 0.0,
        "documentValidationStatus": None,
        "serviceDate": None,
        "vendor": None,
        "technician": None,
        "type": None,
        "workDone": [],
        "partsReplaced": [],
        "readings": [],
        "issuesFound": [],
        "recommendations": [],
        "nextServiceDue": None,
        "cost": None,
        "summary": None,
        "machineIdentity": None,
        "confidence": {},
    }


def _clamp01(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return max(0.0, min(1.0, float(v)))
    return 0.0


def _normalize_document_kind(v: Any) -> str:
    if v is None or not str(v).strip():
        return "unknown"
    s = str(v).strip().lower().replace(" ", "_")
    if s in ("service_report", "service", "job_card", "maintenance_report", "maintenance"):
        return "service_report"
    if s == "unknown":
        return "unknown"
    return str(v).strip().lower().replace(" ", "_")


def prune_field_confidence(out: Dict[str, Any]) -> None:
    """Drop confidence entries when the corresponding field has no extracted value."""
    conf = out.get("confidence")
    if not isinstance(conf, dict):
        out["confidence"] = {}
        return

    def _has(field: str) -> bool:
        val = out.get(field)
        if field in ("workDone", "partsReplaced", "readings", "issuesFound", "recommendations"):
            return isinstance(val, list) and len(val) > 0
        if field == "cost":
            return isinstance(val, dict) and val.get("amount") is not None
        if field == "machineIdentity":
            if not isinstance(val, dict):
                return False
            return any(val.get(k) for k in ("name", "model", "serial"))
        return val is not None and str(val).strip() != ""

    cleaned: Dict[str, float] = {}
    for k, v in conf.items():
        if k in ("documentKind", "classificationConfidence"):
            continue
        if _has(k) and isinstance(v, (int, float)) and not isinstance(v, bool):
            cleaned[k] = _clamp01(v)
    out["confidence"] = cleaned


def sanitize_against_request_context(
    out: Dict[str, Any],
    hints: Dict[str, Any],
    machine: Dict[str, Any],
) -> None:
    """Remove values that only echo upload machine/hints, not document content."""
    hint_date = _date(hints.get("serviceDate"))
    hint_vendor = (hints.get("vendor") or "").strip().lower()
    ctx_ids = {
        (machine.get("id") or "").strip().lower(),
        (machine.get("name") or "").strip().lower(),
        (machine.get("model") or "").strip().lower(),
    }
    ctx_ids.discard("")

    if hint_date and out.get("serviceDate") == hint_date:
        out["serviceDate"] = None
    if hint_vendor and (out.get("vendor") or "").strip().lower() == hint_vendor:
        out["vendor"] = None

    ident = out.get("machineIdentity")
    if isinstance(ident, dict):
        iname = (ident.get("name") or "").strip().lower()
        imodel = (ident.get("model") or "").strip().lower()
        iserial = (ident.get("serial") or "").strip()
        echo_name = iname and (iname in ctx_ids or any(c and c in iname for c in ctx_ids))
        echo_model = imodel and imodel in ctx_ids
        if (echo_name or echo_model) and not iserial:
            out["machineIdentity"] = None
        elif not any((ident.get("name"), ident.get("model"), ident.get("serial"))):
            out["machineIdentity"] = None


def _clear_service_fields(out: Dict[str, Any]) -> None:
    out["serviceDate"] = None
    out["vendor"] = None
    out["technician"] = None
    out["type"] = None
    out["workDone"] = []
    out["partsReplaced"] = []
    out["readings"] = []
    out["issuesFound"] = []
    out["recommendations"] = []
    out["nextServiceDue"] = None
    out["cost"] = None
    out["summary"] = None
    out["machineIdentity"] = None
    out["confidence"] = {}


def _heuristic_classify(text: str) -> Dict[str, Any]:
    low = text.lower()
    if not low.strip():
        return {"documentKind": "unknown", "classificationConfidence": 0.0, "documentValidationStatus": "uncertain"}

    non_service = (
        ("hotel", "invoice", "tax invoice", "gst", "lodging", "resume", "curriculum vitae", "offer letter"),
        ("user manual", "operator manual", "equipment manual", "installation guide", "warranty card"),
    )
    for keywords in non_service[0]:
        if keywords in low:
            if keywords in ("hotel", "lodging"):
                kind = "hotel_bill"
            elif keywords in ("invoice", "tax invoice", "gst"):
                kind = "invoice"
            elif keywords in ("resume", "curriculum vitae", "offer letter"):
                kind = "resume"
            else:
                kind = "other"
            return {
                "documentKind": kind,
                "classificationConfidence": 0.85,
                "documentValidationStatus": "invalid",
            }
    for keywords in non_service[1]:
        if keywords in low:
            return {
                "documentKind": "equipment_manual",
                "classificationConfidence": 0.85,
                "documentValidationStatus": "invalid",
            }

    service_signals = (
        "service report",
        "work done",
        "preventive",
        "corrective",
        "technician",
        "next service",
        "bearing",
        "maintenance",
        "vibration",
    )
    hits = sum(1 for w in service_signals if w in low)
    if hits >= 2:
        return {
            "documentKind": "service_report",
            "classificationConfidence": min(0.5 + hits * 0.08, 0.92),
            "documentValidationStatus": "uncertain",
        }
    return {"documentKind": "unknown", "classificationConfidence": 0.4, "documentValidationStatus": "uncertain"}


def _date(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()[:10]
    return s if _DATE.match(s) else None


def _clip_str(v: Any, limit: int) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def normalize_extraction(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = _empty()
    out["documentKind"] = _normalize_document_kind(raw.get("documentKind"))
    out["classificationConfidence"] = _clamp01(raw.get("classificationConfidence"))
    dvs = raw.get("documentValidationStatus")
    if isinstance(dvs, str) and dvs.strip().lower() in _VALIDATION_STATUS:
        out["documentValidationStatus"] = dvs.strip().lower()
    out["serviceDate"] = _date(raw.get("serviceDate"))
    out["nextServiceDue"] = _date(raw.get("nextServiceDue"))
    out["vendor"] = _clip_str(raw.get("vendor"), 200)
    out["technician"] = _clip_str(raw.get("technician"), 200)
    typ = raw.get("type")
    if isinstance(typ, str) and typ.strip().upper() in _TYPES:
        out["type"] = typ.strip().upper()
    out["workDone"] = _str_list(raw.get("workDone"), 50)
    out["issuesFound"] = _str_list(raw.get("issuesFound"), 50)
    out["recommendations"] = _str_list(raw.get("recommendations"), 50)
    out["partsReplaced"] = _parts(raw.get("partsReplaced"))
    out["readings"] = _readings(raw.get("readings"))
    out["cost"] = _cost(raw.get("cost"))
    out["summary"] = _clip_str(raw.get("summary"), 2000)
    ident = raw.get("machineIdentity")
    if isinstance(ident, dict):
        mi = {
            "name": _clip_str(ident.get("name"), 200),
            "model": _clip_str(ident.get("model"), 200),
            "serial": _clip_str(ident.get("serial"), 200),
        }
        out["machineIdentity"] = mi if any(mi.values()) else None
    conf = raw.get("confidence")
    if isinstance(conf, dict):
        cleaned: Dict[str, float] = {}
        for k, v in conf.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cleaned[str(k)] = _clamp01(v)
        out["confidence"] = cleaned

    if out["documentKind"] != "service_report" and out["classificationConfidence"] >= 0.7:
        if out["documentValidationStatus"] is None:
            out["documentValidationStatus"] = "invalid"
        _clear_service_fields(out)
    elif out["documentKind"] != "service_report":
        _clear_service_fields(out)

    prune_field_confidence(out)
    return out


def _str_list(v: Any, limit: int) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if x and str(x).strip()][:limit]


def _parts(v: Any) -> List[Dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:50]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        qty = item.get("qty")
        out.append({
            "name": str(item["name"]).strip(),
            "qty": float(qty) if isinstance(qty, (int, float)) and not isinstance(qty, bool) else None,
        })
    return out


def _readings(v: Any) -> List[Dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:50]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        val = item.get("value")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        unit = item.get("unit")
        out.append({
            "name": str(item["name"]).strip(),
            "value": float(val),
            "unit": str(unit).strip() if unit else None,
        })
    return out


def _cost(v: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(v, dict):
        return None
    amount = v.get("amount")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return None
    cur = v.get("currency")
    return {"amount": float(amount), "currency": str(cur).strip() if cur else None}


def _call_llm(user_text: str, images: List[dict]) -> Dict[str, Any]:
    if images and not user_text:
        # Vision: send first image as data URL via chat content array.
        import httpx
        from src.config import settings as cfg

        img = images[0]
        b64 = base64.b64encode(img["data"]).decode("ascii")
        mime = img.get("mime") or "image/jpeg"
        api_key = cfg.report_llm_api_key()
        base = (cfg.REPORT_LLM_BASE_URL or "https://api.groq.com/openai/v1").rstrip("/")
        model = cfg.REPORT_LLM_MODEL or "openai/gpt-oss-120b"
        payload = {
            "model": model,
            "temperature": 0.1,
            "max_tokens": 1800,
            "messages": [
                {"role": "system", "content": _PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Classify this document and extract service fields only if it is a service report. "
                            "Do not use any upload context.",
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                },
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=float(cfg.REPORT_LLM_TIMEOUT_S or 90)) as client:
            resp = client.post(f"{base}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        return extract_json_object(str(content))

    raw = chat_completions_text(
        system_prompt=_PROMPT,
        user_content=user_text[:24000],
        temperature=0.1,
        max_tokens=1800,
        timeout_s=min(float(settings.REPORT_LLM_TIMEOUT_S or 90), 100),
    )
    return extract_json_object(raw)


def extract_from_plain_text(text: str) -> Dict[str, Any]:
    """Best-effort parse when Groq is unavailable. Never invents numbers not on the page."""
    out = _empty()
    if not text:
        return out
    cls = _heuristic_classify(text)
    out["documentKind"] = cls["documentKind"]
    out["classificationConfidence"] = cls["classificationConfidence"]
    out["documentValidationStatus"] = cls.get("documentValidationStatus")
    if out["documentKind"] != "service_report":
        prune_field_confidence(out)
        return out
    dates = re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", text)
    iso_dates: List[str] = []
    for d, m, y in dates:
        iso_dates.append(f"{y}-{m}-{d}")
    iso_dates += re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_dates:
        out["serviceDate"] = iso_dates[0]
        if len(iso_dates) > 1:
            out["nextServiceDue"] = iso_dates[-1]
        out["confidence"]["serviceDate"] = 0.75
    low = text.lower()
    if "preventive" in low:
        out["type"] = "PREVENTIVE"
        out["confidence"]["type"] = 0.8
    elif "corrective" in low:
        out["type"] = "CORRECTIVE"
        out["confidence"]["type"] = 0.8
    elif "breakdown" in low:
        out["type"] = "BREAKDOWN"
        out["confidence"]["type"] = 0.8
    elif "inspection" in low:
        out["type"] = "INSPECTION"
        out["confidence"]["type"] = 0.8
    elif "calibration" in low:
        out["type"] = "CALIBRATION"
        out["confidence"]["type"] = 0.8
    tech = re.search(r"Technician[:\s]+([A-Za-z. ]{2,40})", text)
    if tech:
        out["technician"] = tech.group(1).strip()[:200]
        out["confidence"]["technician"] = 0.75
    vendor = re.search(r"Vendor[:\s]+([^\n]{2,80})", text)
    if vendor:
        out["vendor"] = vendor.group(1).strip()[:200]
        out["confidence"]["vendor"] = 0.7
    out["summary"] = " ".join(text.split())[:500] or None
    prune_field_confidence(out)
    return out


def analyze_service_report(body: Dict[str, Any]) -> Dict[str, Any]:
    hints = body.get("hints") or {}
    machine = body.get("machine") or {}
    try:
        text, images = files_to_prompt_blocks(body.get("files") or [])
    except Exception as e:
        logger.warning(f"Service report analyze: file fetch failed ({e})")
        return _empty()
    preamble = (
        "=== USER CONTEXT (do NOT copy into extracted fields) ===\n"
        f"Filed under machine id={machine.get('id')} name={machine.get('name')} model={machine.get('model')}\n"
        f"Upload hints (ignore for extraction): serviceDate={hints.get('serviceDate')} vendor={hints.get('vendor')}\n"
        "=== END CONTEXT — classify and extract from the document only ===\n\n"
    )
    if not text and not images:
        logger.warning("Service report analyze: no extractable text or images")
        return _empty()
    if settings.report_llm_enabled:
        try:
            parsed = _call_llm(preamble + text, images if not text else [])
            if isinstance(parsed, dict):
                out = normalize_extraction(parsed)
                sanitize_against_request_context(out, hints, machine)
                prune_field_confidence(out)
                return out
        except Exception as e:
            logger.warning(f"Service report analyze LLM failed ({e}); using text fallback")
    out = extract_from_plain_text(text)
    sanitize_against_request_context(out, hints, machine)
    prune_field_confidence(out)
    return out
