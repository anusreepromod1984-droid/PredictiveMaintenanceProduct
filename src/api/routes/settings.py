"""
Settings Router — stores mutable runtime preferences (e.g. alert language)
that external systems (frontend, API clients) can update without redeploying.
"""
from __future__ import annotations

import threading
from fastapi import APIRouter
from pydantic import BaseModel
from src.utils.logger import get_logger

logger = get_logger("APMS.Settings")

router = APIRouter(prefix="/settings", tags=["settings"])

_lock = threading.Lock()
_state: dict = {
    "alert_language": "en",   # BCP-47 code: "en", "hi", "ta", "de", …
}

def get_alert_language() -> str:
    with _lock:
        return _state.get("alert_language", "en")

class AlertLanguagePayload(BaseModel):
    language: str

class AlertLanguageResponse(BaseModel):
    alert_language: str
    message: str

@router.get("/alert-language", response_model=AlertLanguageResponse, summary="Get alert language")
def get_alert_language_route() -> AlertLanguageResponse:
    lang = get_alert_language()
    return AlertLanguageResponse(alert_language=lang, message="OK")

@router.post("/alert-language", response_model=AlertLanguageResponse, summary="Set alert language")
def set_alert_language(payload: AlertLanguagePayload) -> AlertLanguageResponse:
    code = payload.language.lower().split("-")[0].strip()
    if not code:
        code = "en"
    with _lock:
        _state["alert_language"] = code
    logger.info(f"[Settings] Alert language set to '{code}'")
    return AlertLanguageResponse(
        alert_language=code,
        message=f"Alert language set to '{code}'",
    )
