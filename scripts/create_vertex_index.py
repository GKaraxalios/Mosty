#!/usr/bin/env python3
"""
Create a Vertex AI Vector Search index and deploy it to an endpoint.

Usage:
    python -m scripts.create_vertex_index

Environment variables required (from .env):
    GOOGLE_CLOUD_PROJECT
    GOOGLE_CLOUD_LOCATION
    GOOGLE_APPLICATION_CREDENTIALS

This script will:
1. Create a Matching Engine index (stream update, cosine distance)
2. Create an index endpoint
3. Deploy the index to the endpoint
4. Print the IDs to add to your .env
"""
from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not project:
        print("ERROR: GOOGLE_CLOUD_PROJECT not set in .env")
        sys.exit(1)

    if credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

    from google.cloud import aiplatform

    aiplatform.init(project=project, location=location)

    # ── 1. Create index ──────────────────────────────────────────────────
    print("Creating Vertex AI Vector Search index…")
    print("  (shard_size=SMALL — suitable for up to ~100k vectors)")
    index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
        display_name="mostyapi-v2-index",
        description="MostyAPI v2 product catalog vector index",
        dimensions=768,
        approximate_neighbors_count=150,
        distance_measure_type="COSINE_DISTANCE",
        leaf_node_embedding_count=500,
        leaf_nodes_to_search_percent=7,
        index_update_method="STREAM_UPDATE",
        shard_size="SHARD_SIZE_SMALL",
    )
    print(f"  Index created: {index.resource_name}")

    # ── 2. Create endpoint ───────────────────────────────────────────────
    print("Creating index endpoint…")
    endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
        display_name="mostyapi-v2-endpoint",
        description="MostyAPI v2 product catalog search endpoint",
        public_endpoint_enabled=True,
    )
    print(f"  Endpoint created: {endpoint.resource_name}")

    # ── 3. Deploy index ──────────────────────────────────────────────────
    deployed_index_id = "mostyapi_v2_deployed"
    print(f"Deploying index as '{deployed_index_id}'…")
    endpoint.deploy_index(
        index=index,
        deployed_index_id=deployed_index_id,
        display_name="mosty-products-deployed",
        machine_type="e2-standard-2",
        min_replica_count=1,
        max_replica_count=1,
    )
    print("  Deployment complete!")

    # ── 4. Print .env values ─────────────────────────────────────────────
    index_id = index.resource_name.split("/")[-1]
    endpoint_id = endpoint.resource_name.split("/")[-1]

    print("\n" + "=" * 60)
    print("Add these to your .env file:")
    print("=" * 60)
    print(f"VERTEX_INDEX_ID={index_id}")
    print(f"VERTEX_INDEX_ENDPOINT_ID={endpoint_id}")
    print(f"VERTEX_DEPLOYED_INDEX_ID={deployed_index_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
