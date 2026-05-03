"""Postgres-backed durable history.

Stores threads (one per conversation) and messages (one row per turn). The
short-term memory module reads from here on cold start so a thread keeps
its rolling summary across server restarts. The agent itself never reads
raw history into the LLM — Postgres is the system of record, but the
prompt is still bounded by the rolling summary + recent window.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from app.config import get_settings


_pool: Optional[asyncpg.Pool] = None
_init_lock = asyncio.Lock()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS threads (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    title        TEXT,
    summary      TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS threads_user_idx ON threads(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id           BIGSERIAL PRIMARY KEY,
    thread_id    TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_thread_idx ON messages(thread_id, id);

-- Backfill: existing deployments get the column added without losing rows.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata JSONB;

CREATE TABLE IF NOT EXISTS files (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    thread_id    TEXT,
    filename     TEXT NOT NULL,
    mime         TEXT NOT NULL,
    size_bytes   BIGINT NOT NULL,
    kind         TEXT NOT NULL,             -- csv | xlsx | pdf | docx | txt
    storage_path TEXT NOT NULL,             -- absolute path on disk
    content_text TEXT,                      -- extracted plain-text rendering
    summary      TEXT,                      -- LLM-generated one-paragraph summary
    metadata     JSONB,                     -- columns/sheets/page_count/etc.
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS files_user_idx ON files(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS files_thread_idx ON files(thread_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT,
    action       TEXT NOT NULL,    -- delete_file | delete_thread | delete_user | delete_triple
    target_type  TEXT NOT NULL,    -- file | thread | user | triple
    target_id    TEXT,
    details      JSONB,
    request_id   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_user_idx ON audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_action_idx ON audit_log(action, created_at DESC);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,             -- short, also used as src ref
    user_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,                -- 'file' | 'message'
    text         TEXT NOT NULL,
    -- File-chunk fields (NULL for messages):
    file_id      TEXT,
    filename     TEXT,
    page         INTEGER,
    chunk_idx    INTEGER,
    -- Message-chunk fields (NULL for files):
    thread_id    TEXT,
    message_id   BIGINT,
    role         TEXT,
    -- Common:
    embedded     BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunks_user_idx     ON chunks(user_id);
CREATE INDEX IF NOT EXISTS chunks_file_idx     ON chunks(file_id);
CREATE INDEX IF NOT EXISTS chunks_message_idx  ON chunks(message_id);
CREATE INDEX IF NOT EXISTS chunks_thread_idx   ON chunks(thread_id);
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _init_lock:
        if _pool is not None:
            return _pool
        s = get_settings()
        _pool = await asyncpg.create_pool(
            host=s.PG_HOST,
            port=s.PG_PORT,
            database=s.PG_DATABASE,
            user=s.PG_USER,
            password=s.PG_PASSWORD,
            min_size=1,
            max_size=10,
        )
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def upsert_thread(thread_id: str, user_id: str, title: Optional[str] = None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO threads (id, user_id, title, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (id) DO UPDATE
              SET updated_at = now(),
                  title = COALESCE(EXCLUDED.title, threads.title)
            """,
            thread_id,
            user_id,
            title,
        )


async def update_thread_summary(thread_id: str, summary: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE threads SET summary = $2, updated_at = now() WHERE id = $1",
            thread_id,
            summary,
        )


async def append_message(
    thread_id: str,
    role: str,
    content: str,
    metadata: Optional[dict] = None,
) -> Optional[int]:
    """Insert a message, return its primary-key id so callers can index
    the row in the RAG store."""
    pool = await get_pool()
    payload = json.dumps(metadata) if metadata else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO messages (thread_id, role, content, metadata) "
            "VALUES ($1, $2, $3, $4::jsonb) RETURNING id",
            thread_id,
            role,
            content,
            payload,
        )
        await conn.execute(
            "UPDATE threads SET updated_at = now() WHERE id = $1", thread_id
        )
    return int(row["id"]) if row else None


