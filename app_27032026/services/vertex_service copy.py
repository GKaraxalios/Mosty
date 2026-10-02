from __future__ import annotations

import json
import os
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_vertex_initialized = False


def _ensure_vertex_init() -> None:
    global _vertex_initialized
    if _vertex_initialized:
        return
    settings = get_settings()

    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            settings.GOOGLE_APPLICATION_CREDENTIALS,
        )

    if not settings.GOOGLE_CLOUD_PROJECT:
        logger.warning(
            "GOOGLE_CLOUD_PROJECT not set — Vertex AI calls will fail until configured"
        )
        _vertex_initialized = True
        return

    import vertexai
    vertexai.init(
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )
    _vertex_initialized = True
    logger.info(
        "Vertex AI initialized: project=%s, location=%s",
        settings.GOOGLE_CLOUD_PROJECT,
        settings.GOOGLE_CLOUD_LOCATION,
    )


class VertexEmbeddingService:
    """Generates text embeddings via Vertex AI."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput

        model = TextEmbeddingModel.from_pretrained(self._settings.VERTEX_EMBEDDING_MODEL)

        batch_size = 250
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = [TextEmbeddingInput(text=t, task_type="RETRIEVAL_DOCUMENT") for t in batch]
            embeddings = model.get_embeddings(inputs)
            all_embeddings.extend([e.values for e in embeddings])
            logger.info("Embedded batch %d–%d of %d", i, i + len(batch), len(texts))

        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput

        model = TextEmbeddingModel.from_pretrained(self._settings.VERTEX_EMBEDDING_MODEL)
        inputs = [TextEmbeddingInput(text=text, task_type="RETRIEVAL_QUERY")]
        embeddings = model.get_embeddings(inputs)
        return embeddings[0].values


class VertexVectorSearchService:
    """Manages vectors in Vertex AI Vector Search (Matching Engine)."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()

    def _normalize_vertex_id(value: str) -> str:
        if not value:
            return value
        value = value.strip()
        if "/" in value:
            return value.rstrip("/").split("/")[-1]
        return value

    def _get_index(self):
        from google.cloud import aiplatform
        return aiplatform.MatchingEngineIndex(
            index_name=self._normalize_vertex_id(self._settings.VERTEX_INDEX_ID),
            project=self._settings.GOOGLE_CLOUD_PROJECT,
            location=self._settings.GOOGLE_CLOUD_LOCATION,
        )

    def _get_index_endpoint(self):
        from google.cloud import aiplatform
        return aiplatform.MatchingEngineIndexEndpoint(
            index_endpoint_name=self._normalize_vertex_id(self._settings.VERTEX_INDEX_ENDPOINT_ID),
            project=self._settings.GOOGLE_CLOUD_PROJECT,
            location=self._settings.GOOGLE_CLOUD_LOCATION,
        )

    async def upsert_vectors(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata_list: list[dict[str, Any]],
    ) -> bool:
        """Upsert vectors into the deployed index."""
        from google.cloud import aiplatform

        index = self._get_index()

        datapoints = []
        for vid, emb, meta in zip(ids, embeddings, metadata_list):
            restricts = []
            for key, value in meta.items():
                if isinstance(value, list):
                    for v in value:
                        restricts.append(
                            aiplatform.matching_engine.matching_engine_index_endpoint.Namespace(
                                name=key, allow_tokens=[str(v)]
                            )
                        )
                elif isinstance(value, (str, int, float)):
                    restricts.append(
                        aiplatform.matching_engine.matching_engine_index_endpoint.Namespace(
                            name=key, allow_tokens=[str(value)]
                        )
                    )

            datapoints.append(
                {
                    "datapoint_id": vid,
                    "feature_vector": emb,
                }
            )

        index.upsert_datapoints(datapoints=datapoints)
        logger.info("Upserted %d vectors to index", len(ids))
        return True

    async def remove_vectors(self, ids: list[str]) -> int:
        index = self._get_index()
        index.remove_datapoints(datapoint_ids=ids)
        logger.info("Removed %d vectors from index", len(ids))
        return len(ids)

    async def find_neighbors(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        endpoint = self._get_index_endpoint()
        deployed_index_id = self._settings.VERTEX_DEPLOYED_INDEX_ID

        response = endpoint.find_neighbors(
            deployed_index_id=deployed_index_id,
            queries=[query_embedding],
            num_neighbors=top_k,
        )

        results: list[dict[str, Any]] = []
        for neighbor_group in response:
            for neighbor in neighbor_group:
                results.append(
                    {
                        "id": neighbor.id,
                        "score": neighbor.distance,
                    }
                )
        return results


class VertexGenerativeService:
    """Generates text answers via Vertex AI generative models."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()

    async def generate(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> str:
        from vertexai.generative_models import GenerativeModel, GenerationConfig

        model = GenerativeModel(
            model_name=self._settings.VERTEX_GENERATIVE_MODEL,
            system_instruction=system_instruction,
        )

        config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        response = model.generate_content(
            prompt,
            generation_config=config,
        )
        return response.text
