"""LangGraph nodes for the memory-augmented chatbot.

Each node returns ONLY the keys it updates. Parallel branches must not
write to the same key without a reducer — we keep them disjoint
(graph_retrieve writes graph_facts, vector_retrieve writes semantic_hits).
"""
from __future__ import annotations

import re
import time
from datetime import date

from app.config import get_settings
from app.models.schemas import ExtractedMemory
from app.prompts.response import RESPONSE_SYSTEM, RESPONSE_USER
from app.services.agent.compaction import compact_messages, total_tokens
from app.services.agent.state import AgentState
from app.services.agent.tools import TOOL_SCHEMAS, execute_tool
from app.services.llm import chat, chat_with_tools
from app.services.memory.extractor import extract_memory
from app.services.memory.long_term import (
    graph_facts_for_probes,
    long_term_profile,
    store_memory,
)
from app.services.memory.retriever import plan_retrieval
from app.services.memory.short_term import get_short_term
from app.services.storage import postgres as pg


# ---------- Node 1: Input ----------
async def input_node(state: AgentState) -> dict:
    """Seed the state with short-term context for this thread."""
    stm = get_short_term()
    thread_id = state["thread_id"]
    summary = stm.get_summary(thread_id)
    window = stm.recent_window(thread_id)
    rendered = "\n".join(f"{t.role}: {t.content}" for t in window)
    return {"thread_summary": summary, "recent_window": rendered}


# ---------- Node 2: Memory Extraction ----------
async def extract_node(state: AgentState) -> dict:
    last_assistant = ""
    for line in reversed((state.get("recent_window") or "").splitlines()):
        if line.startswith("assistant:"):
            last_assistant = line[len("assistant:") :].strip()
            break
    extracted = await extract_memory(
        state["user_id"], state["user_message"], last_assistant
    )
    return {"extracted": extracted}


# ---------- Node 3: Memory Storage ----------
async def store_node(state: AgentState) -> dict:
    extracted: ExtractedMemory = state.get("extracted") or ExtractedMemory()
    result: dict = {"added": [], "updated": [], "reinforced": [], "removed": []}
    if extracted.triples or extracted.deletes:
        result = await store_memory(
            state["user_id"], extracted, thread_id=state.get("thread_id")
        )
    return {"write_result": result}


# ---------- Node 4: Retrieval Planner ----------
async def plan_node(state: AgentState) -> dict:
    plan = await plan_retrieval(state["user_message"], state.get("thread_summary", ""))
    return {"plan": plan}


# ---------- Node 5: Graph Retrieval (parallel branch) ----------
async def graph_retrieve_node(state: AgentState) -> dict:
    plan = state.get("plan") or {}
    if not plan.get("needs_memory"):
        return {"graph_facts": []}
    probes = plan.get("graph_probes") or []
    facts = await graph_facts_for_probes(state["user_id"], probes)
    return {"graph_facts": facts}


# ---------- Node 6: Vector Retrieval (DISABLED — graph-only mode) ----------
async def vector_retrieve_node(state: AgentState) -> dict:
    return {"semantic_hits": []}


# ---------- Node 7: Context Builder ----------
async def context_node(state: AgentState) -> dict:
    """Assemble the bounded context strings for the response prompt.

    Crucially, we never paste full chat history. We only pass:
      - the rolling summary
      - top-K graph facts (probes + always-on profile)
      - top-K semantic hits
    """
    settings = get_settings()

    # Always-on long-term profile so continuity holds even if planner skipped memory.
    profile = await long_term_profile(state["user_id"])

    graph_facts = list(state.get("graph_facts") or [])
    seen = {(f["s"], f["rel"], f["o"]) for f in graph_facts}
    for f in profile:
        key = (f["s"], f["rel"], f["o"])
        if key not in seen:
            graph_facts.append(f)
            seen.add(key)
    graph_facts = graph_facts[: settings.GRAPH_RETRIEVAL_LIMIT]

    if graph_facts:
        graph_block = "\n".join(_render_fact(f) for f in graph_facts)
    else:
        graph_block = "(none)"

    return {
        "graph_facts": graph_facts,
        "semantic_hits": [],
        "context_block": f"GRAPH:\n{graph_block}",
    }


