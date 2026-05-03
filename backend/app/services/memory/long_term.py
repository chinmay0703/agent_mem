"""Long-term memory facade — graph only.

Semantic memory was removed at the user's request. Everything we remember
about a user lives in Neo4j as (subject, relation, object) triples. The
agent talks to this module for both reading and writing.
"""
from __future__ import annotations

from app.config import get_settings
from app.models.schemas import ExtractedMemory, Triple  # noqa: F401
from app.services.graph.neo4j_client import get_graph_client


async def store_memory(
    user_id: str,
    extracted: ExtractedMemory,
    thread_id: str | None = None,
) -> dict:
    """Apply add+update+delete operations from one extraction pass.

    Returns the actual triples written/changed so the agent can surface
    "what happened" detail to the UI.
    """
    graph = get_graph_client()

    # If the LLM emitted both an add and a delete for the same (s,r,o), treat
    # it as a single update (e.g. a date change phrased as a correction).
    add_keys = {
        (t.subject.strip(), t.relation.upper().strip(), t.object.strip())
        for t in extracted.triples
    }
    filtered_deletes = [
        t
        for t in extracted.deletes
        if (t.subject.strip(), t.relation.upper().strip(), t.object.strip())
        not in add_keys
    ]

    added: list[Triple] = []
    updated: list[Triple] = []
    reinforced: list[Triple] = []
    for triple in extracted.triples:
        try:
            status = await graph.upsert_triple(user_id, triple, thread_id=thread_id)
        except Exception as e:
            print(f"[long_term] upsert failed for {triple}: {e}")
            continue
        if status == "created":
            added.append(triple)
        elif status == "updated":
            updated.append(triple)
        else:  # "reinforced" — same fact re-asserted, count++ on the edge
            reinforced.append(triple)

    removed: list[Triple] = []
    for triple in filtered_deletes:
        try:
            ok = await graph.delete_triple(user_id, triple)
        except Exception as e:
            print(f"[long_term] delete failed for {triple}: {e}")
            ok = False
        if ok:
            removed.append(triple)

    return {
        "added": added,
        "updated": updated,
        "reinforced": reinforced,
        "removed": removed,
    }


async def graph_facts_for_probes(
    user_id: str, probes: list[dict], limit: int = 0
) -> list[dict]:
    """Resolve a list of LLM-emitted probes against the user's graph."""
    settings = get_settings()
    graph = get_graph_client()
    cap = limit or settings.GRAPH_RETRIEVAL_LIMIT
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []

    for p in probes or []:
        if not isinstance(p, dict):
            continue
        if "subject" in p and "relation" in p:
            facts = await graph.find_by_subject_relation(
                user_id, p["subject"], p["relation"], limit=cap
            )
        elif "entity" in p:
            facts = await graph.find_by_entity(user_id, p["entity"], limit=cap)
        else:
            continue
        for f in facts:
            key = (f["s"], f["rel"], f["o"])
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
            if len(out) >= cap:
                return out
    return out


async def long_term_profile(user_id: str) -> list[dict]:
    """Always-on grounding facts for the response prompt — includes 2-hop
    facts about people/things the user has mentioned (e.g. the user's
    brother's job, the user's project's tech stack)."""
    settings = get_settings()
    return await get_graph_client().long_term_profile(
        user_id, limit=settings.GRAPH_RETRIEVAL_LIMIT
    )