async def list_threads(user_id: str, limit: int = 50) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.title, t.summary, t.created_at, t.updated_at,
                   COUNT(m.id) AS message_count
            FROM threads t
            LEFT JOIN messages m ON m.thread_id = t.id
            WHERE t.user_id = $1
            GROUP BY t.id
            ORDER BY t.updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
    return [dict(r) for r in rows]


async def get_messages(thread_id: str, limit: int = 200) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, role, content, metadata, created_at
            FROM messages
            WHERE thread_id = $1
            ORDER BY id ASC
            LIMIT $2
            """,
            thread_id,
            limit,
        )
    out = []
    for r in rows:
        d = dict(r)
        meta = d.get("metadata")
        if isinstance(meta, str):
            try:
                d["metadata"] = json.loads(meta)
            except json.JSONDecodeError:
                d["metadata"] = None
        out.append(d)
    return out


async def get_thread(thread_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, title, summary, created_at, updated_at FROM threads WHERE id = $1",
            thread_id,
        )
    return dict(row) if row else None


async def get_recent_messages(thread_id: str, n: int) -> list[dict]:
    """Return the most recent N messages in chronological order."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content
            FROM (
              SELECT id, role, content
              FROM messages
              WHERE thread_id = $1
              ORDER BY id DESC
              LIMIT $2
            ) sub
            ORDER BY id ASC
            """,
            thread_id,
            n,
        )
    return [dict(r) for r in rows]


async def delete_thread(thread_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM threads WHERE id = $1", thread_id)


async def prune_thread_for_regenerate(thread_id: str) -> list[int]:
    """Delete the last user message and everything after it (the assistant
    reply, plus any later turns). Returns the deleted message ids so the
    caller can deindex chunks. No-op if the thread has no user messages."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id FROM messages WHERE thread_id = $1 AND role = 'user' "
                "ORDER BY id DESC LIMIT 1",
                thread_id,
            )
            if not row:
                return []
            cutoff_id = int(row["id"])
            rows = await conn.fetch(
                "DELETE FROM messages WHERE thread_id = $1 AND id >= $2 RETURNING id",
                thread_id,
                cutoff_id,
            )
    return [int(r["id"]) for r in rows]


async def delete_chunks_for_messages(user_id: str, message_ids: list[int]) -> list[str]:
    """Delete chunk rows for the given message ids; return chunk ids so the
    FAISS caller can purge them."""
    if not message_ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "DELETE FROM chunks WHERE user_id = $1 AND message_id = ANY($2::bigint[]) "
            "RETURNING id",
            user_id,
            message_ids,
        )
    return [r["id"] for r in rows]


async def delete_user(user_id: str) -> int:
    """Delete every thread + message + file belonging to a user.
    Returns thread count."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rec = await conn.fetchrow(
            "WITH d AS (DELETE FROM threads WHERE user_id = $1 RETURNING 1) "
            "SELECT count(*) AS n FROM d",
            user_id,
        )
        await conn.execute("DELETE FROM files WHERE user_id = $1", user_id)
    return int(rec["n"]) if rec else 0


# ── Files ────────────────────────────────────────────────────────────────


async def insert_file(
    file_id: str,
    user_id: str,
    thread_id: Optional[str],
    filename: str,
    mime: str,
    size_bytes: int,
    kind: str,
    storage_path: str,
    content_text: Optional[str],
    summary: Optional[str],
    metadata: Optional[dict],
) -> None:
    pool = await get_pool()
    payload = json.dumps(metadata) if metadata else None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO files (id, user_id, thread_id, filename, mime, size_bytes,
                               kind, storage_path, content_text, summary, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
            """,
            file_id, user_id, thread_id, filename, mime, size_bytes,
            kind, storage_path, content_text, summary, payload,
        )


