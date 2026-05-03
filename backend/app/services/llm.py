"""Thin async wrapper around the OpenAI client.

Centralized so we can swap providers (Claude, etc.) without touching the agent graph.

Reasoning-model awareness:
  - GPT-5.x and o-series ("reasoning") models use `max_completion_tokens`
    (not `max_tokens`) and reject the `temperature` parameter.
  - We detect by name prefix and adjust the call accordingly. Keeps the
    same call-site API for the rest of the codebase.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import numpy as np
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = get_settings().OPENAI_API_KEY
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. Run the setup wizard first."
            )
        _client = AsyncOpenAI(api_key=key)
    return _client


def reset_client() -> None:
    """Drop the cached OpenAI client so the next call picks up a freshly
    saved API key from runtime config."""
    global _client
    _client = None


def _is_reasoning_model(name: str) -> bool:
    n = (name or "").lower()
    return n.startswith("gpt-5") or n.startswith("o1") or n.startswith("o3") or n.startswith("o4")


def _completion_kwargs(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.3,
    json_mode: bool = False,
    tools: Optional[list[dict]] = None,
) -> dict:
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": settings.MAX_OUTPUT_TOKENS,
    }
    if not _is_reasoning_model(model):
        # Non-reasoning models honor temperature; reasoning models reject it.
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if tools:
        kwargs["tools"] = tools
    return kwargs


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=4))
async def chat(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    json_mode: bool = False,
    model: Optional[str] = None,
) -> str:
    settings = get_settings()
    chosen = model or settings.MODEL_NAME
    kwargs = _completion_kwargs(
        chosen,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        json_mode=json_mode,
    )
    resp = await asyncio.wait_for(
        get_client().chat.completions.create(**kwargs),
        timeout=settings.LLM_TIMEOUT_S,
    )
    return resp.choices[0].message.content or ""


async def chat_json(system: str, user: str, *, temperature: float = 0.1) -> dict:
    """Call the model in JSON mode and parse the result. Returns {} on parse failure."""
    raw = await chat(system, user, temperature=temperature, json_mode=True)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Reasoning models occasionally wrap JSON in fences or prose despite
        # response_format. Try to recover the JSON block.
        if raw:
            try:
                start = raw.find("{")
                end = raw.rfind("}")
                if 0 <= start < end:
                    return json.loads(raw[start : end + 1])
            except Exception:
                pass
        return {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=4))
async def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    temperature: float = 0.3,
    model: Optional[str] = None,
):
    """Invoke chat completions with tools available. Returns the raw choice
    so the caller can inspect tool_calls vs. plain content."""
    settings = get_settings()
    chosen = model or settings.MODEL_NAME
    kwargs = _completion_kwargs(
        chosen, messages, temperature=temperature, tools=tools
    )
    resp = await asyncio.wait_for(
        get_client().chat.completions.create(**kwargs),
        timeout=settings.LLM_TIMEOUT_S,
    )
    return resp.choices[0]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=4))
async def embed(text: str) -> np.ndarray:
    settings = get_settings()
    resp = await get_client().embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=text,
    )
    return np.asarray(resp.data[0].embedding, dtype=np.float32)


async def embed_many(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, get_settings().EMBEDDING_DIM), dtype=np.float32)
    settings = get_settings()
    resp = await get_client().embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    return np.asarray([d.embedding for d in resp.data], dtype=np.float32)
