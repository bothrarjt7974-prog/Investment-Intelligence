"""
Structural theme synthesis (Layer 6).

Across quality-passed, long-horizon claims from the last N days, cluster by
canonical entity (deterministic, code — see module docstring in
src/internal/theme_scoring.py for why clustering isn't embedding-based
yet), run each cluster through an Opus independence-classification +
thesis-synthesis pass, and combine that with each contributing expert's
credibility (src/internal/theme_scoring.py) into a themes row. Mirrors the
CLI/loop/logging pattern used by every other pipeline module in this repo.
"""

from __future__ import annotations

import argparse
import json
from statistics import median
from typing import Any

import pendulum
from loguru import logger

from src.db.queries import (
    fetch_experts_by_ids,
    fetch_synthesizable_claims,
    fetch_where,
    insert_one,
    update_by_id,
)
from src.internal.theme_scoring import VALID_INDEPENDENCE_LABELS, aggregate_cluster
from src.llm.claude_client import claude
from src.synthesis.entity_hierarchy import resolve_entity_cluster


def cluster_claims_by_entity(claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Group claims by hierarchy-resolved cluster key — deterministic, no
    LLM. Claims on child entities (MSFT, us_software, global_ai...) fold
    into their parent cluster (us_tech_ai) so multi-expert convergence on
    one economic story is counted at the level the thesis lives, instead
    of fragmenting across per-ticker clusters that each stay below the
    conviction bar. See src/synthesis/entity_hierarchy.py.
    """
    clusters: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        entity = claim.get("entity")
        if not entity:
            continue
        clusters.setdefault(resolve_entity_cluster(entity), []).append(claim)
    return clusters


def _build_claims_block(claims: list[dict[str, Any]], expert_by_id: dict[str, dict[str, Any]]) -> str:
    """Render a cluster's claims into the text block theme_synthesis_v1.md expects."""
    lines = []
    for claim in claims:
        expert_name = expert_by_id.get(claim.get("expert_id"), {}).get("name", "Unknown expert")
        lines.append(
            f"- claim_id: {claim['id']}\n"
            f"  expert_name: {expert_name}\n"
            f"  claim_text: {claim.get('claim_text', '')}\n"
            f"  reason_given: {claim.get('reason_given', '')}\n"
            f"  direction: {claim.get('direction', 'na')}\n"
            f"  target_horizon_months: {claim.get('target_horizon_months', 'null')}\n"
            f"  claimed_at: {claim.get('claimed_at', '')}"
        )
    return "\n".join(lines)


def parse_synthesis_response(raw_response: str) -> dict[str, Any]:
    """Parse Claude JSON output for a theme synthesis pass and validate shape."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON from theme synthesis") from exc

    if not isinstance(payload, dict):
        raise ValueError("Theme synthesis response must be a JSON object")

    claim_independence = payload.get("claim_independence")
    if not isinstance(claim_independence, list):
        raise ValueError("Theme synthesis response missing 'claim_independence' list")

    for entry in claim_independence:
        if not isinstance(entry, dict) or "claim_id" not in entry or "independence_label" not in entry:
            raise ValueError(f"Malformed claim_independence entry: {entry}")
        if entry["independence_label"] not in VALID_INDEPENDENCE_LABELS:
            raise ValueError(f"Invalid independence_label: {entry['independence_label']!r}")

    if not payload.get("summary_thesis"):
        raise ValueError("Theme synthesis response missing 'summary_thesis'")

    payload.setdefault("cycle_position", None)
    payload.setdefault("dissenting_views", None)
    payload.setdefault("instruments_mentioned", [])

    return payload


def synthesize_cluster(
    entity: str,
    claims: list[dict[str, Any]],
    window_start_date: str,
    window_end_date: str,
) -> dict[str, Any] | None:
    """
    Synthesize one entity cluster into a themes row. Returns None (caller
    logs a skip) if none of the cluster's claims resolve to a known expert.
    """
    expert_ids = sorted({c["expert_id"] for c in claims if c.get("expert_id")})
    experts = fetch_experts_by_ids(expert_ids)
    expert_by_id = {e["id"]: e for e in experts}
    if not expert_by_id:
        logger.warning(f"Cluster '{entity}': no resolvable experts, skipping")
        return None

    variables = {
        "entity_cluster_key": entity,
        "claims_block": _build_claims_block(claims, expert_by_id),
    }
    response = claude.synthesize(prompt_name="theme_synthesis_v1", content="", variables=variables)
    payload = parse_synthesis_response(response)

    independence_labels = {
        entry["claim_id"]: entry["independence_label"] for entry in payload["claim_independence"]
    }
    scoring = aggregate_cluster(claims, independence_labels, expert_by_id)

    horizons = [c["target_horizon_months"] for c in claims if c.get("target_horizon_months")]
    # Median, not max — one long-dated outlier claim shouldn't inflate the
    # theme's stated horizon when most of the cluster converges shorter.
    time_horizon_years = round(median(horizons) / 12, 1) if horizons else None

    geographies = [c["geography"] for c in claims if c.get("geography")]
    geography = max(set(geographies), key=geographies.count) if geographies else None

    # Child entities the cluster's claims actually named (tickers folded
    # in by the hierarchy) must surface as instruments: guidance grounding
    # and portfolio-alignment checks match holdings against
    # instruments_mentioned, not against the parent cluster key.
    claim_entities = sorted({c["entity"] for c in claims if c.get("entity")})
    instruments = list(
        dict.fromkeys([*(payload["instruments_mentioned"] or []), *claim_entities])
    )

    return {
        "entity_cluster_key": entity,
        "geography": geography,
        "summary_thesis": payload["summary_thesis"],
        "cycle_position": payload["cycle_position"],
        "dissenting_views": payload["dissenting_views"],
        "instruments_mentioned": instruments,
        "time_horizon_years": time_horizon_years,
        "independence_score": scoring["independence_score"],
        "credibility_weighted_score": scoring["credibility_weighted_score"],
        "conviction_level": scoring["conviction_level"],
        "supporting_experts": scoring["supporting_experts"],
        "source_claim_ids": [c["id"] for c in claims],
        "window_start_date": window_start_date,
        "window_end_date": window_end_date,
        "status": "active",
    }


def run_theme_synthesis(window_days: int = 90, max_clusters: int | None = None) -> dict[str, int]:
    """
    Run theme synthesis over quality-passed, long-horizon claims from the
    last `window_days` days, clustered by entity.
    """
    claims = fetch_synthesizable_claims(window_days=window_days)
    clusters = cluster_claims_by_entity(claims)
    logger.info(f"Found {len(claims)} synthesizable claims across {len(clusters)} entity clusters")

    window_end_date = pendulum.now("UTC").to_date_string()
    window_start_date = pendulum.now("UTC").subtract(days=window_days).to_date_string()

    counts = {"themes_created": 0, "clusters_skipped": 0, "errors": 0, "total_claims": len(claims)}

    cluster_items = list(clusters.items())
    if max_clusters is not None:
        cluster_items = cluster_items[:max_clusters]

    for entity, cluster_claims in cluster_items:
        try:
            theme_row = synthesize_cluster(entity, cluster_claims, window_start_date, window_end_date)
            if theme_row is None:
                counts["clusters_skipped"] += 1
                continue

            # Supersede (never delete) any prior active theme for this
            # cluster — history is preserved for SEBI record-keeping.
            # Includes themes stored under the cluster's child entity keys
            # (pre-hierarchy runs created e.g. separate 'us_tech' and
            # 'global_ai' themes that this parent-key theme replaces).
            child_keys = {c["entity"] for c in cluster_claims if c.get("entity")}
            for key in {entity, *child_keys}:
                for existing in fetch_where("themes", {"entity_cluster_key": key, "status": "active"}):
                    update_by_id("themes", existing["id"], {"status": "superseded"})

            insert_one("themes", theme_row)
            counts["themes_created"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Failed to synthesize cluster '{entity}': {exc}")
            counts["errors"] += 1

    logger.info(
        f"Theme synthesis complete: {counts['themes_created']} themes created, "
        f"{counts['clusters_skipped']} clusters skipped, {counts['errors']} errors "
        f"(from {counts['total_claims']} claims)"
    )

    return counts


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for theme synthesis."""
    parser = argparse.ArgumentParser(
        description="Synthesize structural themes from quality-passed long-horizon claims"
    )
    parser.add_argument(
        "--window-days", type=int, default=90, help="How many days back to pull claims from"
    )
    parser.add_argument(
        "--max-clusters", type=int, default=None, help="Limit how many entity clusters to process for a test run"
    )
    args = parser.parse_args(argv)

    counts = run_theme_synthesis(window_days=args.window_days, max_clusters=args.max_clusters)
    print(
        f"Processed {counts['total_claims']} claims: "
        f"{counts['themes_created']} themes created, "
        f"{counts['clusters_skipped']} clusters skipped, "
        f"{counts['errors']} errors"
    )


if __name__ == "__main__":
    main()
