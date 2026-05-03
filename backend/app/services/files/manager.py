"""File lifecycle: receive bytes → parse → summarize → persist (disk +
Postgres) → register as graph node."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.models.schemas import Triple
from app.services.files.parsers import parse, kind_for
from app.services.files.summarizer import summarize_file
from app.services.graph.neo4j_client import get_graph_client
from app.services.rag.indexer import deindex_file, index_file
from app.services.storage import postgres as pg


def _files_root() -> Path:
    root = get_settings().DATA_DIR / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def store_upload(
    user_id: str,
    thread_id: Optional[str],
    filename: str,
    mime: str,
    raw: bytes,
) -> dict:
    """Run the full upload pipeline. Returns the file row dict."""
    file_id = uuid.uuid4().hex
    user_dir = _files_root() / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    storage_path = user_dir / f"{file_id}__{Path(filename).name}"
    storage_path.write_bytes(raw)

    kind, content_text, parser_meta = parse(raw, filename)
    summary = await summarize_file(filename, kind, content_text)

    metadata = {**parser_meta, "uploaded_thread_id": thread_id}

    await pg.insert_file(
        file_id=file_id,
        user_id=user_id,
        thread_id=thread_id,
        filename=filename,
        mime=mime or "application/octet-stream",
        size_bytes=len(raw),
        kind=kind,
        storage_path=str(storage_path),
        content_text=content_text,
        summary=summary,
        metadata=metadata,
    )

    # Index the file's content into the per-user RAG store. Best-effort: a
    # failure here doesn't break the upload — the row is still in Postgres
    # and can be backfilled later.
    try:
        await index_file(user_id, file_id, filename, kind, content_text or "")
    except Exception as e:
        print(f"[files] rag index_file failed for {filename}: {e}")

    # Register the file in the user's graph so future turns can recall it.
    # The file's display name (filename) becomes the node id; the file_id
    # lives in the graph node's metadata so tools can resolve it later.
    graph = get_graph_client()
    try:
        await graph.upsert_triple(
            user_id,
            Triple(
                subject="User",
                relation="UPLOADED",
                object=filename,
                subject_type="user",
                object_type="other",
                confidence=1.0,
            ),
            thread_id=thread_id,
        )
        await graph.upsert_triple(
            user_id,
            Triple(
                subject=filename,
                relation="IS_A",
                object=kind,
                subject_type="other",
                object_type="topic",
                confidence=1.0,
            ),
            thread_id=thread_id,
        )
        await graph.upsert_triple(
            user_id,
            Triple(
                subject=filename,
                relation="HAS_SUMMARY",
                object=summary[:200],
                subject_type="other",
                object_type="other",
                confidence=1.0,
            ),
            thread_id=thread_id,
        )
    except Exception as e:
        # Don't fail the upload if the graph is briefly unavailable.
        print(f"[files] graph register failed: {e}")

    return {
        "id": file_id,
        "user_id": user_id,
        "thread_id": thread_id,
        "filename": filename,
        "kind": kind,
        "size_bytes": len(raw),
        "summary": summary,
        "metadata": metadata,
    }


async def delete_upload(user_id: str, file_id: str) -> dict | None:
    """Remove a file from disk, Postgres, AND its graph nodes.

    Returns counts so the API can surface "deleted N graph edges + M nodes",
    or None if the file didn't exist.
    """
    # Look up the row first so we know the filename for the graph cleanup.
    f = await pg.get_file(user_id, file_id)
    if not f:
        return None
    filename = f["filename"]
    storage_path = f["storage_path"]

    # RAG cleanup BEFORE the row is gone (so we still know the file_id).
    try:
        await deindex_file(user_id, file_id)
    except Exception as e:
        print(f"[files] rag deindex_file failed for {filename}: {e}")

    # Postgres + disk cleanup.
    await pg.delete_file_row(user_id, file_id)
    try:
        os.unlink(storage_path)
    except FileNotFoundError:
        pass

    # Graph cascade — remove the file's node and any edges touching it,
    # plus orphan nodes (e.g., the kind-topic if no other file references
    # it). Don't fail the delete if the graph is briefly unavailable.
    cleanup = {"edges": 0, "nodes": 0}
    try:
        cleanup = await get_graph_client().delete_node_by_name(user_id, filename)
    except Exception as e:
        print(f"[files] graph cleanup failed for {filename}: {e}")

    return {
        "file_id": file_id,
        "filename": filename,
        "graph_edges_removed": cleanup.get("edges", 0),
        "graph_nodes_removed": cleanup.get("nodes", 0),
    }
