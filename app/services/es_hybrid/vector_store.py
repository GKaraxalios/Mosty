"""Vector leg: ένα pickle shard ανά έγγραφο + NumPy cosine (inner product σε
L2-normalized vectors). Add/update/delete εγγράφου = 1 αρχείο, όχι rebuild όλου."""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import config

log = logging.getLogger(__name__)
_lock = threading.Lock()
_cache: Optional[Tuple[Tuple, np.ndarray, List[Dict[str, Any]]]] = None


def _dir() -> str:
    d = os.path.join(config.CACHE_DIR, "vector_shards")
    os.makedirs(d, exist_ok=True)
    return d


def doc_key(chunk: Dict[str, Any]) -> str:
    sha = str(chunk.get("file_sha256") or "").strip()
    if sha:
        return sha
    rel = str(chunk.get("relative_path") or chunk.get("file_name") or "")
    return "rp_" + hashlib.sha256(rel.encode()).hexdigest()[:40]


def _path(key: str) -> str:
    return os.path.join(_dir(), f"{key}.pkl")


def write_shard(key: str, chunks: List[Dict[str, Any]], embeddings: List[Any]) -> int:
    if not chunks or not len(embeddings):
        delete_shard(key)
        return 0
    mat = np.asarray([np.asarray(e, dtype="float32").flatten() for e in embeddings], dtype="float32")
    payload = {"doc_key": key, "chunks": list(chunks), "embeddings": mat,
               "updated_at": datetime.now(tz=timezone.utc).isoformat()}
    fd, tmp = tempfile.mkstemp(dir=_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _path(key))  # ατομικό
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return len(chunks)


def delete_shard(key: str) -> bool:
    try:
        if os.path.isfile(_path(key)):
            os.remove(_path(key))
            return True
    except OSError as exc:
        log.warning("shard delete failed %s: %s", key, exc)
    return False


def _files() -> List[str]:
    return sorted(os.path.join(_dir(), f) for f in os.listdir(_dir()) if f.endswith(".pkl"))


def load_all() -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """Συγχωνεύει όλα τα shards. Cache με (path, mtime, size) signature."""
    global _cache
    files = _files()
    sig = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in files)
    with _lock:
        if _cache and _cache[0] == sig:
            return _cache[1], _cache[2]
        chunks: List[Dict[str, Any]] = []
        rows: List[np.ndarray] = []
        for p in files:
            try:
                with open(p, "rb") as f:
                    pl = pickle.load(f)
                arr = np.asarray(pl["embeddings"], dtype="float32")
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)
                if arr.shape[0] != len(pl["chunks"]):
                    log.warning("shard %s misaligned, skipping", p)
                    continue
                chunks.extend(pl["chunks"])
                rows.extend(arr)
            except Exception as exc:
                log.warning("failed loading %s: %s", p, exc)
        mat = np.asarray(rows, dtype="float32") if rows else np.empty((0, 0), dtype="float32")
        _cache = (sig, mat, chunks)
        return mat, chunks


def search_vector(q_emb: np.ndarray, k: int = 80,
                  filters: Optional[Dict[str, Any]] = None) -> List[Tuple[str, float, int]]:
    """Επιστρέφει [(chunk_id, score, row_idx)] best-first. q_emb: (1, d) normalized."""
    mat, chunks = load_all()
    n = mat.shape[0]
    if n == 0:
        return []
    rows = list(range(n))
    if filters:
        # (Mosty) τα σύνολα τιμών χτίζονται 1 φορά ανά κλήση, όχι ανά chunk: ίδια σημασιολογία,
        # αλλά O(n+m) αντί O(n*m) όταν το φίλτρο έχει χιλιάδες chunk_ids.
        allowed = {
            key: ({str(v) for v in exp} if isinstance(exp, (list, tuple, set)) else {str(exp)})
            for key, exp in filters.items() if exp not in (None, "", [])
        }

        def ok(c):
            return all(str(c.get(key)) in vals for key, vals in allowed.items())
        rows = [i for i in rows if ok(chunks[i])]
        if not rows:
            return []
    sub = mat[rows]
    sims = (sub @ q_emb.T).ravel()
    kk = min(max(k, 1), len(rows))
    part = np.argpartition(-sims, kk - 1)[:kk] if kk < len(rows) else np.arange(len(rows))
    order = part[np.argsort(-sims[part])]
    return [(str(chunks[rows[i]].get("chunk_id") or f"legacy_{rows[i]}"), float(sims[i]), rows[i])
            for i in order]
