"""One-shot LLM summary for an uploaded file.

Run on upload, stored alongside the file row, and surfaced both as a graph
node attribute and as the FILE_SUMMARY tool's default short answer.
"""
from __future__ import annotations

from app.services.llm import chat


SYSTEM = """You write concise, factual summaries of uploaded files for a
chatbot's memory. Output a single short paragraph (<= 80 words). Cover:
- what the file is (table, report, contract, code, etc.)
- the key columns / sections / topics it contains
- any obvious purpose ("monthly sales by region", "Q3 review draft")

Do not invent details. If the content is unclear, say so plainly. No preface."""

USER_TMPL = """Filename: {filename}
Kind: {kind}

Content (truncated):
---
{content}
---

Summary:"""


async def summarize_file(filename: str, kind: str, content_text: str) -> str:
    if not content_text or not content_text.strip():
        return f"{filename} — empty or unreadable file."
    # Keep the prompt small enough to avoid blowing context — head of the
    # rendered content is enough for a high-quality summary.
    snippet = content_text[:8000]
    try:
        out = await chat(SYSTEM, USER_TMPL.format(filename=filename, kind=kind, content=snippet),
                         temperature=0.2)
    except Exception as e:
        return f"{filename} — summary unavailable: {e}"
    return (out or "").strip() or f"{filename} — summary unavailable."
