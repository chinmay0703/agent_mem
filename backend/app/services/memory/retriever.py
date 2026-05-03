"""Plan retrieval, then fetch."""
from __future__ import annotations

from app.prompts.retrieval import RETRIEVAL_SYSTEM, RETRIEVAL_USER
from app.services.llm import chat_json


async def plan_retrieval(user_message: str, thread_summary: str) -> dict:
    """Ask the model where to look. Returns a dict with keys
    'semantic_query' (str), 'graph_probes' (list[dict]), 'needs_memory' (bool)."""
    raw = await chat_json(
        RETRIEVAL_SYSTEM,
        RETRIEVAL_USER.format(
            thread_summary=thread_summary or "(empty)",
            user_message=user_message,
        ),
    )
    if not isinstance(raw, dict):
        return {"semantic_query": "", "graph_probes": [], "needs_memory": False}
    semantic_query = (raw.get("semantic_query") or "").strip()
    probes = raw.get("graph_probes") or []
    if not isinstance(probes, list):
        probes = []
    return {
        "semantic_query": semantic_query,
        "graph_probes": probes,
        "needs_memory": bool(raw.get("needs_memory", bool(semantic_query or probes))),
    }
