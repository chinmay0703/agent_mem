from fastapi import APIRouter, Depends, HTTPException

from app.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse
from app.security import require_api_key, validate_user_id
from app.services.agent.graph import run_agent
from app.services.concurrency import get_chat_lock, get_chat_rate_limiter
from app.services.memory.short_term import get_short_term
from app.services.rag.indexer import deindex_messages
from app.services.storage import postgres as pg


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    settings = get_settings()
    user_id = validate_user_id(req.user_id)

    if len(req.message) > settings.MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"message exceeds {settings.MAX_MESSAGE_CHARS} characters",
        )

    # Per-user rate limiting (token bucket).
    bucket = get_chat_rate_limiter(settings.RATE_LIMIT_PER_MIN)
    if not await bucket.take(user_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    # Per-(user, thread) lock so concurrent requests on the same thread
    # don't race on the rolling summary or trip the dedup logic.
    lock_key = f"{user_id}::{req.thread_id or 'new'}"
    lock = await get_chat_lock().acquire(lock_key)
    async with lock:
        # Regenerate: drop the prior user+assistant pair from Postgres and
        # the FAISS chunk index, then evict the in-memory thread state so
        # the agent's next access re-hydrates from the now-pruned DB.
        # Without this the DB grows a duplicate user message every retry.
        if req.regenerate and req.thread_id:
            deleted_ids = await pg.prune_thread_for_regenerate(req.thread_id)
            if deleted_ids:
                await deindex_messages(user_id, deleted_ids)
                get_short_term().forget(req.thread_id)
        try:
            result = await run_agent(user_id, req.thread_id, req.message)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"agent error: {e}") from e

    return ChatResponse(
        response=result["response"],
        thread_id=result["thread_id"],
        extracted_triples=result["extracted_triples"],
        updated_triples=result.get("updated_triples", 0),
        reinforced_triples=result.get("reinforced_triples", 0),
        removed_triples=result.get("removed_triples", 0),
        added=result.get("added", []),
        updated=result.get("updated", []),
        reinforced=result.get("reinforced", []),
        removed=result.get("removed", []),
        tool_calls=result.get("tool_calls", []),
        sources=result.get("sources", []),
        turn_ms=int(result.get("turn_ms") or 0),
    )
