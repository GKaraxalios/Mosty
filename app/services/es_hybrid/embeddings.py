"""Δημιουργία embeddings (Vertex/google-genai): batches των 32, retry+backoff,
fallback μοντέλο σε 404, L2-normalize, query embedding με hard timeout."""
from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np

from . import config

log = logging.getLogger(__name__)

_TRANSIENT = ("429", "503", "rate limit", "resource exhausted", "deadline exceeded",
              "unavailable", "timeout", "temporarily", "connection", "internal",
              "server disconnected", "broken pipe", "reset")

_client = None
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="embed-query")


def l2_normalize(x: np.ndarray, axis: int = 1) -> np.ndarray:
    n = np.linalg.norm(x, axis=axis, keepdims=True)
    n = np.where(n == 0, 1.0, n)
    return (x / n).astype("float32", copy=False)


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        kw: Dict[str, Any] = dict(vertexai=True, project=config.GCP_PROJECT, location=config.GCP_LOCATION)
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            from google.oauth2 import service_account
            kw["credentials"] = service_account.Credentials.from_service_account_file(
                config.GOOGLE_APPLICATION_CREDENTIALS,
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
        kw["http_options"] = types.HttpOptions(timeout=int(config.EMBEDDING_REQUEST_TIMEOUT_S * 1000))
        _client = genai.Client(**kw)
    return _client


def truncate_tokens(text: str, max_tokens: int) -> str:
    text = str(text or "").strip()
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        ids = enc.encode(text)
        return text if len(ids) <= max_tokens else enc.decode(ids[:max_tokens]).strip()
    except Exception:
        return text[: max_tokens * 4].strip()


def prepare_input(chunk: Dict[str, Any], max_tokens: Optional[int] = None) -> str:
    """Embed το embedding_text (αλλιώς text), κομμένο σε token budget."""
    text = str(chunk.get("embedding_text") or chunk.get("text") or "").strip()
    return truncate_tokens(text, max_tokens or config.EMBEDDING_MAX_TOKENS)


def _embed_with_retry(model: str, contents: List[str], attempts: int = 3):
    for i in range(1, attempts + 1):
        try:
            return _get_client().models.embed_content(model=model, contents=contents)
        except Exception as exc:
            if i < attempts and any(f in str(exc).lower() for f in _TRANSIENT):
                time.sleep(min(8.0, 0.75 * 2 ** (i - 1)))
                continue
            raise


def _is_not_found(exc: Exception) -> bool:
    t = str(exc).upper()
    return "NOT_FOUND" in t or "404" in t or "WAS NOT FOUND" in t


def embed_chunks(chunks: List[Dict[str, Any]]):
    """Επιστρέφει (rows, valid_chunks) 1:1, L2-normalized float32.
    Chunks που αποτυγχάνουν παραλείπονται (θα μείνουν keyword-only)."""
    if config.EMBEDDING_PROVIDER == "none" or not chunks:
        return [], []
    model, fallback = config.EMBEDDING_MODEL, config.EMBEDDING_FALLBACK_MODEL
    rows: List[np.ndarray] = []
    valid: List[Dict[str, Any]] = []
    bs = config.EMBEDDING_BATCH_SIZE
    for start in range(0, len(chunks), bs):
        batch = chunks[start:start + bs]
        contents = [prepare_input(c) for c in batch]
        try:
            resp = _embed_with_retry(model, contents)
        except Exception as exc:
            if _is_not_found(exc) and fallback and model != fallback:
                log.warning("model %r not found, falling back to %r", model, fallback)
                model = fallback
                try:
                    resp = _embed_with_retry(model, contents)
                except Exception as exc2:
                    log.warning("batch %d failed: %s", start, exc2)
                    continue
            else:
                log.warning("batch %d failed: %s", start, exc)
                continue
        for chunk, emb in zip(batch, list(getattr(resp, "embeddings", None) or [])):
            vals = getattr(emb, "values", None)
            if vals is None:
                continue
            rows.append(l2_normalize(np.asarray(vals, dtype="float32").reshape(1, -1)).flatten())
            valid.append(chunk)
    return rows, valid


def embed_query(text: str) -> np.ndarray:
    """(1, d) L2-normalized. Hard timeout: αν κολλήσει ο provider, ρίχνει TimeoutError
    και το hybrid πέφτει σε keyword-only."""
    fut = _pool.submit(_embed_with_retry, config.EMBEDDING_MODEL, [text])
    try:
        r = fut.result(timeout=config.EMBEDDING_REQUEST_TIMEOUT_S)
    except concurrent.futures.TimeoutError as e:
        fut.cancel()
        raise TimeoutError(f"query embedding timed out after {config.EMBEDDING_REQUEST_TIMEOUT_S}s") from e
    q = np.array([e.values for e in r.embeddings], dtype="float32")
    return l2_normalize(q, axis=1)
