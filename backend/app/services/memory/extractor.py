"""Extract durable memory from a user turn.

This module emits BOTH adds and deletes — when the user retracts or
contradicts something already in the graph, the extraction LLM names the
exact triple to remove. To make that work, we pass the user's current
graph facts into the extraction prompt.
"""
from __future__ import annotations

from datetime import date

from app.config import get_settings
from app.models.schemas import ExtractedMemory, Triple
from app.prompts.extraction import EXTRACTION_SYSTEM, EXTRACTION_USER
from app.security import truncate_entity
from app.services.graph.neo4j_client import get_graph_client
from app.services.llm import chat_json


def _today() -> str:
    return date.today().isoformat()


def _canonicalize_entity(name: str, known: set[str]) -> str:
    """If the LLM emits a less-specific form of an entity that already exists
    in the user's graph (e.g. "Pusad" when "Pusad, Maharashtra" is already a
    node), replace it with the existing canonical name so the new edge
    attaches to the existing node instead of spawning a duplicate.

    Match is conservative: only triggers on an exact case-insensitive match,
    or on a "head segment" match where the new name is the same as the
    existing name up to a comma or space boundary. We never silently merge
    unrelated entities — "Tesla" will not match "Tesla Model 3" because that
    risks merging a brand with a specific product, but "Pusad" matches
    "Pusad, Maharashtra" because the comma is a clear locality qualifier.
    """
    if not name:
        return name
    nl = name.lower().strip()
    if not nl:
        return name
    # 1. Exact case-insensitive match — always canonicalize.
    for ent in known:
        if ent.lower() == nl:
            return ent
    # 2. Head segment match against locality / qualifier suffixes.
    for ent in known:
        el = ent.lower()
        if not el.startswith(nl):
            continue
        if len(el) == len(nl):
            continue
        boundary = el[len(nl)]
        # Comma is a strong signal ("Pusad, Maharashtra"). Other separators
        # are intentionally NOT matched — too risky.
        if boundary == ",":
            return ent
    return name


def _collect_known_entities(current_facts: list[dict]) -> set[str]:
    s: set[str] = set()
    for f in current_facts:
        if f.get("s"):
            s.add(f["s"])
        if f.get("o"):
            s.add(f["o"])
    return s


def _render_current_facts(facts: list[dict], limit: int = 40) -> str:
    if not facts:
        return "(no facts yet)"
    lines = []
    for f in facts[:limit]:
        lines.append(f"- {f['s']} {f['rel']} {f['o']}")
    return "\n".join(lines)


async def _fetch_current_facts(user_id: str) -> list[dict]:
    """Pull the most reinforced facts for this user. The LLM uses this list
    to decide what to delete — and to avoid re-adding things we already know."""
    try:
        return await get_graph_client().long_term_profile(user_id, limit=40)
    except Exception as e:
        print(f"[extractor] failed to fetch current facts: {e}")
        return []


async def extract_memory(
    user_id: str,
    user_message: str,
    last_assistant_turn: str = "",
) -> ExtractedMemory:
    current_facts = await _fetch_current_facts(user_id)
    raw = await chat_json(
        EXTRACTION_SYSTEM,
        EXTRACTION_USER.format(
            current_date=_today(),
            current_facts=_render_current_facts(current_facts),
            assistant_turn=last_assistant_turn or "(none)",
            user_message=user_message,
        ),
    )
    if not isinstance(raw, dict):
        return ExtractedMemory()

    settings = get_settings()
    known = _collect_known_entities(current_facts)

    triples: list[Triple] = []
    for t in raw.get("triples", []) or []:
        try:
            triple = Triple(**t)
        except Exception:
            continue
        if triple.confidence < settings.MIN_CONFIDENCE:
            continue
        triple.subject = _canonicalize_entity(truncate_entity(triple.subject), known)
        triple.object = _canonicalize_entity(truncate_entity(triple.object), known)
        if not triple.subject or not triple.relation or not triple.object:
            continue
        triples.append(triple)

    deletes: list[Triple] = []
    for t in raw.get("deletes", []) or []:
        try:
            triple = Triple(**t)
        except Exception:
            continue
        triple.subject = _canonicalize_entity(truncate_entity(triple.subject), known)
        triple.object = _canonicalize_entity(truncate_entity(triple.object), known)
        if not triple.subject or not triple.relation or not triple.object:
            continue
        deletes.append(triple)

    summary = (raw.get("summary") or "").strip()
    return ExtractedMemory(triples=triples, deletes=deletes, summary=summary)
