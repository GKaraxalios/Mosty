#!/usr/bin/env python3
"""
Incremental AI product index update for Mosty.

Run from cron to sync new/updated products from Magento into the vector index
without full reindex. Uses a lock to prevent overlapping runs.

Usage:
    python -m scripts.update_index [--reconcile-deletes] [--dry-run]

Cron example:
    */10 * * * * /usr/bin/python3 /path/to/MostyAPI/scripts/update_index.py >> /var/log/mosty_index_update.log 2>&1

Environment: load from .env (MAGENTO_GRAPHQL_URL, GOOGLE_*, SYNC_STATE_PATH, etc.)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Load .env before any app imports that read config
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.chdir(_project_root)


def _acquire_lock(lock_path: Path, timeout_seconds: int):
    """Acquire exclusive lock file. Returns (True, fd) if acquired, (False, None) otherwise.
    Caller must pass fd to _release_lock to release.
    """
    try:
        import fcntl
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return True, fd
    except (ImportError, OSError):
        pass
    # Fallback: pid file
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            os.kill(pid, 0)
        except (ValueError, OSError):
            pass
        else:
            return False, None
    lock_path.write_text(str(os.getpid()))
    return True, None


def _release_lock(lock_path: Path, fd: int | None) -> None:
    if fd is not None:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except (ImportError, OSError, ValueError):
            pass
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Incremental product index update")
    parser.add_argument(
        "--reconcile-deletes",
        action="store_true",
        help="Remove index entries for products no longer in Magento (slower, use occasionally)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only load state and log what would run; do not call Magento or update index",
    )
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.core.logging import setup_logging, get_logger

    setup_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    lock_path = Path(settings.INDEX_LOCK_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = getattr(settings, "INDEX_LOCK_TIMEOUT_SECONDS", 3600)

    acquired, lock_fd = _acquire_lock(lock_path, timeout)
    if not acquired:
        logger.warning("Another index update is already running; exiting (idempotent skip)")
        return 0

    try:
        if args.dry_run:
            from app.services.sync_state_service import load_sync_state, get_since_timestamp
            state = load_sync_state()
            since = get_since_timestamp(state)
            logger.info("DRY RUN: would sync products updated since %s", since)
            return 0

        import asyncio
        from app.services.incremental_index_service import IncrementalIndexService

        async def run() -> dict:
            svc = IncrementalIndexService()
            return await svc.run_incremental_sync(reconcile_deletes=args.reconcile_deletes)

        stats = asyncio.run(run())
        logger.info(
            "Index update finished: processed=%d updated=%d removed=%d failed=%d",
            stats.get("products_processed", 0),
            stats.get("updated_count", 0),
            stats.get("removed_count", 0),
            stats.get("failed_count", 0),
        )
        return 0 if stats.get("failed_count", 0) == 0 else 1
    except Exception as e:
        logger.exception("Index update failed: %s", e)
        from app.services.sync_state_service import load_sync_state, save_sync_state
        state = load_sync_state()
        save_sync_state(state, last_error=str(e))
        return 1
    finally:
        _release_lock(lock_path, lock_fd)


if __name__ == "__main__":
    sys.exit(main())
