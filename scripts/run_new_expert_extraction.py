"""
One-off driver: run Track A + Track B extraction on only the new-expert
content pieces (the 135 pieces from the 9 experts unblocked by today's
language-fallback fix), explicitly excluding Akshat's 702-piece backlog
which is a separate, deliberately-deferred full-backlog decision.

Not part of the permanent pipeline — delete after this run, or keep as a
reference pattern for future scoped reprocessing.
"""

from __future__ import annotations

import pendulum
from loguru import logger

from src.db.queries import fetch_unprocessed_content_pieces, fetch_unprocessed_content_pieces_for_strategies, fetch_where, update_by_id
from src.extraction.extractor import extract_claims_for_piece
from src.extraction.strategy_extractor import extract_strategies_for_piece

AKSHAT_EXPERT_ID = "a11c8ced-1756-49c8-aae2-b4fdd66e3e54"


def run_claims():
    pieces = [p for p in fetch_unprocessed_content_pieces(limit=1000) if p["expert_id"] != AKSHAT_EXPERT_ID]
    logger.info(f"Track A: {len(pieces)} new-expert pieces to process")
    for i, piece in enumerate(pieces, 1):
        piece_id = piece["id"]
        try:
            inserted = extract_claims_for_piece(piece)
            update_by_id("content_pieces", piece_id, {"processed_at": pendulum.now("UTC").to_iso8601_string()})
            logger.info(f"[{i}/{len(pieces)}] claims OK: {piece_id} ({len(inserted)} inserted)")
        except Exception as exc:
            logger.exception(f"[{i}/{len(pieces)}] claims FAILED: {piece_id}: {exc}")
            update_by_id("content_pieces", piece_id, {"processed_at": pendulum.now("UTC").to_iso8601_string(), "processing_error": str(exc)})


def run_strategies():
    pieces = [p for p in fetch_unprocessed_content_pieces_for_strategies(limit=1000) if p["expert_id"] != AKSHAT_EXPERT_ID]
    logger.info(f"Track B: {len(pieces)} new-expert pieces to process")
    for i, piece in enumerate(pieces, 1):
        piece_id = piece["id"]
        try:
            inserted = extract_strategies_for_piece(piece)
            update_by_id("content_pieces", piece_id, {"strategies_processed_at": pendulum.now("UTC").to_iso8601_string()})
            logger.info(f"[{i}/{len(pieces)}] strategies OK: {piece_id} ({len(inserted)} inserted)")
        except Exception as exc:
            logger.exception(f"[{i}/{len(pieces)}] strategies FAILED: {piece_id}: {exc}")
            update_by_id("content_pieces", piece_id, {"strategies_processed_at": pendulum.now("UTC").to_iso8601_string(), "strategies_processing_error": str(exc)})


if __name__ == "__main__":
    import sys

    track = sys.argv[1] if len(sys.argv) > 1 else "both"
    if track in ("claims", "both"):
        run_claims()
    if track in ("strategies", "both"):
        run_strategies()
