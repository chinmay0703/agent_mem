from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.security import require_api_key, validate_user_id
from app.services.graph.neo4j_client import get_graph_client
from app.services.storage import postgres as pg


router = APIRouter(
    prefix="/threads", tags=["threads"], dependencies=[Depends(require_api_key)]
)


class ThreadSummary(BaseModel):
    id: str
    title: Optional[str]
    summary: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class Message(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    metadata: Optional[dict] = None


class ThreadDetail(BaseModel):
    id: str
    title: Optional[str]
    summary: str
    created_at: datetime
    updated_at: datetime
    messages: list[Message]


@router.get("/{user_id}", response_model=list[ThreadSummary])
async def list_user_threads(user_id: str, limit: int = 50):
    user_id = validate_user_id(user_id)
    limit = max(1, min(200, int(limit)))
    rows = await pg.list_threads(user_id, limit=limit)
    return [ThreadSummary(**r) for r in rows]


@router.get("/{user_id}/{thread_id}", response_model=ThreadDetail)
async def get_thread_detail(user_id: str, thread_id: str):
    user_id = validate_user_id(user_id)
    thread = await pg.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Thread not found")
    msgs = await pg.get_messages(thread_id)
    return ThreadDetail(
        id=thread["id"],
        title=thread.get("title"),
        summary=thread.get("summary", ""),
        created_at=thread["created_at"],
        updated_at=thread["updated_at"],
        messages=[Message(**m) for m in msgs],
    )


@router.delete("/{user_id}/{thread_id}")
async def delete_thread(user_id: str, thread_id: str, request: Request):
    """Delete a thread, its messages, AND any graph triples that were created
    in this thread and aren't shared with another thread."""
    user_id = validate_user_id(user_id)
    thread = await pg.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        edges_removed = await get_graph_client().delete_triples_for_thread(
            user_id, thread_id
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Graph cleanup failed: {e}"
        ) from e
    await pg.delete_thread(thread_id)
    from app.services.memory.short_term import get_short_term

    get_short_term().forget(thread_id)
    await pg.write_audit(
        user_id=user_id,
        action="delete_thread",
        target_type="thread",
        target_id=thread_id,
        details={"edges_removed": edges_removed},
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "deleted", "edges_removed": edges_removed}
