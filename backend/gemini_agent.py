"""Gemini chat-model factory.

Builds cached `ChatGoogleGenerativeAI` clients for the classifier and the
tool-calling orchestrator. This module does not load ERP data, pack a
context JSON blob, or rewrite the user's question.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from chat_activity_log import get_turn_log

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

_DEFAULT_MODEL = "gemini-3.5-flash"
_RETIRED_MODELS = frozenset(
    {
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash-exp",
    }
)

_CACHE_LOCK = threading.Lock()
_LLM_CACHE: dict[tuple[Any, ...], BaseChatModel] = {}
_RETIRED_FALLBACK_LOGGED = False

_OPENAI_PROVIDERS = frozenset({"lmstudio", "openai", "openai_compatible"})


def _clean(value: str | None, default: str = "") -> str:
    return (value or default).strip().strip("'\"")


def provider_name() -> str:
    """Configured LLM provider ('gemini' or 'lmstudio'/'openai')."""
    raw = _clean(os.getenv("LLM_PROVIDER"), "gemini").casefold()
    if raw in _OPENAI_PROVIDERS:
        return "openai"
    return "gemini"


def _thoughts_enabled() -> bool:
    return os.getenv("CHAT_LOG_THOUGHTS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _api_key() -> str:
    key = _clean(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Check the project root .env file."
        )
    return key


def primary_model_name() -> str:
    """Configured chat model for the active provider."""
    if provider_name() == "openai":
        return _clean(os.getenv("LLM_MODEL"), "local-model") or "local-model"
    return _clean(os.getenv("GEMINI_MODEL"), _DEFAULT_MODEL) or _DEFAULT_MODEL


def fallback_model_name() -> str:
    """Optional second model. Blank, retired, or identical-to-primary values are dropped."""
    global _RETIRED_FALLBACK_LOGGED
    if provider_name() == "openai":
        fallback = _clean(os.getenv("LLM_FALLBACK_MODEL") or "")
        return "" if not fallback or fallback == primary_model_name() else fallback
    fallback = _clean(os.getenv("GEMINI_FALLBACK_MODEL") or "")
    if not fallback:
        return ""
    if fallback.casefold() in _RETIRED_MODELS:
        if not _RETIRED_FALLBACK_LOGGED:
            _RETIRED_FALLBACK_LOGGED = True
            log = get_turn_log()
            if log:
                log.thinking(
                    f"Ignoring retired fallback model {fallback}. "
                    "Set GEMINI_FALLBACK_MODEL to a current model or leave it empty."
                )
        return ""
    primary = primary_model_name()
    if fallback == primary:
        return ""
    return fallback


def model_attempt_order() -> list[str]:
    """Primary model, then a non-empty distinct fallback when one is configured."""
    models = [primary_model_name()]
    fallback = fallback_model_name()
    if fallback and fallback not in models:
        models.append(fallback)
    return models


def build_llm(
    model_name: str,
    *,
    temperature: float | None = 0.1,
    max_output_tokens: int | None = 900,
    timeout: float | None = 30,
    max_retries: int = 1,
    include_thoughts: bool | None = None,
    thinking_budget: int | None = None,
) -> BaseChatModel:
    """Construct one chat-model client for the configured provider."""
    if provider_name() == "openai":
        name = _clean(model_name, "local-model") or "local-model"
        base_url = _clean(os.getenv("LLM_BASE_URL"), "http://localhost:1235/v1")
        api_key = _clean(os.getenv("LLM_API_KEY"), "lm-studio") or "lm-studio"
        return ChatOpenAI(
            model=name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_output_tokens,
            timeout=timeout,
            max_retries=max(1, int(max_retries)),
        )

    name = _clean(model_name, _DEFAULT_MODEL) or _DEFAULT_MODEL
    thoughts = _thoughts_enabled() if include_thoughts is None else include_thoughts
    budget = thinking_budget
    if budget is None:
        budget = 256 if thoughts else 0

    kwargs: dict[str, Any] = {
        "model": name,
        "google_api_key": _api_key(),
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "timeout": timeout,
        # 1 = a single request. 0 is treated by this SDK as "use the default".
        "max_retries": max(1, int(max_retries)),
        "include_thoughts": thoughts,
        "thinking_budget": budget,
    }
    try:
        return ChatGoogleGenerativeAI(**kwargs)
    except Exception:
        kwargs.pop("thinking_budget", None)
        kwargs.pop("include_thoughts", None)
        return ChatGoogleGenerativeAI(**kwargs)


def get_llm(
    model_name: str | None = None,
    *,
    temperature: float | None = 0.1,
    max_output_tokens: int | None = 900,
    timeout: float | None = 30,
    max_retries: int = 1,
    include_thoughts: bool | None = None,
    thinking_budget: int | None = None,
) -> BaseChatModel:
    """Return a cached chat-model client for the configured provider."""
    provider = provider_name()
    name = _clean(model_name) if model_name else primary_model_name()
    thoughts = _thoughts_enabled() if include_thoughts is None else include_thoughts
    budget = thinking_budget
    if budget is None:
        budget = 256 if thoughts else 0
    cache_key = (
        provider,
        name,
        temperature,
        max_output_tokens,
        timeout,
        max(1, int(max_retries)),
        thoughts,
        budget,
    )
    with _CACHE_LOCK:
        cached = _LLM_CACHE.get(cache_key)
        if cached is not None:
            return cached
    client = build_llm(
        name,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        max_retries=max_retries,
        include_thoughts=thoughts,
        thinking_budget=budget,
    )
    with _CACHE_LOCK:
        _LLM_CACHE[cache_key] = client
    return client


def clear_llm_cache() -> None:
    """Drop cached clients. Used by tests that swap model settings."""
    with _CACHE_LOCK:
        _LLM_CACHE.clear()
