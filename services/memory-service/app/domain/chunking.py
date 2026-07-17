"""Text chunking (MEM-FR-032). Fixed/semantic windows with token overlap and a
per-chunk byte cap; per-source chunk cap (BR-17)."""

from __future__ import annotations

import re

_WORD = re.compile(r"\S+")


def chunk_text(
    text: str, *, max_tokens: int, overlap: int, max_bytes: int, cap: int
) -> tuple[list[str], bool]:
    """Return (chunks, capped). Splits on whitespace tokens into windows of
    ``max_tokens`` with ``overlap`` token overlap; truncates each chunk to
    ``max_bytes`` and the whole source to ``cap`` chunks (BR-17)."""
    words = _WORD.findall(text or "")
    if not words:
        return [], False
    chunks: list[str] = []
    step = max(1, max_tokens - overlap)
    i = 0
    capped = False
    while i < len(words):
        window = words[i : i + max_tokens]
        chunk = " ".join(window).encode()[:max_bytes].decode(errors="ignore")
        chunks.append(chunk)
        if len(chunks) >= cap:
            capped = i + max_tokens < len(words)
            break
        i += step
    return chunks, capped
