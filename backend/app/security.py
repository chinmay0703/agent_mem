"""Optional bearer-token gate.

If `API_KEY` is set in the environment, every protected route requires
`Authorization: Bearer <API_KEY>`. If unset, the gate is a no-op (dev mode).
"""
from __future__ import annotations

import re

from fastapi import Header, HTTPException

from app.config import get_settings


_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@-]+$")


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().API_KEY
    if not expected:
        return  # dev mode: open
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


def validate_user_id(user_id: str) -> str:
    """Reject obviously invalid user_ids before we use them as Neo4j keys
    or Postgres FKs. We don't try to be exhaustive — just block the
    egregious cases (empty, too long, control chars, path-traversal-ish)."""
    s = get_settings()
    user_id = (user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    if len(user_id) > s.MAX_USER_ID_LEN:
        raise HTTPException(status_code=400, detail="user_id too long")
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(
            status_code=400,
            detail="user_id may only contain letters, digits, '.', '_', '@', '-'",
        )
    return user_id


def truncate_entity(name: str) -> str:
    """LLM-emitted entity names occasionally come back as paragraphs. Cap them
    so a runaway model can't pollute the graph with huge node names."""
    s = get_settings()
    name = (name or "").strip()
    if len(name) > s.MAX_ENTITY_NAME_CHARS:
        return name[: s.MAX_ENTITY_NAME_CHARS].rstrip()
    return name
