from __future__ import annotations

import hashlib
import re

from app.core.config import get_settings
from app.models.schemas import RAGChunk, RAGDocument


_SENTENCE_RE = re.compile(r"(?<=[.!;])\s+")


def _chunk_id(sku: str, index: int) -> str:
    raw = f"{sku}::{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _token_estimate(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _split_by_sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^[A-Z][A-Za-z\s]+:", line) and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _fixed_size_chunks(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    sentences = _SENTENCE_RE.split(text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sent in sentences:
        st = _token_estimate(sent)
        if current_tokens + st > max_tokens and current:
            chunks.append(" ".join(current))
            overlap_sents: list[str] = []
            ot = 0
            for s in reversed(current):
                ot += _token_estimate(s)
                if ot > overlap_tokens:
                    break
                overlap_sents.insert(0, s)
            current = overlap_sents
            current_tokens = sum(_token_estimate(s) for s in current)
        current.append(sent)
        current_tokens += st

    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_rag_document(
    doc: RAGDocument,
    full_text: str,
) -> list[RAGChunk]:
    settings = get_settings()
    max_tokens = settings.CHUNK_MAX_TOKENS
    overlap_tokens = settings.CHUNK_OVERLAP_TOKENS

    sections = _split_by_sections(full_text)

    raw_chunks: list[str] = []
    for section in sections:
        if _token_estimate(section) <= max_tokens:
            raw_chunks.append(section)
        else:
            raw_chunks.extend(
                _fixed_size_chunks(section, max_tokens, overlap_tokens)
            )

    if not raw_chunks:
        raw_chunks = [full_text[:2000]]

    result: list[RAGChunk] = []
    for idx, text in enumerate(raw_chunks):
        result.append(
            RAGChunk(
                chunk_id=_chunk_id(doc.sku, idx),
                sku=doc.sku,
                name=doc.name,
                url=doc.canonical_url,
                categories=doc.categories,
                price=doc.final_price,
                updated_at=doc.updated_at,
                source="magento_graphql",
                text=text.strip(),
                meta_keyword=doc.meta_keyword,
                meta_description=doc.meta_description,
                description_text=doc.description_text,
                stock_status=doc.stock_status,
                image_url=doc.image_url,
            )
        )
    return result
