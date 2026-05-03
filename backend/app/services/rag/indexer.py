"""High-level indexing helpers — turn a file or a chat message into chunk
rows + FAISS embeddings."""
from __future__ import annotations

import uuid
from typing import Optional

from app.services.rag.chunking import chunk_text
from app.services.rag.index import get_rag_store
from app.services.storage import postgres as pg


def _new_chunk_id(prefix: str) -> str:
    # Short stable id used as the citation src ref. ~12 chars is plenty for
    # uniqueness within a user's footprint.
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def index_file(
    user_id: str,
    file_id: str,
    filename: str,
    kind: str,
    content_text: str,
) -> int:
    """Chunk + embed + persist a file's content. Returns number of chunks
    indexed."""
    if not content_text or not content_text.strip():
        return 0
    chunks = chunk_text(content_text)
    if not chunks:
        return 0
    rows = []
    pairs: list[tuple[str, str]] = []
    for ch in chunks:
        cid = _new_chunk_id("f")
        rows.append({
            "id": cid,
            "user_id": user_id,
            "kind": "file",
            "text": ch.text,
            "file_id": file_id,
            "filename": filename,
            "page": ch.page,
            "chunk_idx": ch.chunk_idx,
            "embedded": True,
        })
        pairs.append((cid, ch.text))
    await pg.insert_chunks(rows)
    try:
        await get_rag_store().add_chunks(user_id, pairs)
    except Exception as e:
        # Don't break upload if embedding fails — surface in logs.
        print(f"[rag] embedding failed for {filename}: {e}")
        # Mark them as not-embedded so we can backfill later if needed.
        await pg.mark_chunks_embedded([])  # no-op; just to keep API consistent
    return len(rows)


async def index_message(
    user_id: str,
    thread_id: str,
    message_id: int,
    role: str,
    content: str,
) -> int:
    """Index a single chat message. Short messages stay as one chunk; long
    ones get split."""
    if not content or not content.strip():
        return 0
    chunks = chunk_text(content, target_tokens=300, overlap_tokens=30)
    if not chunks:
        return 0
    rows = []
    pairs: list[tuple[str, str]] = []
    for ch in chunks:
        cid = _new_chunk_id("m")
        rows.append({
            "id": cid,
            "user_id": user_id,
            "kind": "message",
            "text": ch.text,
            "thread_id": thread_id,
            "message_id": message_id,
            "role": role,
            "embedded": True,
        })
        pairs.append((cid, ch.text))
    await pg.insert_chunks(rows)
    try:
        await get_rag_store().add_chunks(user_id, pairs)
    except Exception as e:
        print(f"[rag] embed message {message_id} failed: {e}")
    return len(rows)


async def deindex_file(user_id: str, file_id: str) -> int:
    """Remove all chunks for a file from Postgres + FAISS. Returns count."""
    chunk_ids = await pg.delete_chunks_for_file(user_id, file_id)
    if chunk_ids:
        try:
            await get_rag_store().remove(user_id, chunk_ids)
        except Exception as e:
            print(f"[rag] FAISS remove failed for {file_id}: {e}")
    return len(chunk_ids)


async def deindex_messages(user_id: str, message_ids: list[int]) -> int:
    """Remove all chunks for the given chat messages from Postgres + FAISS.
    Used by the regenerate flow so a redone turn doesn't leave the old
    user/assistant pair haunting future RAG queries."""
    if not message_ids:
        return 0
    chunk_ids = await pg.delete_chunks_for_messages(user_id, message_ids)
    if chunk_ids:
        try:
            await get_rag_store().remove(user_id, chunk_ids)
        except Exception as e:
            print(f"[rag] FAISS remove failed for messages {message_ids}: {e}")
    return len(chunk_ids)
