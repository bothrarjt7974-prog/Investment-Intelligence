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
from loguru import logger
from openai import OpenAI

from src.config.settings import settings
from src.db.queries import fetch_strategies_pending_embedding, update_by_id

CHROMA_PATH = Path(__file__).parent.parent.parent / "data" / "chroma"
COLLECTION_NAME = "strategies"

_openai_client: OpenAI | None = None
_chroma_client: chromadb.ClientAPI | None = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


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
    """Embed one piece of text via OpenAI. Real API cost — called once per strategy."""
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
    strategies = fetch_strategies_pending_embedding(limit=max_strategies or 100)
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
