"""Thin Groq client (OpenAI-compatible chat completions).

The LLM only interprets and explains. Every number it may mention is passed in
as verified backend data; callers must not ask it to calculate.

If the configured model is not available to the key, the client picks the first
available model from PREFERRED (checked once via /models).
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from . import config

log = logging.getLogger("shared.llm")
PREFERRED = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
_resolved: dict = {}


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(config.GROQ_API_KEY)


def _headers():
    return {"Authorization": f"Bearer {config.GROQ_API_KEY}"}


def model() -> str:
    if "model" in _resolved:
        return _resolved["model"]
    want = config.GROQ_MODEL
    try:
        r = httpx.get(f"{config.GROQ_BASE_URL}/models", headers=_headers(), timeout=15)
        ids = {m["id"] for m in r.json().get("data", [])}
        if want in ids:
            _resolved["model"] = want
        else:
            pick = next((m for m in PREFERRED if m in ids), want)
            if pick != want:
                log.warning("Groq model %s not available; using %s", want, pick)
            _resolved["model"] = pick
    except Exception:
        _resolved["model"] = want
    return _resolved["model"]


def _available_models() -> set:
    if "ids" not in _resolved:
        try:
            r = httpx.get(f"{config.GROQ_BASE_URL}/models", headers=_headers(), timeout=15)
            _resolved["ids"] = {m["id"] for m in r.json().get("data", [])}
        except Exception:
            _resolved["ids"] = set()
    return _resolved["ids"]


def chat(messages: list[dict], temperature: float = 0.3, max_tokens: int = 700, json_mode: bool = False) -> str:
    """Rate-limit aware: on HTTP 429 wait the advised time (<= 6 s) and retry, otherwise
    fall back to the next available model, which has its own token quota."""
    primary = model()
    tried = []
    candidates = [primary] + [m for m in PREFERRED if m != primary and m in _available_models()]
    for m in candidates[:3]:
        for attempt in range(2):
            try:
                return _chat_once(m, messages, temperature, max_tokens, json_mode)
            except _RateLimited as rl:
                tried.append(m)
                if rl.wait is not None and rl.wait <= 6 and attempt == 0:
                    time.sleep(rl.wait + 0.3)
                    continue
                break
    raise LLMUnavailable(f"Rate limited on {', '.join(dict.fromkeys(tried)) or 'all models'}")


class _RateLimited(Exception):
    def __init__(self, wait):
        self.wait = wait


def _chat_once(m: str, messages, temperature, max_tokens, json_mode) -> str:
    if not config.GROQ_API_KEY:
        raise LLMUnavailable("GROQ_API_KEY not set")
    payload: dict = {"model": m, "messages": messages, "temperature": temperature}
    if "gpt-oss" in m or "qwen3" in m:
        # reasoning models: reasoning tokens count toward the budget
        payload["max_completion_tokens"] = max_tokens + 1200
        payload["reasoning_effort"] = "low"
        if "qwen3" in m:
            payload["reasoning_format"] = "hidden"
    else:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(f"{config.GROQ_BASE_URL}/chat/completions", headers=_headers(), json=payload, timeout=60)
        r.raise_for_status()
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not content:
            raise LLMUnavailable("empty completion")
        return content
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            m_wait = re.search(r"try again in ([\d.]+)\s*s", exc.response.text)
            log.info("Groq rate limit on %s (retry in %ss)", m, m_wait.group(1) if m_wait else "?")
            raise _RateLimited(float(m_wait.group(1)) if m_wait else None) from exc
        log.warning("Groq HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        raise LLMUnavailable(f"Groq error {exc.response.status_code}") from exc
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        log.warning("Groq call failed: %s", exc)
        raise LLMUnavailable(str(exc)) from exc


def chat_json(messages: list[dict], **kw) -> dict:
    text = chat(messages, json_mode=True, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise LLMUnavailable("LLM did not return JSON")
