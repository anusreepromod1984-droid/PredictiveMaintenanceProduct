"""
Shared Groq / OpenAI-compatible chat completions for APMS LLM features.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger("Services.LLMClient")

_DEFAULT_GROQ_BASE = "https://api.groq.com/openai/v1"


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


def _resolve_chat_completions_url() -> str:
    base = (settings.REPORT_LLM_BASE_URL or _DEFAULT_GROQ_BASE).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _model_candidates() -> List[str]:
    primary = (settings.REPORT_LLM_MODEL or "openai/gpt-oss-120b").strip()
    out: List[str] = [primary] if primary else []
    raw = (getattr(settings, "REPORT_LLM_FALLBACK_MODELS", None) or "").strip()
    if raw:
        for part in raw.split(","):
            m = part.strip()
            if m and m not in out:
                out.append(m)
    return out


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

    url = _resolve_chat_completions_url()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = float(timeout_s if timeout_s is not None else settings.REPORT_LLM_TIMEOUT_S or 50.0)

    last_error: Optional[Exception] = None
    for model in _model_candidates():
        for use_json_mode in (True, False):
            payload: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 404:
                        detail = resp.text[:300]
                        logger.warning(
                            f"Groq model not found ({model}); "
                            f"404 on {url} — {detail}"
                        )
                        last_error = httpx.HTTPStatusError(
                            f"404 for model {model}",
                            request=resp.request,
                            response=resp,
                        )
                        break  # try next model
                    if resp.status_code in (400, 422) and use_json_mode:
                        logger.debug(f"Groq rejected json_object for {model}; retrying without")
                        continue
                    resp.raise_for_status()
                    body = resp.json()
                choices = body.get("choices") or []
                if not choices:
                    raise ValueError("LLM returned no choices")
                message = choices[0].get("message") or {}
                content = message.get("content")
                if not content:
                    raise ValueError("LLM returned empty content")
                if model != _model_candidates()[0]:
                    logger.info(f"Groq chat succeeded using fallback model {model}")
                return str(content)
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response is not None and e.response.status_code == 404:
                    break
                if use_json_mode:
                    continue
                raise
            except Exception as e:
                last_error = e
                if use_json_mode:
                    continue
                raise

    if last_error:
        raise last_error
    raise RuntimeError("Groq chat completions failed for all configured models")


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
