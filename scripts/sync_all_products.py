#!/usr/bin/env python3
"""
Trigger a full RAG sync via the MostyAPI endpoint.

Usage:
    python -m scripts.sync_all_products

    # Or specify custom host/key:
    python -m scripts.sync_all_products --host http://localhost:8000 --api-key YOUR_KEY
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
    parser = argparse.ArgumentParser(description="Trigger full RAG sync")
    parser.add_argument(
        "--host",
        default="http://localhost:9198",
        help="MostyAPI base URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("MOSTY_RAG_API_KEY", ""),
        help="API key for MostyAPI",
    )
    args = parser.parse_args()

    url = f"{args.host}/api/v1/rag/sync"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if args.api_key:
        headers["x-mosty-api-key"] = args.api_key

    print(f"Starting full RAG sync → {url}")
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
    print(f"\nSync complete in {elapsed:.1f}s")
    print(f"  Products synced:      {data.get('products_synced', 'N/A')}")
    print(f"  Chunks created:       {data.get('chunks_created', 'N/A')}")
    print(f"  Embeddings generated: {data.get('embeddings_generated', 'N/A')}")
    print(f"  Index updated:        {data.get('index_updated', 'N/A')}")


if __name__ == "__main__":
    main()
