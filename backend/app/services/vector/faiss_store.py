"""Per-user FAISS index for semantic memory.

Each user gets an isolated FlatIP index (cosine via normalized vectors) plus a
JSON sidecar with metadata: id, summary, created_at, last_accessed, importance.
Indexes are loaded lazily and persisted to disk on every write so a restart
keeps memory.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import faiss
import numpy as np

from app.config import get_settings


@dataclass
class MemoryRecord:
    id: str
    summary: str
    created_at: str
    last_accessed: Optional[str] = None
    importance: float = 0.5
    embedding_norm: float = 1.0
    metadata: dict = field(default_factory=dict)


class _UserIndex:
    """Owns one FAISS index + sidecar metadata for a single user."""

    def __init__(self, user_id: str, dim: int, root: Path) -> None:
        self.user_id = user_id
        self.dim = dim
        self.dir = root / user_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.faiss"
        self.meta_path = self.dir / "meta.json"
        self.lock = threading.Lock()
        self.records: list[MemoryRecord] = []
        self.index = self._load_or_create()

    def _load_or_create(self) -> faiss.IndexIDMap:
        if self.index_path.exists() and self.meta_path.exists():
            base = faiss.read_index(str(self.index_path))
            with self.meta_path.open() as f:
                self.records = [MemoryRecord(**r) for r in json.load(f)]
            return base
        base = faiss.IndexIDMap(faiss.IndexFlatIP(self.dim))
        return base

    def _persist(self) -> None:
        faiss.write_index(self.index, str(self.index_path))
        with self.meta_path.open("w") as f:
            json.dump([asdict(r) for r in self.records], f)

    @staticmethod
    def _normalize(vec: np.ndarray) -> tuple[np.ndarray, float]:
        norm = float(np.linalg.norm(vec))
        if norm == 0:
            return vec, 0.0
        return vec / norm, norm

    def add(self, vec: np.ndarray, summary: str, importance: float = 0.5) -> str:
        with self.lock:
            unit, norm = self._normalize(vec.astype(np.float32))
            mem_id = uuid.uuid4().hex
            int_id = abs(hash(mem_id)) % (2**63 - 1)
            self.index.add_with_ids(unit.reshape(1, -1), np.asarray([int_id], dtype=np.int64))
            self.records.append(
                MemoryRecord(
                    id=mem_id,
                    summary=summary,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    importance=float(importance),
                    embedding_norm=norm,
                    metadata={"int_id": int_id},
                )
            )
            self._persist()
            return mem_id

    def search(self, vec: np.ndarray, top_k: int = 5) -> list[tuple[MemoryRecord, float]]:
        with self.lock:
            if not self.records:
                return []
            unit, _ = self._normalize(vec.astype(np.float32))
            D, I = self.index.search(unit.reshape(1, -1), min(top_k, len(self.records)))
            id_to_rec = {r.metadata.get("int_id"): r for r in self.records}
            out: list[tuple[MemoryRecord, float]] = []
            for sim, int_id in zip(D[0].tolist(), I[0].tolist()):
                if int_id == -1:
                    continue
                rec = id_to_rec.get(int_id)
                if rec is None:
                    continue
                out.append((rec, float(sim)))
            return out

    def touch(self, mem_id: str) -> None:
        with self.lock:
            for r in self.records:
                if r.id == mem_id:
                    r.last_accessed = datetime.now(timezone.utc).isoformat()
            self._persist()

    def list_records(self) -> list[MemoryRecord]:
        with self.lock:
            return list(self.records)


class FaissStore:
    """Async-friendly facade over per-user FAISS indexes.

    FAISS itself is sync; we offload to a thread executor so the FastAPI
    event loop is never blocked.
    """

    def __init__(self) -> None:
        s = get_settings()
        self.root = s.DATA_DIR / "faiss"
        self.root.mkdir(parents=True, exist_ok=True)
        self.dim = s.EMBEDDING_DIM
        self.half_life_days = s.DECAY_HALF_LIFE_DAYS
        self._users: dict[str, _UserIndex] = {}
        self._users_lock = threading.Lock()

    def _user_index(self, user_id: str) -> _UserIndex:
        with self._users_lock:
            idx = self._users.get(user_id)
            if idx is None:
                idx = _UserIndex(user_id, self.dim, self.root)
                self._users[user_id] = idx
            return idx

    async def add(
        self, user_id: str, vec: np.ndarray, summary: str, importance: float = 0.5
    ) -> str:
        idx = self._user_index(user_id)
        return await asyncio.to_thread(idx.add, vec, summary, importance)

    async def search(
        self, user_id: str, vec: np.ndarray, top_k: int = 5
    ) -> list[dict]:
        idx = self._user_index(user_id)
        hits = await asyncio.to_thread(idx.search, vec, top_k)
        now = datetime.now(timezone.utc)
        scored: list[dict] = []
        for rec, sim in hits:
            decay = self._decay(rec.created_at, now)
            score = sim * (0.5 + 0.5 * rec.importance) * decay
            scored.append(
                {
                    "id": rec.id,
                    "summary": rec.summary,
                    "similarity": sim,
                    "score": score,
                    "importance": rec.importance,
                    "created_at": rec.created_at,
                    "last_accessed": rec.last_accessed,
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    async def list_records(self, user_id: str) -> list[MemoryRecord]:
        idx = self._user_index(user_id)
        return await asyncio.to_thread(idx.list_records)

    async def touch(self, user_id: str, mem_id: str) -> None:
        idx = self._user_index(user_id)
        await asyncio.to_thread(idx.touch, mem_id)

    def _decay(self, created_at_iso: str, now: datetime) -> float:
        try:
            created = datetime.fromisoformat(created_at_iso)
        except ValueError:
            return 1.0
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max((now - created).total_seconds() / 86400.0, 0.0)
        if self.half_life_days <= 0:
            return 1.0
        return math.pow(0.5, age_days / self.half_life_days)


_singleton: Optional[FaissStore] = None


def get_vector_store() -> FaissStore:
    global _singleton
    if _singleton is None:
        _singleton = FaissStore()
    return _singleton
