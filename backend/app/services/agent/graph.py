"""LangGraph wiring for the chatbot.

Flow:
  input -> extract -> store -> plan -> [graph_retrieve, vector_retrieve]
        -> context -> respond -> summarize -> END

Storage runs in parallel-conceptually (its result is independent of retrieval),
but to keep the graph linear and readable we run it sequentially before
retrieval. The two retrieval branches fan out from `plan` and merge at
`context`.
"""
from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph

from app.services.agent.nodes import (
    context_node,
    extract_node,
    graph_retrieve_node,
    input_node,
    plan_node,
    respond_node,
    store_node,
    summarize_node,
    vector_retrieve_node,
)
from app.services.agent.state import AgentState


def build_agent_graph():
    g = StateGraph(AgentState)

    g.add_node("input", input_node)
    g.add_node("extract", extract_node)
    g.add_node("store", store_node)
    g.add_node("plan", plan_node)
    g.add_node("graph_retrieve", graph_retrieve_node)
    g.add_node("vector_retrieve", vector_retrieve_node)
    g.add_node("context", context_node)
    g.add_node("respond", respond_node)
    g.add_node("summarize", summarize_node)

    g.add_edge(START, "input")
    g.add_edge("input", "extract")
    g.add_edge("extract", "store")
    g.add_edge("store", "plan")

    # Fan-out to retrieval branches in parallel.
    g.add_edge("plan", "graph_retrieve")
    g.add_edge("plan", "vector_retrieve")

    # Both branches feed the context builder.
    g.add_edge("graph_retrieve", "context")
    g.add_edge("vector_retrieve", "context")

    g.add_edge("context", "respond")
    g.add_edge("respond", "summarize")
    g.add_edge("summarize", END)

    return g.compile()


_compiled = None


def get_agent():
    global _compiled
    if _compiled is None:
        _compiled = build_agent_graph()
    return _compiled


async def run_agent(user_id: str, thread_id: Optional[str], message: str) -> dict:
    from app.models.schemas import ExtractedMemory
    from app.services.memory.short_term import get_short_term

    stm = get_short_term()
    state = await stm.get_or_create(user_id, thread_id)

    init: AgentState = {
        "user_id": user_id,
        "thread_id": state.thread_id,
        "user_message": message,
    }
    final = await get_agent().ainvoke(init)
    write_result = final.get("write_result") or {}
    added = write_result.get("added", [])
    updated = write_result.get("updated", [])
    reinforced = write_result.get("reinforced", [])
    removed = write_result.get("removed", [])
    return {
        "thread_id": state.thread_id,
        "response": final.get("response", ""),
        "extracted_triples": len(added),
        "updated_triples": len(updated),
        "reinforced_triples": len(reinforced),
        "removed_triples": len(removed),
        "added": [_triple_view(t) for t in added],
        "updated": [_triple_view(t) for t in updated],
        "reinforced": [_triple_view(t) for t in reinforced],
        "removed": [_triple_view(t) for t in removed],
        "tool_calls": final.get("tool_calls") or [],
        "sources": final.get("sources") or [],
        "turn_ms": int(final.get("turn_ms") or 0),
    }


def _triple_view(t) -> dict:
    """Compact wire shape for a triple — just enough for the UI dropdown."""
    return {
        "subject": getattr(t, "subject", "") or "",
        "relation": getattr(t, "relation", "") or "",
        "object": getattr(t, "object", "") or "",
        "valid_from": getattr(t, "valid_from", None),
        "valid_until": getattr(t, "valid_until", None),
        "confidence": float(getattr(t, "confidence", 0.0) or 0.0),
    }
