"""
Shared Groq / OpenAI-compatible chat completions for APMS LLM features.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import httpx

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Services.LLMClient")


def extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in LLM response")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON root must be an object")
    return parsed


def chat_completions_text(
    *,
    system_prompt: str,
    user_content: str,
    temperature: float = 0.35,
    max_tokens: int = 900,
    timeout_s: Optional[float] = None,
) -> str:
    api_key = settings.report_llm_api_key()
    if not api_key:
        raise ValueError("LLM API key not configured (set GROQ_API_KEY)")
    base = (settings.REPORT_LLM_BASE_URL or "https://api.groq.com/openai/v1").rstrip("/")
    model = settings.REPORT_LLM_MODEL or "llama-3.3-70b-versatile"
    url = f"{base}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = float(timeout_s if timeout_s is not None else settings.REPORT_LLM_TIMEOUT_S or 50.0)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("LLM returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise ValueError("LLM returned empty content")
    return str(content)


def chat_completions_json(
    *,
    system_prompt: str,
    user_payload: Dict[str, Any],
    temperature: float = 0.35,
    max_tokens: int = 900,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    raw = chat_completions_text(
        system_prompt=system_prompt,
        user_content=json.dumps(user_payload, ensure_ascii=False),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )
    return extract_json_object(raw)
