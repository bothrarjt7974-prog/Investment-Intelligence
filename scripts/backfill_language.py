"""
One-off backfill: set content_pieces.language from the transcript text itself.

WHY: youtube_capture hardcoded language='en' whenever an en/en-US/en-IN track
existed, so all pieces ingested before 2026-07-10 are labeled 'en' even when
raw_text is literally Devanagari (audit finding — Hindi extraction quality
could not be measured because no piece was labeled Hindi). Track labels lie;
the text doesn't: a transcript whose characters are substantially Devanagari
is Hindi regardless of what YouTube called the caption track.

Deterministic code, no LLM. Idempotent — re-running converges to the same
labels. Only flips 'en' rows to 'hi'; never touches other values.

Run from repo root:
    python -m scripts.backfill_language           # apply
    python -m scripts.backfill_language --dry-run # report only
"""

from __future__ import annotations

import argparse

from loguru import logger

from src.db.queries import supabase, update_by_id

# Devanagari block (U+0900-U+097F). Hinglish transcripts mix scripts; a 20%
# share is far above English-with-quoted-Hindi noise while catching real
# Hindi/Hinglish transcripts, whose share in practice is 60%+.
DEVANAGARI_START = 0x0900
DEVANAGARI_END = 0x097F
HINDI_MIN_DEVANAGARI_SHARE = 0.20


def devanagari_share(text: str) -> float:
    """Fraction of non-whitespace characters in the Devanagari block."""
    meaningful = [ch for ch in text if not ch.isspace()]
    if not meaningful:
        return 0.0
    hits = sum(1 for ch in meaningful if DEVANAGARI_START <= ord(ch) <= DEVANAGARI_END)
    return hits / len(meaningful)


def run(dry_run: bool = False) -> dict[str, int]:
    """Scan every content piece; relabel 'en' rows whose text is Hindi."""
    counts = {"scanned": 0, "flipped_to_hi": 0, "already_non_en": 0}
    offset, page_size = 0, 200
    while True:
        rows = (
            supabase.table("content_pieces")
            .select("id,language,raw_text")
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        )
        for row in rows:
            counts["scanned"] += 1
            language = (row.get("language") or "en").lower()
            if not language.startswith("en"):
                counts["already_non_en"] += 1
                continue
            share = devanagari_share(row.get("raw_text") or "")
            if share >= HINDI_MIN_DEVANAGARI_SHARE:
                counts["flipped_to_hi"] += 1
                if not dry_run:
                    update_by_id("content_pieces", row["id"], {"language": "hi"})
        if len(rows) < page_size:
            break
        offset += page_size

    logger.info(
        "Language backfill{}: scanned={scanned} flipped_to_hi={flipped_to_hi} "
        "already_non_en={already_non_en}",
        " (dry run)" if dry_run else "",
        **counts,
    )
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill content_pieces.language from raw_text script")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
