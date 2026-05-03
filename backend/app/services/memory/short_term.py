"""Short-term (thread-level) memory.

Holds a rolling buffer of recent turns and a running summary, keyed by
thread_id. Postgres is the system of record — this module is an in-process
cache that hydrates lazily from Postgres on the first access of a thread,
so the rolling summary survives server restarts.

The agent compresses the buffer into the summary every N turns so we never
feed full chat history into the LLM.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from app.config import get_settings
from app.prompts.summarization import SUMMARIZATION_SYSTEM, SUMMARIZATION_USER
from app.services.llm import chat
from app.services.storage import postgres as pg


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ThreadState:
    thread_id: str
    user_id: str
    summary: str = ""
    buffer: list[Turn] = field(default_factory=list)
    turns_since_summary: int = 0
    hydrated: bool = False  # have we loaded from Postgres yet?


class ShortTermMemory:
    def __init__(self) -> None:
        s = get_settings()
        self.summarize_every = s.SUMMARIZE_EVERY_N
        self.window = s.SHORT_TERM_TURNS
        self._threads: dict[str, ThreadState] = {}
        self._user_threads: dict[str, list[str]] = defaultdict(list)
        self._lock = threading.Lock()

    def _get_or_register(self, user_id: str, thread_id: Optional[str]) -> ThreadState:
        """In-memory registration. Hydration from Postgres happens separately
        via `ensure_hydrated` so we keep the lock non-async."""
        with self._lock:
            if thread_id and thread_id in self._threads:
                return self._threads[thread_id]
            tid = thread_id or uuid.uuid4().hex
            st = ThreadState(thread_id=tid, user_id=user_id, hydrated=not bool(thread_id))
            self._threads[tid] = st
            self._user_threads[user_id].append(tid)
            return st

    async def get_or_create(self, user_id: str, thread_id: Optional[str]) -> ThreadState:
        st = self._get_or_register(user_id, thread_id)
        # Persist the thread row + hydrate prior summary/window from Postgres
        # if we haven't already.
        await pg.upsert_thread(st.thread_id, user_id)
        if not st.hydrated:
            await self._hydrate(st)
        return st

    async def _hydrate(self, st: ThreadState) -> None:
        """Pull the persisted summary and recent N messages from Postgres
        into the in-memory buffer. Idempotent."""
        thread = await pg.get_thread(st.thread_id)
        recent = await pg.get_recent_messages(st.thread_id, self.window)
        with self._lock:
            if st.hydrated:
                return
            if thread and thread.get("summary"):
                st.summary = thread["summary"]
            st.buffer = [Turn(role=m["role"], content=m["content"]) for m in recent]
            st.turns_since_summary = 0
            st.hydrated = True

    def append(self, thread_id: str, role: str, content: str) -> None:
        with self._lock:
            st = self._threads.get(thread_id)
            if st is None:
                return
            st.buffer.append(Turn(role=role, content=content))
            if role == "user":
                st.turns_since_summary += 1
            if len(st.buffer) > self.window * 4:
                st.buffer = st.buffer[-self.window * 2 :]

    async def append_persist(
        self,
        thread_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
        user_id: Optional[str] = None,
    ) -> Optional[int]:
        """Append to the in-memory buffer AND persist to Postgres, then
        index the message into the user's RAG store so future turns can
        cite it cross-thread.

        Returns the inserted message id (or None on failure). `user_id`
        is required for RAG indexing; if omitted we skip RAG.
        """
        self.append(thread_id, role, content)
        try:
            msg_id = await pg.append_message(thread_id, role, content, metadata=metadata)
        except Exception as e:
            print(f"[short_term] pg append failed: {e}")
            return None
        if msg_id and user_id:
            try:
                from app.services.rag.indexer import index_message

                await index_message(user_id, thread_id, msg_id, role, content)
            except Exception as e:
                print(f"[short_term] rag index_message failed: {e}")
        return msg_id

    def recent_window(self, thread_id: str) -> list[Turn]:
        with self._lock:
            st = self._threads.get(thread_id)
            if st is None:
                return []
            return list(st.buffer[-self.window :])

    def should_summarize(self, thread_id: str) -> bool:
        with self._lock:
            st = self._threads.get(thread_id)
            if st is None:
                return False
            return st.turns_since_summary >= self.summarize_every

    def get_summary(self, thread_id: str) -> str:
        with self._lock:
            st = self._threads.get(thread_id)
            return st.summary if st else ""

    def _drain_unsummarized(self, thread_id: str) -> tuple[str, list[Turn]]:
        with self._lock:
            st = self._threads.get(thread_id)
            if st is None:
                return "", []
            turns = list(st.buffer)
            prior = st.summary
            st.turns_since_summary = 0
            return prior, turns

    def _commit_summary(self, thread_id: str, summary: str) -> None:
        with self._lock:
            st = self._threads.get(thread_id)
            if st is None:
                return
            st.summary = summary
            st.buffer = st.buffer[-self.window :]

    async def summarize_now(self, thread_id: str) -> str:
        prior, turns = self._drain_unsummarized(thread_id)
        if not turns:
            return prior
        rendered = "\n".join(f"{t.role}: {t.content}" for t in turns)
        new_summary = await chat(
            SUMMARIZATION_SYSTEM,
            SUMMARIZATION_USER.format(prior_summary=prior or "(none)", new_turns=rendered),
            temperature=0.2,
        )
        new_summary = (new_summary or "").strip()
        if new_summary:
            self._commit_summary(thread_id, new_summary)
            try:
                await pg.update_thread_summary(thread_id, new_summary)
            except Exception as e:
                print(f"[short_term] pg summary update failed: {e}")
        return new_summary or prior

    def forget(self, thread_id: str) -> None:
        """Drop a thread from in-memory state — used when a thread is deleted
        so a recreated id (rare) starts clean."""
        with self._lock:
            st = self._threads.pop(thread_id, None)
            if st is not None:
                tids = self._user_threads.get(st.user_id)
                if tids and thread_id in tids:
                    tids.remove(thread_id)

    def forget_user(self, user_id: str) -> None:
        """Drop all in-memory state for a user."""
        with self._lock:
            tids = list(self._user_threads.get(user_id, []))
            for tid in tids:
                self._threads.pop(tid, None)
            self._user_threads.pop(user_id, None)

    def maybe_summarize_background(self, thread_id: str) -> None:
        if not self.should_summarize(thread_id):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.summarize_now(thread_id))


_singleton: Optional[ShortTermMemory] = None


def get_short_term() -> ShortTermMemory:
    global _singleton
    if _singleton is None:
        _singleton = ShortTermMemory()
    return _singleton
