"""Strategy extraction pipeline for content pieces (Track B).

Mirrors src/extraction/extractor.py's structure and error-handling pattern,
but runs as an independent pass: it reads/writes strategies_processed_at /
strategies_processing_error on content_pieces, never processed_at /
processing_error (Track A's columns), so the two tracks never clobber each
other's state on the same piece.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

import pendulum
from loguru import logger

from src.db.queries import (
    fetch_by_id,
    fetch_claims_by_content_piece,
    fetch_unprocessed_content_pieces_for_strategies,
    insert_many,
    update_by_id,
)
from src.extraction.normalization import normalize_entity, normalize_strategies
from src.llm.claude_client import claude


def _coerce_datetime(value: Any) -> datetime | None:
    """Parse an ISO-like timestamp into a timezone-aware datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return pendulum.parse(value)
        except (ValueError, TypeError):
            logger.warning(f"Unable to parse timestamp: {value!r}")
    return None


def parse_strategies_response(raw_response: str) -> list[dict[str, Any]]:
    """Parse Claude JSON output and return a validated strategy list."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON from Claude strategy extraction") from exc

    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Claude response must be a JSON array of strategy objects")

    return payload


def _resolve_linked_claim_ids(content_piece_id: str, linked_claim_entity: str | None) -> list[str]:
    """
    Resolve a strategy's linked_claim_entity (an entity name, from the LLM)
    to actual claim UUIDs by matching against claims already persisted for
    this content piece. Code does this match, not the LLM — it has no
    visibility into claim rows already in the database.

    Returns an empty list (never an error) if there's no entity to match,
    Track A hasn't run for this piece yet, or nothing matches.
    """
    if not linked_claim_entity:
        return []

    claims = fetch_claims_by_content_piece(content_piece_id)
    return [
        claim["id"]
        for claim in claims
        if normalize_entity(claim.get("entity")) == linked_claim_entity
    ]


def extract_strategies_for_piece(piece: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract strategies for one content piece and persist them to the
    strategies table.

    The flow:
      1. Call Claude to extract raw strategies from the transcript
      2. Parse the JSON response
      3. Run through the normalization pipeline (entity_class canonicalization,
         within-content dedup)
      4. Resolve each strategy's linked_claim_entity to actual claim ids
      5. Insert each normalized strategy into the database
    """
    expert_id = piece.get("expert_id")
    if not expert_id:
        raise ValueError("Content piece is missing expert_id")

    expert = fetch_by_id("experts", expert_id)
    if not expert:
        raise ValueError(f"Expert not found for content piece {piece.get('id')}")

    transcript = piece.get("raw_text") or ""
    if not transcript.strip():
        logger.warning(f"Skipping piece {piece.get('id')} because raw_text is empty")
        return []

    published_at = _coerce_datetime(piece.get("published_at"))
    if published_at is None:
        logger.warning(f"Skipping piece {piece.get('id')} because published_at is invalid")
        return []

    # --- Step 1: Call Claude to extract raw strategies ---
    variables = {
        "expert_name": expert.get("name", "Unknown expert"),
        "source_type": expert.get("source_type", "unknown"),
        "published_at": str(piece.get("published_at") or ""),
    }

    response = claude.call(
        prompt_name="strategy_extraction_v1",
        content=transcript,
        variables=variables,
    )

    # --- Step 2: Parse the JSON response ---
    raw_strategies = parse_strategies_response(response)
    if not raw_strategies:
        logger.info(f"No strategies extracted for piece {piece.get('id')}")
        return []

    # --- Step 3: Run normalization pipeline ---
    clean_strategies = normalize_strategies(raw_strategies)

    if not clean_strategies:
        logger.warning(f"All strategies filtered out by normalization for piece {piece.get('id')}")
        return []

    # --- Step 4 + 5: Resolve linked claims, then insert in one batch ---
    # Same atomicity rationale as extractor.py's batch insert: a mid-loop
    # crash before strategies_processed_at is set must not leave partial
    # rows with no way to detect them on retry.
    rows = [
        {
            "content_piece_id": piece["id"],
            "expert_id": expert_id,
            "source_type": "expert_extracted",
            "linked_claim_ids": _resolve_linked_claim_ids(
                piece["id"], strategy["linked_claim_entity"]
            ),
            "strategy_text": strategy["strategy_text"],
            "strategy_type": strategy["strategy_type"],
            "trigger_conditions": strategy["trigger_conditions"],
            "entity_class": strategy["entity_class"],
            "allocation_rule": strategy["allocation_rule"],
            "position_sizing": strategy["position_sizing"],
            "entry_methodology": strategy["entry_methodology"],
            "holding_horizon_months": strategy["holding_horizon_months"],
            "exit_conditions": strategy["exit_conditions"],
            "risk_framework": strategy["risk_framework"],
            "reason_given": strategy["reason_given"],
        }
        for strategy in clean_strategies
    ]
    return insert_many("strategies", rows)


def run_strategy_extraction(max_pieces: int | None = None) -> int:
    """Run Track B extraction for content pieces not yet strategies_processed."""
    pieces = fetch_unprocessed_content_pieces_for_strategies(limit=max_pieces or 100)
    logger.info(f"Found {len(pieces)} content pieces unprocessed for strategies")

    processed_count = 0
    total_pieces = len(pieces)
    for index, piece in enumerate(pieces, start=1):
        piece_id = piece.get("id")
        remaining = total_pieces - index
        logger.info(
            f"Processing {index}/{total_pieces} ({remaining} remaining): "
            f"piece {piece_id}"
        )
        try:
            inserted = extract_strategies_for_piece(piece)
            update_by_id(
                "content_pieces",
                piece_id,
                {"strategies_processed_at": pendulum.now("UTC").to_iso8601_string()},
            )
            processed_count += 1
            logger.info(
                f"Processed {index}/{total_pieces} piece {piece_id} "
                f"({len(inserted)} strategies inserted)"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to process piece {piece_id}: {exc}")
            # Mark strategies_processed_at on failure too, same rationale as
            # Track A: leaving it NULL re-sends the same broken piece to
            # Claude on every run at unbounded API cost. Requeue manually by
            # clearing both fields after a prompt/code fix.
            update_by_id(
                "content_pieces",
                piece_id,
                {
                    "strategies_processed_at": pendulum.now("UTC").to_iso8601_string(),
                    "strategies_processing_error": str(exc),
                },
            )

    return processed_count


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for strategy extraction."""
    parser = argparse.ArgumentParser(description="Extract strategies from content pieces (Track B)")
    parser.add_argument(
        "--max-pieces",
        type=int,
        default=None,
        help="Limit the number of unprocessed pieces to process for a test run",
    )
    args = parser.parse_args(argv)

    count = run_strategy_extraction(max_pieces=args.max_pieces)
    print(f"Processed {count} content pieces")


if __name__ == "__main__":
    main()