# ---------- Node 8: Response Generator (tool-calling loop) ----------
async def respond_node(state: AgentState) -> dict:
    """Generate the assistant response with tool access.

    Loop:
      1. Send the model the system prompt + bounded context + user message
         + the tool catalog.
      2. If the model returns a tool call, execute it server-side and
         append the result as a `tool` message.
      3. Stop when the model returns a plain text response, or when we
         hit MAX_TOOL_ITERATIONS as a safety brake.
    Each iteration, we compact the message list to fit under
    MAX_PROMPT_TOKENS so the conversation can never blow context.
    """
    settings = get_settings()
    graph_facts = state.get("graph_facts") or []
    graph_block = "\n".join(_render_fact(f) for f in graph_facts) or "(none)"

    # Inject the user's files list so the LLM has correct file_ids on hand
    # for read_file / query_dataframe / python_sandbox without needing a
    # separate list_files tool call. Bounded — top 20 most-recent files.
    try:
        files = await pg.list_files(state["user_id"], limit=20)
    except Exception:
        files = []
    if files:
        files_block = "\n".join(
            f"- file_id={f['id']}  kind={f['kind']}  name={f['filename']}"
            + (f"  summary: {f['summary'][:120]}" if f.get("summary") else "")
            for f in files
        )
    else:
        files_block = "(no files uploaded)"

    # Build the seed message list. The user-context block is collapsed into
    # the user message so we have a clean (system, user) thread for the
    # tool loop to extend.
    recent = (state.get("recent_window") or "").strip() or "(no prior turns)"
    user_block = RESPONSE_USER.format(
        current_date=date.today().isoformat(),
        thread_summary=state.get("thread_summary") or "(none)",
        recent_turns=recent,
        graph_facts=graph_block,
        files_list=files_block,
        user_message=state["user_message"],
    )
    messages: list[dict] = [
        {"role": "system", "content": RESPONSE_SYSTEM},
        {"role": "user", "content": user_block},
    ]

    tool_calls_log: list[dict] = []
    final_text = ""
    turn_started_at = time.perf_counter()

    # Track every chunk that came back from search_knowledge so we can
    # resolve [src:CHUNK_ID] markers in the model's final reply.
    seen_chunks: dict[str, dict] = {}

    for iteration in range(settings.MAX_TOOL_ITERATIONS):
        iter_started_at = time.perf_counter()
        # Compact before every model call — tool results can be sizable.
        if total_tokens(messages) > settings.MAX_PROMPT_TOKENS:
            messages = compact_messages(messages, settings.MAX_PROMPT_TOKENS)

        try:
            choice = await chat_with_tools(
                messages, TOOL_SCHEMAS, temperature=0.3
            )
        except Exception as e:
            final_text = f"Sorry — I hit an error while answering: {e}"
            break

        msg = choice.message
        # Plain answer — we're done.
        if not getattr(msg, "tool_calls", None):
            final_text = (msg.content or "").strip()
            break

        # The assistant turn that issued the tool call MUST be included in
        # the next request, with the tool_calls intact. Otherwise the API
        # rejects the subsequent `tool` message.
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        # Execute every requested tool serially — the LLM occasionally
        # batches multiple calls in one turn.
        for tc in msg.tool_calls:
            try:
                import json as _json
                args = _json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            tool_started = time.perf_counter()
            result = await execute_tool(tc.function.name, state["user_id"], args)
            tool_ms = int((time.perf_counter() - tool_started) * 1000)
            tool_calls_log.append({
                "name": tc.function.name,
                "args": args,
                "result_preview": result[:600],
                "ms": tool_ms,
                "iteration": iteration,
            })
            # Harvest citable sources from any tool that touches a file or
            # returns ranked chunks. The model can cite via [src:CHUNK_ID]
            # (search_knowledge results) OR [src:FILE_ID] (read_file /
            # query_dataframe / python_sandbox file references).
            try:
                import json as _json
                if tc.function.name == "search_knowledge":
                    parsed = _json.loads(result)
                    for r in parsed.get("results") or []:
                        sid = r.get("src_id")
                        if sid:
                            seen_chunks[sid] = r
                elif tc.function.name in ("read_file", "query_dataframe"):
                    fid = (args or {}).get("file_id")
                    if fid:
                        f = await pg.get_file(state["user_id"], fid)
                        if f:
                            seen_chunks[fid] = {
                                "src_id": fid,
                                "kind": "file",
                                "filename": f.get("filename"),
                                "snippet": (f.get("summary") or "")[:600],
                            }
                elif tc.function.name == "python_sandbox":
                    for fid in (args or {}).get("file_ids") or []:
                        f = await pg.get_file(state["user_id"], fid)
                        if f:
                            seen_chunks[fid] = {
                                "src_id": fid,
                                "kind": "file",
                                "filename": f.get("filename"),
                                "snippet": (f.get("summary") or "")[:600],
                            }
            except Exception:
                pass
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": result,
                }
            )
    else:
        # Hit the iteration cap without a final text answer.
        final_text = (
            "I made multiple tool calls but ran out of iteration budget. "
            "Try a more specific question."
        )

    # Parse [src:CHUNK_ID] markers and assemble the source list. Only
    # keep sources the model actually referenced.
    cited_ids: list[str] = []
    seen = set()
    for m in _SRC_RE.finditer(final_text or ""):
        cid = m.group(1)
        if cid not in seen:
            seen.add(cid)
            cited_ids.append(cid)
    sources: list[dict] = [seen_chunks[c] for c in cited_ids if c in seen_chunks]
    total_ms = int((time.perf_counter() - turn_started_at) * 1000)
    return {
        "response": final_text or "",
        "tool_calls": tool_calls_log,
        "sources": sources,
        "turn_ms": total_ms,
    }


