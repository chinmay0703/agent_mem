"""Per-user FAISS index for RAG chunks (file content + chat messages).

One index per user, persisted to disk. Vector ids are 64-bit hashes of the
chunk's string id; we keep the id ↔ string-id map in a JSON sidecar so we
can resolve search hits back to chunk rows in Postgres.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Optional

import faiss
import numpy as np

from app.config import get_settings
from app.services.llm import embed_many


def _root() -> Path:
    s = get_settings()
    p = s.DATA_DIR / "rag"
    p.mkdir(parents=True, exist_ok=True)
    return p


class _UserIndex:
    """Owns one FAISS index for one user. Single-thread access is enforced
    via an internal lock; actual ANN ops are run in a thread executor by the
    async wrapper."""

    def __init__(self, user_id: str, dim: int) -> None:
        self.user_id = user_id
        self.dim = dim
        self.dir = _root() / user_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.faiss"
        self.meta_path = self.dir / "ids.json"
        self.lock = threading.Lock()
        # int64_id → chunk_id_string. We use abs(hash(chunk_id)) % 2**63
        # as the FAISS int id so collisions are essentially impossible
        # within one user's footprint.
        self.id_map: dict[int, str] = {}
        self.index = self._load_or_create()

    def _load_or_create(self) -> faiss.IndexIDMap:
        if self.index_path.exists() and self.meta_path.exists():
            base = faiss.read_index(str(self.index_path))
            with self.meta_path.open() as f:
                self.id_map = {int(k): v for k, v in json.load(f).items()}
            return base
        return faiss.IndexIDMap(faiss.IndexFlatIP(self.dim))

    def _persist(self) -> None:
        faiss.write_index(self.index, str(self.index_path))
        with self.meta_path.open("w") as f:
            json.dump({str(k): v for k, v in self.id_map.items()}, f)

    @staticmethod
    def _normalize(m: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms

    @staticmethod
    def _int_id(s: str) -> int:
        return abs(hash(s)) % (2**63 - 1)

    def add(self, chunk_ids: list[str], vectors: np.ndarray) -> None:
        with self.lock:
            unit = self._normalize(vectors.astype(np.float32))
            int_ids = np.asarray([self._int_id(cid) for cid in chunk_ids], dtype=np.int64)
            self.index.add_with_ids(unit, int_ids)
            for cid, iid in zip(chunk_ids, int_ids.tolist()):
                self.id_map[iid] = cid
            self._persist()

    def remove(self, chunk_ids: list[str]) -> int:
        with self.lock:
            int_ids = np.asarray([self._int_id(cid) for cid in chunk_ids], dtype=np.int64)
            removed = self.index.remove_ids(int_ids)
            for iid in int_ids.tolist():
                self.id_map.pop(iid, None)
            self._persist()
            return int(removed)

    def search(self, vec: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        with self.lock:
            if not self.id_map:
                return []
            unit = self._normalize(vec.reshape(1, -1).astype(np.float32))
            k = min(top_k, len(self.id_map))
            D, I = self.index.search(unit, k)
            out: list[tuple[str, float]] = []
            for sim, iid in zip(D[0].tolist(), I[0].tolist()):
                if iid == -1:
                    continue
                cid = self.id_map.get(iid)
                if cid is None:
                    continue
                out.append((cid, float(sim)))
            return out


class RagStore:
    """Async-friendly facade over per-user FAISS indexes for RAG chunks."""

    def __init__(self) -> None:
        s = get_settings()
        self.dim = s.EMBEDDING_DIM
        self._users: dict[str, _UserIndex] = {}
        self._users_lock = threading.Lock()

    def _user_index(self, user_id: str) -> _UserIndex:
        with self._users_lock:
            idx = self._users.get(user_id)
            if idx is None:
                idx = _UserIndex(user_id, self.dim)
                self._users[user_id] = idx
            return idx

    async def add_chunks(self, user_id: str, chunks: list[tuple[str, str]]) -> None:
        """`chunks` is a list of (chunk_id, text). Embeds in one batch then
        adds to the index."""
        if not chunks:
            return
        ids = [c[0] for c in chunks]
        texts = [c[1] for c in chunks]
        vecs = await embed_many(texts)
        if vecs.size == 0:
            return
        idx = self._user_index(user_id)
        await asyncio.to_thread(idx.add, ids, vecs)

    async def search(
        self, user_id: str, query: str, top_k: int = 8
    ) -> list[tuple[str, float]]:
        """Returns [(chunk_id, similarity)] sorted by similarity desc."""
        if not query.strip():
            return []
        from app.services.llm import embed

        vec = await embed(query)
        idx = self._user_index(user_id)
        return await asyncio.to_thread(idx.search, vec, top_k)

    async def remove(self, user_id: str, chunk_ids: list[str]) -> int:
        if not chunk_ids:
            return 0
        idx = self._user_index(user_id)
        return await asyncio.to_thread(idx.remove, chunk_ids)


_singleton: Optional[RagStore] = None


def get_rag_store() -> RagStore:
    global _singleton
    if _singleton is None:
        _singleton = RagStore()
    return _singleton
