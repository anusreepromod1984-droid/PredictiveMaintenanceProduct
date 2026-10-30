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

_PROMPT = """You extract fields from an industrial machine service report.
Use ONLY text that appears in the document. If a field is not stated, use null or [].
Never guess vendor, cost, dates, serial numbers, or readings.
Dates must be YYYY-MM-DD.
type must be one of PREVENTIVE, CORRECTIVE, BREAKDOWN, INSPECTION, CALIBRATION, OTHER, or null.
Keep reading units exactly as written.
confidence is 0 to 1 for each field you filled (below 0.7 means uncertain).
Reply with one JSON object and no markdown:
{
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
partsReplaced items: {"name": "...", "qty": number or null}
readings items: {"name": "...", "value": number, "unit": string or null}
cost: {"amount": number, "currency": "INR"} or null
machineIdentity: {"name": null, "model": null, "serial": null} or null
Hints are optional context only; the document wins when they disagree.
"""


def _empty() -> Dict[str, Any]:
    return {
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
        out["machineIdentity"] = {
            "name": _clip_str(ident.get("name"), 200),
            "model": _clip_str(ident.get("model"), 200),
            "serial": _clip_str(ident.get("serial"), 200),
        }
    conf = raw.get("confidence")
    if isinstance(conf, dict):
        cleaned: Dict[str, float] = {}
        for k, v in conf.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cleaned[str(k)] = max(0.0, min(1.0, float(v)))
        out["confidence"] = cleaned
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
                        {"type": "text", "text": "Read this service sheet image. Extract only what is written."},
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


def analyze_service_report(body: Dict[str, Any]) -> Dict[str, Any]:
    hints = body.get("hints") or {}
    machine = body.get("machine") or {}
    text, images = files_to_prompt_blocks(body.get("files") or [])
    preamble = (
        f"Machine on file: id={machine.get('id')} name={machine.get('name')} model={machine.get('model')}\n"
        f"User hints (not authoritative): serviceDate={hints.get('serviceDate')} vendor={hints.get('vendor')}\n\n"
    )
    if not text and not images:
        logger.warning("Service report analyze: no extractable text or images")
        return _empty()
    if not settings.report_llm_enabled:
        logger.warning("Service report analyze: GROQ_API_KEY missing")
        return _empty()
    try:
        parsed = _call_llm(preamble + text, images if not text else [])
    except Exception as e:
        logger.warning(f"Service report analyze LLM failed ({e})")
        return _empty()
    if not isinstance(parsed, dict):
        return _empty()
    return normalize_extraction(parsed)
