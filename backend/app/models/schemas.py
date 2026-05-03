from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


NodeType = Literal["user", "company", "preference", "goal", "person", "topic", "other"]


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    thread_id: Optional[str] = None
    message: str = Field(..., min_length=1)
    # Drop the prior user+assistant turn from this thread before running the
    # agent — used for the "Regenerate" UI action so the DB doesn't end up
    # with duplicate user messages.
    regenerate: bool = False


class TripleView(BaseModel):
    subject: str
    relation: str
    object: str
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    confidence: float = 0.0


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    extracted_triples: int = 0
    updated_triples: int = 0
    reinforced_triples: int = 0
    removed_triples: int = 0
    added: list[TripleView] = []
    updated: list[TripleView] = []
    reinforced: list[TripleView] = []
    removed: list[TripleView] = []
    tool_calls: list[dict] = []
    sources: list[dict] = []
    turn_ms: int = 0


class Triple(BaseModel):
    subject: str
    relation: str
    object: str
    subject_type: NodeType = "other"
    object_type: NodeType = "other"
    confidence: float = 0.7
    # Time-bound facts: when did this start being true / when does it stop?
    # Both are ISO date strings (YYYY-MM-DD). Optional — most facts have neither.
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class ExtractedMemory(BaseModel):
    # Triples to upsert into the graph (default field — keeps callers backward-compat).
    triples: list[Triple] = []
    # Triples the user has retracted/contradicted in this turn — to be deleted.
    deletes: list[Triple] = []
    summary: str = ""


class GraphNode(BaseModel):
    id: str
    type: NodeType


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    confidence: float = 1.0
    timestamp: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class GraphPayload(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


