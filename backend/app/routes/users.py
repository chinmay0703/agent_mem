"""User-level admin endpoints — currently just a GDPR-style full delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.security import require_api_key, validate_user_id
from app.services.graph.neo4j_client import get_graph_client
from app.services.memory.short_term import get_short_term
from app.services.storage import postgres as pg


router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[Depends(require_api_key)]
)


@router.delete("/{user_id}")
async def delete_user(user_id: str, request: Request):
    """Wipe everything we have on this user — graph nodes/edges, all threads
    and messages, and any in-memory short-term buffers.

    This is the GDPR/right-to-be-forgotten endpoint. It is irreversible.
    """
    user_id = validate_user_id(user_id)
    try:
        nodes_removed = await get_graph_client().delete_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"graph wipe failed: {e}") from e
    try:
        threads_removed = await pg.delete_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db wipe failed: {e}") from e
    get_short_term().forget_user(user_id)
    await pg.write_audit(
        user_id=user_id,
        action="delete_user",
        target_type="user",
        target_id=user_id,
        details={
            "graph_nodes_removed": nodes_removed,
            "threads_removed": threads_removed,
        },
        request_id=getattr(request.state, "request_id", None),
    )
    return {
        "status": "deleted",
        "user_id": user_id,
        "graph_nodes_removed": nodes_removed,
        "threads_removed": threads_removed,
    }
