"""
Daily ingestion orchestrator.

Pulls latest content from every active expert across all source types.
Run via cron at 11pm daily.

Usage:
    python -m src.ingestion.run_all
"""

from __future__ import annotations

from loguru import logger

from src.db.queries import fetch_where
from src.ingestion import youtube


def run_youtube_ingestion() -> dict[str, int]:
    """Ingest from all active YouTube experts. Returns counts per expert."""
    experts = fetch_where("experts", {"source_type": "youtube", "is_active": True})
    logger.info(f"Found {len(experts)} active YouTube experts")

    counts: dict[str, int] = {}
    for expert in experts:
        try:
            ingested = youtube.ingest_channel(
                expert_id=expert["id"],
                channel_handle=expert["handle"],
                max_videos=30,
            )
            counts[expert["name"]] = len(ingested)
        except Exception as e:
            logger.exception(f"Failed to ingest {expert['name']}: {e}")
            counts[expert["name"]] = -1

    return counts


def main() -> None:
    logger.info("=" * 60)
    logger.info("Starting daily ingestion run")
    logger.info("=" * 60)

    yt_counts = run_youtube_ingestion()

    # TODO: add blog ingestion
    # TODO: add podcast ingestion

    logger.info("=" * 60)
    logger.info("Ingestion summary:")
    for name, count in yt_counts.items():
        status = f"{count} new" if count >= 0 else "FAILED"
        logger.info(f"  {name}: {status}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