async def list_files(user_id: str, limit: int = 100) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, filename, mime, size_bytes, kind, summary, metadata, created_at
            FROM files
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id, limit,
        )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        m = d.get("metadata")
        if isinstance(m, str):
            try:
                d["metadata"] = json.loads(m)
            except json.JSONDecodeError:
                d["metadata"] = None
        out.append(d)
    return out


async def get_file(user_id: str, file_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, thread_id, filename, mime, size_bytes, kind,
                   storage_path, content_text, summary, metadata, created_at
            FROM files
            WHERE id = $1 AND user_id = $2
            """,
            file_id, user_id,
        )
    if not row:
        return None
    d = dict(row)
    m = d.get("metadata")
    if isinstance(m, str):
        try:
            d["metadata"] = json.loads(m)
        except json.JSONDecodeError:
            d["metadata"] = None
    return d


async def delete_file_row(user_id: str, file_id: str) -> Optional[str]:
    """Delete the row, return storage_path so caller can unlink the file."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM files WHERE id = $1 AND user_id = $2 RETURNING storage_path",
            file_id, user_id,
        )
    return row["storage_path"] if row else None


# ── Audit log ────────────────────────────────────────────────────────────


async def write_audit(
    *,
    user_id: Optional[str],
    action: str,
    target_type: str,
    target_id: Optional[str],
    details: Optional[dict] = None,
    request_id: Optional[str] = None,
) -> None:
    pool = await get_pool()
    payload = json.dumps(details) if details else None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_log (user_id, action, target_type, target_id, details, request_id)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            user_id, action, target_type, target_id, payload, request_id,
        )


async def insert_chunks(rows: list[dict]) -> None:
    """Bulk-insert chunk rows. Each row must have keys: id, user_id, kind,
    text, plus optional file_id/filename/page/chunk_idx OR
    thread_id/message_id/role."""
    if not rows:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO chunks
              (id, user_id, kind, text, file_id, filename, page, chunk_idx,
               thread_id, message_id, role, embedded)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                (
                    r["id"], r["user_id"], r["kind"], r["text"],
                    r.get("file_id"), r.get("filename"), r.get("page"), r.get("chunk_idx"),
                    r.get("thread_id"), r.get("message_id"), r.get("role"),
                    bool(r.get("embedded", False)),
                )
                for r in rows
            ],
        )


async def mark_chunks_embedded(chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE chunks SET embedded = true WHERE id = ANY($1::text[])",
            chunk_ids,
        )


async def get_chunks(user_id: str, ids: list[str]) -> list[dict]:
    """Fetch chunk rows by ids (for the search tool to render snippets +
    metadata). Returns in the order the ids were given."""
    if not ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM chunks WHERE user_id = $1 AND id = ANY($2::text[])",
            user_id, ids,
        )
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


async def get_chunks_for_file(user_id: str, file_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM chunks WHERE user_id = $1 AND file_id = $2",
            user_id, file_id,
        )
    return [dict(r) for r in rows]


async def delete_chunks_for_file(user_id: str, file_id: str) -> list[str]:
    """Delete chunk rows for a file; return the ids deleted so the FAISS
    caller can purge them from the index."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "DELETE FROM chunks WHERE user_id = $1 AND file_id = $2 RETURNING id",
            user_id, file_id,
        )
    return [r["id"] for r in rows]


async def delete_chunks_for_user(user_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM chunks WHERE user_id = $1", user_id)


async def list_audit(user_id: Optional[str], limit: int = 100) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                """
                SELECT id, user_id, action, target_type, target_id, details, request_id, created_at
                FROM audit_log
                WHERE user_id = $1
                ORDER BY id DESC
                LIMIT $2
                """,
                user_id, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, user_id, action, target_type, target_id, details, request_id, created_at
                FROM audit_log
                ORDER BY id DESC
                LIMIT $1
                """,
                limit,
            )
    out = []
    for r in rows:
        d = dict(r)
        details = d.get("details")
        if isinstance(details, str):
            try:
                d["details"] = json.loads(details)
            except json.JSONDecodeError:
                d["details"] = None
        out.append(d)
    return out
