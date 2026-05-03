"""Token-aware chunker for RAG.

Strategy:
  1. Split on blank lines (paragraph boundaries).
  2. Greedily pack paragraphs into ~target_tokens per chunk.
  3. If a single paragraph is too big, fall back to sentence splitting,
     then to character windows as a last resort.
  4. Emit ~overlap tokens of the previous chunk's tail into the next chunk
     so context isn't lost at boundaries.

Token counting routes through the tokenizer that matches the configured
chat model — gpt-5.x → o200k_base, gpt-4 → cl100k_base — so chunks have
the right token budget for the model that ultimately consumes them via
search_knowledge / read_file. Falls back to a char/4 heuristic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.services.agent.compaction import _enc as _shared_enc


def _enc():
    return _shared_enc()


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _enc()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    text: str
    page: int | None = None  # for PDFs, the page this chunk starts on
    chunk_idx: int = 0


_PARA = re.compile(r"\n\s*\n")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _hard_window(text: str, target: int) -> list[str]:
    """Last-resort splitter for very long single sentences/lines: slice by
    approximate token count using tokens or chars."""
    enc = _enc()
    if enc is not None:
        try:
            ids = enc.encode(text)
            return [enc.decode(ids[i : i + target]) for i in range(0, len(ids), target)]
        except Exception:
            pass
    char_per_chunk = target * 4
    return [text[i : i + char_per_chunk] for i in range(0, len(text), char_per_chunk)]


def chunk_text(
    text: str,
    target_tokens: int = 400,
    overlap_tokens: int = 50,
    max_tokens: int = 600,
) -> list[Chunk]:
    """Split `text` into Chunks. Page is inferred for PDF-style content
    that contains lines starting with `--- page N ---` (our pdf parser's
    page marker)."""
    if not text or not text.strip():
        return []

    # Page tracking — our pdf parser inserts "--- page N ---" markers.
    page_re = re.compile(r"---\s*page\s+(\d+)\s*---", re.IGNORECASE)

    paragraphs: list[tuple[int | None, str]] = []
    current_page: int | None = None
    for raw_para in _PARA.split(text):
        para = raw_para.strip()
        if not para:
            continue
        m = page_re.search(para)
        if m:
            current_page = int(m.group(1))
            cleaned = page_re.sub("", para).strip()
            if cleaned:
                paragraphs.append((current_page, cleaned))
        else:
            paragraphs.append((current_page, para))

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tok = 0
    buf_page: int | None = None

    def flush() -> None:
        nonlocal buf, buf_tok, buf_page
        if not buf:
            return
        body = "\n\n".join(buf).strip()
        if body:
            chunks.append(Chunk(text=body, page=buf_page, chunk_idx=len(chunks)))
        buf = []
        buf_tok = 0
        buf_page = None

    for page, para in paragraphs:
        ptok = count_tokens(para)
        if ptok > max_tokens:
            # Too big — flush buffer, then split the paragraph.
            flush()
            for piece in _hard_window(para, target_tokens):
                if not piece.strip():
                    continue
                chunks.append(
                    Chunk(text=piece.strip(), page=page, chunk_idx=len(chunks))
                )
            continue
        if buf_tok + ptok > target_tokens and buf:
            flush()
        if not buf:
            buf_page = page
        buf.append(para)
        buf_tok += ptok
    flush()

    # Apply overlap: prepend tail of previous chunk to each subsequent one.
    if overlap_tokens > 0 and len(chunks) > 1:
        for i in range(1, len(chunks)):
            prev = chunks[i - 1].text
            tail = _take_last_tokens(prev, overlap_tokens)
            if tail and not chunks[i].text.startswith(tail):
                chunks[i] = Chunk(
                    text=tail + "\n\n" + chunks[i].text,
                    page=chunks[i].page,
                    chunk_idx=i,
                )

    return chunks


def _take_last_tokens(text: str, n: int) -> str:
    enc = _enc()
    if enc is not None:
        try:
            ids = enc.encode(text)
            if len(ids) <= n:
                return text
            return enc.decode(ids[-n:])
        except Exception:
            pass
    return text[-n * 4 :]
