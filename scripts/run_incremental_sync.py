#!/usr/bin/env python3
"""
Trigger an incremental RAG sync via the MostyAPI endpoint.

Usage:
    python -m scripts.run_incremental_sync
    python -m scripts.run_incremental_sync --host http://localhost:8000 --api-key KEY
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger incremental RAG sync")
    parser.add_argument(
        "--host",
        default="http://localhost:8000",
        help="MostyAPI base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("MOSTY_RAG_API_KEY", ""),
        help="API key",
    )
    args = parser.parse_args()

    url = f"{args.host}/api/v1/rag/scheduler/run-now/incremental"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if args.api_key:
        headers["x-mosty-api-key"] = args.api_key

    print(f"Triggering incremental sync → {url}")
    start = time.time()

    try:
        with httpx.Client(timeout=600) as client:
            resp = client.post(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"HTTP {exc.response.status_code}: {exc.response.text}")
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    elapsed = time.time() - start
    print(f"Incremental sync triggered in {elapsed:.1f}s")
    print(f"Response: {data}")


if __name__ == "__main__":
    main()
