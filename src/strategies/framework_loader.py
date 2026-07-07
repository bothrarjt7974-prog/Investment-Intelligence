"""
CFA / institutional framework ingestion (Layer 8: strategy library, source
#3/#4).

Loads real, human-authored allocation methodologies from YAML files in
frameworks/ into the strategies table. This module builds the mechanism
only — frameworks/ ships with zero real content (see frameworks/README.md
for why: encoding real CFA/institutional methodology is finance-consultant
work, not something to fabricate for a SEBI RIA product).

No LLM calls — this is a straight parse-and-insert of human-authored
content, already vetted by definition (quality_verdict='pass' on insert;
frameworks don't go through Filter B).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from src.db.queries import fetch_existing_framework_source_names, insert_many

FRAMEWORKS_DIR = Path(__file__).parent.parent.parent / "frameworks"
TEMPLATE_FILENAME = "TEMPLATE.yaml"

REQUIRED_FIELDS = (
    "source_name",
    "source_type",
    "strategy_type",
    "strategy_text",
    "entity_class",
    "allocation_rule",
    "reason_given",
)
VALID_SOURCE_TYPES = ("cfa_framework", "institutional")

OPTIONAL_FIELDS = (
    "trigger_conditions",
    "position_sizing",
    "entry_methodology",
    "holding_horizon_months",
    "exit_conditions",
    "risk_framework",
)


def parse_framework_file(path: Path) -> dict[str, Any]:
    """Parse and validate one framework YAML file. Raises ValueError on any
    missing/invalid required field — a bad framework file must never
    silently load partial or wrong data."""
    with path.open(encoding="utf-8") as f:
        payload = yaml.safe_load(f)

    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: must be a YAML mapping")

    missing = [field for field in REQUIRED_FIELDS if not payload.get(field)]
    if missing:
        raise ValueError(f"{path.name}: missing required fields {missing}")

    if payload["source_type"] not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"{path.name}: source_type must be one of {VALID_SOURCE_TYPES}, "
            f"got {payload['source_type']!r}"
        )

    row = {field: payload[field] for field in REQUIRED_FIELDS}
    for field in OPTIONAL_FIELDS:
        row[field] = payload.get(field)

    row["quality_verdict"] = "pass"
    row["scope"] = "in_scope"
    return row


def load_all_frameworks() -> dict[str, int]:
    """
    Load every framework YAML in frameworks/ (except TEMPLATE.yaml) into
    the strategies table. Skips files whose source_name is already
    present — idempotent, safe to re-run as new frameworks are added.
    """
    if not FRAMEWORKS_DIR.exists():
        logger.warning(f"Frameworks directory not found: {FRAMEWORKS_DIR}")
        return {"loaded": 0, "skipped_existing": 0, "error": 0, "total": 0}

    files = sorted(p for p in FRAMEWORKS_DIR.glob("*.yaml") if p.name != TEMPLATE_FILENAME)
    logger.info(f"Found {len(files)} framework file(s)")

    already_loaded = fetch_existing_framework_source_names()

    counts = {"loaded": 0, "skipped_existing": 0, "error": 0, "total": len(files)}
    rows_to_insert = []

    for path in files:
        try:
            row = parse_framework_file(path)
        except (ValueError, yaml.YAMLError) as exc:
            logger.exception(f"Failed to parse framework file {path.name}: {exc}")
            counts["error"] += 1
            continue

        if row["source_name"] in already_loaded:
            logger.info(f"Skipping already-loaded framework: {row['source_name']}")
            counts["skipped_existing"] += 1
            continue

        rows_to_insert.append(row)

    if rows_to_insert:
        insert_many("strategies", rows_to_insert)
        counts["loaded"] = len(rows_to_insert)

    logger.info(
        f"Framework loading complete: {counts['loaded']} loaded, "
        f"{counts['skipped_existing']} already loaded, {counts['error']} errors "
        f"(of {counts['total']} files)"
    )
    return counts


def main() -> None:
    """Command-line entry point for framework loading."""
    counts = load_all_frameworks()
    print(
        f"Processed {counts['total']} framework files: {counts['loaded']} loaded, "
        f"{counts['skipped_existing']} already loaded, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
