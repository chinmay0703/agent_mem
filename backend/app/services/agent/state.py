"""LangGraph state definition for the chatbot pipeline."""
from __future__ import annotations

from typing import TypedDict

from app.models.schemas import ExtractedMemory


class AgentState(TypedDict, total=False):
    user_id: str
    thread_id: str
    user_message: str

    # Short-term context
    thread_summary: str
    recent_window: str  # rendered as plain text

    # Memory extraction
    extracted: ExtractedMemory
    # Per-turn write results: {"added": list[Triple], "removed": list[Triple]}
    write_result: dict

    # Retrieval planning
    plan: dict  # {"semantic_query": str, "graph_probes": list, "needs_memory": bool}

    # Retrieved memory
    graph_facts: list[dict]
    semantic_hits: list[dict]

    # Final assembled context block (string), used by the response node only.
    context_block: str

    # Output
    response: str
    # List of tool invocations the LLM made during this turn — surfaced to
    # the UI and persisted alongside the assistant message.
    tool_calls: list[dict]
    # Cited RAG sources, in the order they appear in the reply. Each entry
    # is the search_knowledge result row {src_id, kind, snippet, ...}.
    sources: list[dict]
    # Total wall-clock time the response loop took, in milliseconds. Used
    # by the UI to show "Thinking · N steps · X.Xs".
    turn_ms: int