_SRC_RE = re.compile(r"\[src:([A-Za-z0-9_\-]+)\]")


def _render_fact(f: dict) -> str:
    """Render a graph fact for prompt inclusion, with optional validity window."""
    base = f"- {f['s']} {f['rel']} {f['o']}"
    parts = []
    vf = f.get("valid_from")
    vu = f.get("valid_until")
    if vf:
        parts.append(f"from={vf}")
    if vu:
        parts.append(f"until={vu}")
    if parts:
        base += f" [{', '.join(parts)}]"
    return base


# ---------- Node 9: Summarization ----------
async def summarize_node(state: AgentState) -> dict:
    """Persist the new turn (in-memory + Postgres) and trigger summarization
    in the background if we've crossed the threshold. Never blocks the
    response path on summarization.

    The assistant message is persisted WITH its per-turn metadata
    (added/updated/reinforced/removed triples, notes, memories used) so the
    UI dropdown survives a refresh or thread reopen.
    """
    stm = get_short_term()
    await stm.append_persist(
        state["thread_id"], "user", state["user_message"],
        user_id=state["user_id"],
    )
    if state.get("response"):
        meta = _build_turn_metadata(state)
        await stm.append_persist(
            state["thread_id"], "assistant", state["response"],
            metadata=meta, user_id=state["user_id"],
        )
    stm.maybe_summarize_background(state["thread_id"])
    return {}


def _build_turn_metadata(state: AgentState) -> dict:
    wr = state.get("write_result") or {}

    def _view(t) -> dict:
        return {
            "subject": getattr(t, "subject", "") or "",
            "relation": getattr(t, "relation", "") or "",
            "object": getattr(t, "object", "") or "",
            "valid_from": getattr(t, "valid_from", None),
            "valid_until": getattr(t, "valid_until", None),
            "confidence": float(getattr(t, "confidence", 0.0) or 0.0),
        }

    return {
        "added": [_view(t) for t in wr.get("added", [])],
        "updated": [_view(t) for t in wr.get("updated", [])],
        "reinforced": [_view(t) for t in wr.get("reinforced", [])],
        "removed": [_view(t) for t in wr.get("removed", [])],
        "tool_calls": list(state.get("tool_calls") or []),
        "sources": list(state.get("sources") or []),
        "turn_ms": int(state.get("turn_ms") or 0),
    }
