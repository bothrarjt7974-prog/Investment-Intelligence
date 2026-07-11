"""Claim extraction pipeline for unprocessed content pieces."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

import pendulum
from loguru import logger

from src.config.tuning import param
from src.db.queries import fetch_by_id, fetch_unprocessed_content_pieces, insert_many, update_by_id
from src.extraction.normalization import normalize_claims
from src.llm.claude_client import claude

# Transcripts below this length (YouTube Shorts, teaser clips) never
# contain a 3-6 sentence reasoning chain worth extracting.
MIN_TRANSCRIPT_CHARS = param("extraction.min_transcript_chars")


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


class NonFinancialContentError(ValueError):
    """
    Claude answered in prose instead of JSON — in practice this means it
    correctly recognized non-financial content (movie clips, career
    advice videos picked up by channel ingestion) and declined to invent
    claims. Distinct from a real parse failure so the piece is tagged
    'non_financial_content' rather than counted as a pipeline error.
    """


def parse_claims_response(raw_response: str) -> list[dict[str, Any]]:
    """Parse Claude JSON output and return a validated claim list."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        if "[" not in cleaned:
            raise NonFinancialContentError("non_financial_content") from exc
        raise ValueError("Malformed JSON from Claude claim extraction") from exc

    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Claude response must be a JSON array of claim objects")

    return payload


def extract_claims_for_piece(piece: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract claims for one content piece and persist them to the claims table.

    The flow:
      1. Call Claude to extract raw claims from the transcript
      2. Parse the JSON response
      3. Run through the normalization pipeline (entity canonicalization,
         within-video dedup, target_date computation from horizon_months)
      4. Insert each normalized claim into the database
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

    # --- Step 1: Call Claude to extract raw claims ---
    variables = {
        "expert_name": expert.get("name", "Unknown expert"),
        "source_type": expert.get("source_type", "unknown"),
        "published_at": str(piece.get("published_at") or ""),
    }

    response = claude.call(
        prompt_name="claim_extraction_v4",
        content=transcript,
        variables=variables,
    )

    # --- Step 2: Parse the JSON response ---
    raw_claims = parse_claims_response(response)
    if not raw_claims:
        logger.info(f"No claims extracted for piece {piece.get('id')}")
        return []

    # --- Step 3: Run normalization pipeline ---
    # This handles:
    #   - Entity canonicalization (NIFTY50 → nifty50, BTC_USD → BTC, etc.)
    #   - Within-video deduplication (merges similar claims)
    #   - target_date computation from target_horizon_months
    clean_claims = normalize_claims(raw_claims, published_at)

    if not clean_claims:
        logger.warning(f"All claims filtered out by normalization for piece {piece.get('id')}")
        return []

    # --- Step 4: Insert all cleaned claims in one batch ---
    # A single request is atomic on the PostgREST side: either every claim for
    # this piece persists or none do. Inserting row-by-row left a window where
    # a mid-loop crash (before processed_at was set) caused the retry to
    # re-insert the claims that had already landed — duplicate rows with no
    # dedup key to catch them.
    rows = [
        {
            "content_piece_id": piece["id"],
            "expert_id": expert_id,
            "claim_text": claim["claim_text"],
            "claim_type": claim["claim_type"],
            "geography": claim["geography"],
            "timeframe": claim["timeframe"],
            "strength": claim["strength"],
            "entity": claim["entity"],
            "direction": claim["direction"],
            "reason_given": claim["reason_given"],
            "claimed_at": piece.get("published_at"),
            "target_date": claim["target_date"],
            "target_horizon_months": claim["target_horizon_months"],
            "is_conditional": claim["is_conditional"],
            "condition_text": claim["condition_text"],
        }
        for claim in clean_claims
    ]
    return insert_many("claims", rows)


def run_extraction(max_pieces: int | None = None) -> int:
    """Run extraction for unprocessed content pieces."""
    pieces = fetch_unprocessed_content_pieces(limit=max_pieces or 100)
    logger.info(f"Found {len(pieces)} unprocessed content pieces")

    processed_count = 0
    total_pieces = len(pieces)
    for index, piece in enumerate(pieces, start=1):
        piece_id = piece.get("id")
        remaining = total_pieces - index
        logger.info(
            f"Processing {index}/{total_pieces} ({remaining} remaining): "
            f"piece {piece_id}"
        )

        # Shorts and clips under ~500 chars carry no extractable reasoning
        # chain — skip before spending an API call. Deterministic, free.
        if len((piece.get("raw_text") or "").strip()) < MIN_TRANSCRIPT_CHARS:
            update_by_id(
                "content_pieces",
                piece_id,
                {
                    "processed_at": pendulum.now("UTC").to_iso8601_string(),
                    "processing_error": "too_short",
                },
            )
            processed_count += 1
            logger.info(f"Skipped {index}/{total_pieces} piece {piece_id} (too_short)")
            continue

        try:
            inserted = extract_claims_for_piece(piece)
            update_by_id(
                "content_pieces",
                piece_id,
                {"processed_at": pendulum.now("UTC").to_iso8601_string()},
            )
            processed_count += 1
            logger.info(
                f"Processed {index}/{total_pieces} piece {piece_id} "
                f"({len(inserted)} claims inserted)"
            )
        except NonFinancialContentError:
            # Correct model behavior on junk content, not a failure —
            # tagged distinctly so error queries don't count it.
            update_by_id(
                "content_pieces",
                piece_id,
                {
                    "processed_at": pendulum.now("UTC").to_iso8601_string(),
                    "processing_error": "non_financial_content",
                },
            )
            processed_count += 1
            logger.info(f"Piece {piece_id}: non-financial content, no claims")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to process piece {piece_id}: {exc}")
            # Set processed_at on failure too (tag, don't retry forever):
            # leaving it NULL re-sent the same broken piece to Claude on every
            # run — unbounded API cost for a piece that will keep failing.
            # Failed pieces stay queryable via processing_error IS NOT NULL;
            # clear both fields manually to requeue after a prompt/code fix.
            update_by_id(
                "content_pieces",
                piece_id,
                {
                    "processed_at": pendulum.now("UTC").to_iso8601_string(),
                    "processing_error": str(exc),
                },
            )

    return processed_count


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for extraction."""
    parser = argparse.ArgumentParser(description="Extract claims from unprocessed content pieces")
    parser.add_argument(
        "--max-pieces",
        type=int,
        default=None,
        help="Limit the number of unprocessed pieces to process for a test run",
    )
    args = parser.parse_args(argv)

    count = run_extraction(max_pieces=args.max_pieces)
    print(f"Processed {count} content pieces")


if __name__ == "__main__":
    main()
