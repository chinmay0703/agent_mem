"""Heuristic importance scoring for newly-extracted memory.

Used to weight a summary's persistence in the vector store. The simple
heuristic favors first-person assertions about identity, role, employer,
goals, and explicit preferences — exactly the kinds of facts the
extraction prompt is told to surface.
"""
from __future__ import annotations

from app.models.schemas import ExtractedMemory


_HIGH_VALUE_RELATIONS = {
    "WORKS_AT",
    "HAS_ROLE",
    "HAS_GOAL",
    "LIVES_IN",
    "STUDIES",
    "OWNS",
    "PREFERS",
}


def importance_score(extracted: ExtractedMemory) -> float:
    if not extracted.triples and not extracted.summary:
        return 0.0
    base = 0.4
    if extracted.summary:
        base += 0.1
    if extracted.triples:
        avg_conf = sum(t.confidence for t in extracted.triples) / len(extracted.triples)
        base += 0.2 * avg_conf
        if any(t.relation.upper() in _HIGH_VALUE_RELATIONS for t in extracted.triples):
            base += 0.2
    return max(0.0, min(1.0, base))
