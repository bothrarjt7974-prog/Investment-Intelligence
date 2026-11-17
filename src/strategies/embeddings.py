"""
Strategy embeddings — OpenAI embedding calls + local Chroma persistence
(Layer 8: strategy library + matcher).

Every quality-passed, in-scope strategy is embedded once and upserted into
a local Chroma collection alongside filterable metadata (entity_class,
scope, quality_verdict, source_type). src/strategies/matcher.py queries
this collection with a combined semantic + metadata-filter call — see that
module's docstring for why structured filter and semantic rank fuse into
one Chroma query instead of two separate passes.

Chroma persists locally at data/chroma/ (already covered by this repo's
blanket `data/` gitignore rule — a rebuildable local index, not source).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import chromadb
import pendulum
from chromadb.utils import embedding_functions
from loguru import logger

from src.config.settings import settings
from src.db.queries import fetch_strategies_pending_embedding, update_by_id

CHROMA_PATH = Path(__file__).parent.parent.parent / "data" / "chroma"
COLLECTION_NAME = "strategies"

_openai_client: Any | None = None
_chroma_client: chromadb.ClientAPI | None = None
_local_embedder: Any | None = None


# A full run (no --max-* flag) means everything pending. The old `or 100`
# default silently capped nightly runs; 1000 is PostgREST's per-request ceiling.
FULL_RUN_LIMIT = 1000

def _get_openai_client() -> Any:
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _get_local_embedder() -> Any:
    # Chroma's bundled all-MiniLM-L6-v2 (onnxruntime, runs locally, no API
    # key). Weaker than OpenAI's text-embedding-3-small, but the strategy
    # library is small (~20-30 items) and the matcher's structured
    # entity_class filter does most of the discriminating work — the
    # ranking-quality gap is marginal at this scale. Default provider
    # until OpenAI billing (separate account from Anthropic) is resolved.
    global _local_embedder
    if _local_embedder is None:
        _local_embedder = embedding_functions.DefaultEmbeddingFunction()
    return _local_embedder


def get_chroma_collection() -> chromadb.Collection:
    """
    Return the (persistent, on-disk) Chroma collection strategies are
    embedded into. Explicitly configured for cosine distance — Chroma
    defaults to squared L2, not cosine (verified empirically); the plan
    calls for cosine similarity ranking specifically.
    """
    global _chroma_client
    if _chroma_client is None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _chroma_client.get_or_create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def get_embedding(text: str) -> list[float]:
    """
    Embed one piece of text via the configured provider
    (settings.embedding_provider). Local is free; OpenAI is real API cost
    per call. Both paths feed the same Chroma collection — switching
    providers requires re-embedding (dimensions differ), see settings.py.
    """
    if settings.embedding_provider == "local":
        return _get_local_embedder()([text])[0]
    response = _get_openai_client().embeddings.create(model=settings.embedding_model, input=text)
    return response.data[0].embedding


def embed_strategy(strategy: dict[str, Any]) -> None:
    """
    Embed one strategy's text and upsert it into the Chroma collection,
    attaching the metadata the matcher's structured filter needs. Marks
    embedded_at on the strategies row so re-runs are idempotent.
    """
    strategy_id = strategy["id"]
    embedding = get_embedding(strategy["strategy_text"])

    collection = get_chroma_collection()
    collection.upsert(
        ids=[strategy_id],
        embeddings=[embedding],
        documents=[strategy["strategy_text"]],
        metadatas=[
            {
                # Chroma metadata values must be str/int/float/bool — None
                # fields are coerced to empty string, never crash the upsert.
                "entity_class": strategy.get("entity_class") or "",
                "scope": strategy.get("scope") or "in_scope",
                "quality_verdict": strategy.get("quality_verdict") or "",
                "source_type": strategy.get("source_type") or "expert_extracted",
            }
        ],
    )
    update_by_id(
        "strategies", strategy_id, {"embedded_at": pendulum.now("UTC").to_iso8601_string()}
    )


def sync_strategy_embeddings(max_strategies: int | None = None) -> dict[str, int]:
    """Embed every quality-passed, in-scope strategy not yet embedded."""
    strategies = fetch_strategies_pending_embedding(limit=max_strategies or FULL_RUN_LIMIT)
    logger.info(f"Found {len(strategies)} strategies pending embedding")

    counts = {"embedded": 0, "error": 0, "total": len(strategies)}

    for index, strategy in enumerate(strategies, start=1):
        strategy_id = strategy.get("id")
        try:
            embed_strategy(strategy)
            counts["embedded"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to embed strategy {strategy_id}: {exc}")
            counts["error"] += 1

        if index % 25 == 0 or index == len(strategies):
            logger.info(f"Embedded {index}/{len(strategies)} strategies")

    logger.info(
        f"Embedding sync complete: {counts['embedded']} embedded, "
        f"{counts['error']} errors (of {counts['total']})"
    )
    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for the embedding sync."""
    parser = argparse.ArgumentParser(description="Embed quality-passed strategies into Chroma")
    parser.add_argument(
        "--max-strategies", type=int, default=None, help="Limit how many strategies to embed for a test run"
    )
    args = parser.parse_args(argv)

    counts = sync_strategy_embeddings(max_strategies=args.max_strategies)
    print(f"Embedded {counts['embedded']}/{counts['total']} strategies, {counts['error']} errors")


if __name__ == "__main__":
    main()
