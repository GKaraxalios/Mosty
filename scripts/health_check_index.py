#!/usr/bin/env python3
"""
Health-check for incremental index sync status.

Prints sync state (last run, last success, indexed SKU count) and optional
validation (chunk store vs sync state). Safe to run from cron or monitoring.

Usage:
    python -m scripts.health_check_index
    python -m scripts.health_check_index --json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.chdir(_project_root)


def main() -> int:
    from app.services.sync_state_service import load_sync_state
    from app.services.rag_ingestion_service import get_chunk_store

    state = load_sync_state()
    store = get_chunk_store()
    indexed_skus = list({c.sku for c in store.values()})
    chunk_count = len(store)

    out = {
        "status": "ok",
        "last_run_at": state.get("last_run_at"),
        "last_successful_sync_at": state.get("last_successful_sync_at"),
        "last_sync_at": state.get("last_sync_at"),
        "run_count": state.get("run_count", 0),
        "last_error": state.get("last_error"),
        "indexed_sku_count": len(indexed_skus),
        "chunk_count": chunk_count,
    }
    if state.get("last_error"):
        out["status"] = "degraded"

    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        print("Index sync health:")
        print("  Last run:           ", out["last_run_at"] or "never")
        print("  Last success:      ", out["last_successful_sync_at"] or "never")
        print("  Run count:         ", out["run_count"])
        print("  Indexed products:  ", out["indexed_sku_count"])
        print("  Total chunks:      ", out["chunk_count"])
        if out["last_error"]:
            print("  Last error:         ", out["last_error"])
        print("  Status:            ", out["status"])

    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
