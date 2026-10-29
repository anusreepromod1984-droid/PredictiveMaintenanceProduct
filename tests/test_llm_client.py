"""Groq URL and model candidate helpers."""

from src.services import llm_client


def test_resolve_chat_completions_url():
    assert llm_client._resolve_chat_completions_url().endswith("/openai/v1/chat/completions")


def test_model_candidates_includes_primary_and_fallbacks(monkeypatch):
    monkeypatch.setattr(llm_client.settings, "REPORT_LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(
        llm_client.settings,
        "REPORT_LLM_FALLBACK_MODELS",
        "qwen/qwen3-32b,openai/gpt-oss-20b",
    )
    models = llm_client._model_candidates()
    assert models[0] == "openai/gpt-oss-120b"
    assert "qwen/qwen3-32b" in models
