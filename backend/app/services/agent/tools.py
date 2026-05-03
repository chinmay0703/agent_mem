"""Tools the agent can call during a turn.

Tool calling flow:
    user message
      → agent sends [system, context, user] + tool schemas to the LLM
      → if LLM returns a tool call, we execute it server-side
      → tool result is appended as a `tool` message
      → loop until LLM returns a plain message or MAX_ITERATIONS hit

Tools are deliberately small and side-effect-light:
  • list_files         — list user's uploaded files (just IDs/names/summaries)
  • read_file          — return the parsed text of a file (truncated)
  • query_dataframe    — run a pandas expression against a CSV/XLSX file
  • python_sandbox     — run arbitrary Python; files mounted by id

Each tool returns a JSON-serializable string shorter than ~3 KB to keep the
context bounded.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.services.files.sandbox import run_python
from app.services.rag.index import get_rag_store
from app.services.storage import postgres as pg


# ── Tool schemas (OpenAI function-calling format) ────────────────────────


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Semantic search across the user's uploaded files AND past "
                "chat messages (across all threads). Returns the top-N "
                "matching chunks with full metadata. PREFER this over "
                "read_file when answering anything that could come from a "
                "file or earlier conversation — it's faster, more focused, "
                "and the chunks are the citation sources you must reference "
                "with [src:CHUNK_ID] markers in your final answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query"},
                    "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                    "kind": {
                        "type": "string",
                        "enum": ["any", "file", "message"],
                        "default": "any",
                        "description": "Restrict to file chunks, message chunks, or both."
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List the files this user has uploaded. Use whenever the user "
                "asks about a file, asks you to compute something, or refers "
                "to data they've shared."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Return the parsed plain-text rendering of an uploaded file "
                "(truncated to ~12 KB). For tabular files this includes the "
                "shape, columns, head(20), and describe()."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "id from list_files"},
                },
                "required": ["file_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_dataframe",
            "description": (
                "Run a single pandas expression against a CSV/XLSX file. "
                "The dataframe is bound to `df`. Return the result of the "
                "last expression. Use this for sums, means, group-bys, "
                "filters. Examples: 'df[\"sales\"].sum()', "
                "'df.groupby(\"region\")[\"sales\"].mean()'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string"},
                    "expression": {
                        "type": "string",
                        "description": "A single pandas expression operating on `df`.",
                    },
                    "sheet": {
                        "type": "string",
                        "description": "Sheet name (XLSX only). Optional; defaults to first sheet.",
                    },
                },
                "required": ["file_id", "expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_sandbox",
            "description": (
                "Execute a Python script in a sandboxed subprocess. pandas, "
                "numpy, json, csv, datetime are pre-imported. Files referenced "
                "via `file_ids` are copied into the cwd under their original "
                "filename so you can do `pd.read_csv('sales.csv')`. Capture "
                "results with print(). 8s wall-time / 512 MB memory cap."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to run"},
                    "file_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of file ids to mount.",
                    },
                },
                "required": ["code"],
            },
        },
    },
]


# ── Tool implementations ─────────────────────────────────────────────────


def _truncate(s: str, limit: int = 3000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n[... truncated {len(s) - limit} chars ...]"


async def _search_knowledge(user_id: str, args: dict) -> str:
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 8)
    top_k = max(1, min(20, top_k))
    kind = (args.get("kind") or "any").lower()
    if not query:
        return json.dumps({"error": "query is required"})

    # Over-fetch a bit so we can filter by kind without losing top-K.
    raw = await get_rag_store().search(user_id, query, top_k=top_k * 3)
    if not raw:
        return json.dumps({"results": []})

    rows = await pg.get_chunks(user_id, [cid for cid, _ in raw])
    by_id = {r["id"]: r for r in rows}
    out: list[dict] = []
    for cid, sim in raw:
        r = by_id.get(cid)
        if not r:
            continue
        if kind != "any" and r["kind"] != kind:
            continue
        out.append({
            # The src ref the model must cite with [src:<id>].
            "src_id": cid,
            "kind": r["kind"],
            "similarity": round(sim, 3),
            "snippet": r["text"][:600],
            **(
                {
                    "file_id": r.get("file_id"),
                    "filename": r.get("filename"),
                    "page": r.get("page"),
                    "chunk_idx": r.get("chunk_idx"),
                }
                if r["kind"] == "file"
                else {
                    "thread_id": r.get("thread_id"),
                    "message_id": r.get("message_id"),
                    "role": r.get("role"),
                    "created_at": r.get("created_at").isoformat()
                    if r.get("created_at")
                    else None,
                }
            ),
        })
        if len(out) >= top_k:
            break
    return _truncate(json.dumps({"results": out}, default=str), limit=12000)


async def _list_files(user_id: str, _: dict) -> str:
    rows = await pg.list_files(user_id, limit=50)
    if not rows:
        return json.dumps({"files": []})
    out = [
        {
            "id": r["id"],
            "filename": r["filename"],
            "kind": r["kind"],
            "size_bytes": r["size_bytes"],
            "summary": r.get("summary"),
            "metadata": r.get("metadata"),
        }
        for r in rows
    ]
    return _truncate(json.dumps({"files": out}, default=str))


async def _read_file(user_id: str, args: dict) -> str:
    file_id = args.get("file_id", "")
    f = await pg.get_file(user_id, file_id)
    if not f:
        return json.dumps({"error": "file not found"})
    # 30 KB cap — large enough for a typical 5–10 page PDF or a multi-sheet
    # XLSX preview, small enough that the LLM can still reason on top of it
    # alongside the system + thread summary within the prompt budget.
    return _truncate(json.dumps({
        "filename": f["filename"],
        "kind": f["kind"],
        "metadata": f.get("metadata"),
        "content": f.get("content_text") or "",
    }, default=str), limit=30000)


async def _query_dataframe(user_id: str, args: dict) -> str:
    file_id = args.get("file_id", "")
    expr = args.get("expression", "")
    sheet = args.get("sheet")
    if not file_id or not expr:
        return json.dumps({"error": "file_id and expression are required"})
    f = await pg.get_file(user_id, file_id)
    if not f:
        return json.dumps({"error": "file not found"})
    if f["kind"] not in ("csv", "xlsx"):
        return json.dumps({"error": f"query_dataframe only supports csv/xlsx, got {f['kind']}"})

    # Build a tiny script that loads the file and prints the result of the expression.
    safe_filename = os.path.basename(f["filename"])
    if f["kind"] == "csv":
        loader = f"df = pd.read_csv({safe_filename!r})"
    else:
        if sheet:
            loader = f"df = pd.read_excel({safe_filename!r}, sheet_name={sheet!r})"
        else:
            loader = f"df = pd.read_excel({safe_filename!r})"
    code = (
        f"{loader}\n"
        f"_result = ({expr})\n"
        "try:\n"
        "    import pandas as _pd\n"
        "    if isinstance(_result, (_pd.Series, _pd.DataFrame)):\n"
        "        print(_result.to_string())\n"
        "    else:\n"
        "        print(_result)\n"
        "except Exception as _e:\n"
        "    print(f'<<print error: {_e}>>')\n"
    )
    res = await run_python(code, file_paths={safe_filename: f["storage_path"]})

    # Helpful: when stderr mentions a KeyError on a column, surface the
    # actual column list so the LLM can self-correct on the next iteration.
    hint = None
    if res.stderr and "KeyError" in res.stderr:
        meta = f.get("metadata") or {}
        cols = (meta.get("columns") if isinstance(meta, dict) else None) or []
        if cols:
            hint = f"available columns: {cols}"

    payload = {
        "stdout": res.stdout,
        "stderr": res.stderr,
        "returncode": res.returncode,
        "timed_out": res.timed_out,
    }
    if hint:
        payload["hint"] = hint
    return _truncate(json.dumps(payload))


async def _python_sandbox(user_id: str, args: dict) -> str:
    code = args.get("code", "")
    file_ids = args.get("file_ids") or []
    if not code.strip():
        return json.dumps({"error": "code is required"})

    file_paths: dict[str, str] = {}
    for fid in file_ids:
        f = await pg.get_file(user_id, fid)
        if not f:
            return json.dumps({"error": f"file {fid} not found"})
        safe = os.path.basename(f["filename"])
        file_paths[safe] = f["storage_path"]

    res = await run_python(code, file_paths=file_paths)
    return _truncate(json.dumps({
        "stdout": res.stdout,
        "stderr": res.stderr,
        "returncode": res.returncode,
        "timed_out": res.timed_out,
    }))


TOOL_DISPATCH = {
    "search_knowledge": _search_knowledge,
    "list_files": _list_files,
    "read_file": _read_file,
    "query_dataframe": _query_dataframe,
    "python_sandbox": _python_sandbox,
}


async def execute_tool(name: str, user_id: str, args: dict[str, Any]) -> str:
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        return await fn(user_id, args or {})
    except Exception as e:
        return json.dumps({"error": f"tool {name} crashed: {e}"})
