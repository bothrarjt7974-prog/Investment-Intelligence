"""
Database access layer.

All database access in this codebase routes through this module.
Uses Supabase Python client for higher-level operations,
SQLAlchemy for complex queries when needed.
"""

from __future__ import annotations

from typing import Any

import pendulum
from loguru import logger
from supabase import Client, create_client

from src.config.settings import settings


def get_supabase_client() -> Client:
    """Return a configured Supabase client."""
    return create_client(settings.supabase_url, settings.supabase_key)


# Module-level singleton
supabase: Client = get_supabase_client()


def insert_one(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Insert a single row into a table. Returns the inserted row."""
    logger.debug(f"Inserting into {table}: {row.get('id', '<new>')}")
    response = supabase.table(table).insert(row).execute()
    return response.data[0] if response.data else {}


def insert_many(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bulk insert. Returns inserted rows."""
    if not rows:
        return []
    logger.debug(f"Bulk inserting {len(rows)} rows into {table}")
    response = supabase.table(table).insert(rows).execute()
    return response.data


def fetch_by_id(table: str, row_id: str) -> dict[str, Any] | None:
    """Fetch a single row by ID."""
    response = supabase.table(table).select("*").eq("id", row_id).limit(1).execute()
    return response.data[0] if response.data else None


def fetch_unprocessed_content_pieces(limit: int = 100) -> list[dict[str, Any]]:
    """
    Fetch unprocessed content pieces where processed_at is null.

    Oldest published_at first: claims extracted from old content already
    have resolvable target_dates (their claimed horizons have passed), so
    they can be validated immediately instead of waiting years — this is
    the "backfill imperative" from the technical plan (§8).
    """
    response = (
        supabase.table("content_pieces")
        .select("*")
        .is_("processed_at", "null")
        .order("published_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_where(
    table: str, filters: dict[str, Any], limit: int = 100
) -> list[dict[str, Any]]:
    """Fetch rows matching multiple equality filters."""
    query = supabase.table(table).select("*")
    for column, value in filters.items():
        query = query.eq(column, value)
    response = query.limit(limit).execute()
    return response.data


def update_by_id(table: str, row_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Update a row by ID. Returns updated row."""
    response = supabase.table(table).update(updates).eq("id", row_id).execute()
    return response.data[0] if response.data else {}


def fetch_unfiltered_claims(limit: int = 100) -> list[dict]:
    """
    Return claims that have not yet been quality-screened.

    A claim is "unfiltered" when quality_verdict IS NULL.
    Failed claims (quality_verdict = 'fail') are NOT re-screened.
    """
    response = (
        supabase.table("claims")
        .select("*")
        .is_("quality_verdict", "null")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def upsert_many(
    table: str, rows: list[dict[str, Any]], on_conflict: str
) -> list[dict[str, Any]]:
    """
    Bulk upsert. Rows conflicting on `on_conflict` columns are skipped
    (ignore_duplicates=True) rather than overwritten — market_data snapshots
    are immutable once written, re-running a backfill should just no-op on
    dates already present.
    """
    if not rows:
        return []
    logger.debug(f"Bulk upserting {len(rows)} rows into {table} (on_conflict={on_conflict})")
    response = (
        supabase.table(table)
        .upsert(rows, on_conflict=on_conflict, ignore_duplicates=True)
        .execute()
    )
    return response.data or []


def symbol_has_market_data(symbol: str) -> bool:
    """True if `symbol` has at least one row in market_data — used to decide
    whether an on-demand single-symbol backfill is needed before validating
    a fallback-resolved entity (one never covered by the bulk backfill)."""
    response = (
        supabase.table("market_data").select("id").eq("symbol", symbol).limit(1).execute()
    )
    return bool(response.data)


def fetch_latest_market_price(
    symbol: str, on_or_before: str, earliest: str
) -> dict[str, Any] | None:
    """Latest market_data row for `symbol` on or before a date, not earlier than `earliest`."""
    response = (
        supabase.table("market_data")
        .select("*")
        .eq("symbol", symbol)
        .lte("snapshot_date", on_or_before)
        .gte("snapshot_date", earliest)
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def fetch_market_price_series(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    """All market_data rows for `symbol` between start and end, ascending by date."""
    response = (
        supabase.table("market_data")
        .select("snapshot_date,close_price")
        .eq("symbol", symbol)
        .gte("snapshot_date", start)
        .lte("snapshot_date", end)
        .order("snapshot_date", desc=False)
        .execute()
    )
    return response.data or []


def fetch_validatable_claims(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return claims ready for outcome validation: quality-passed (pass OR
    partial — see 2026-07-04 decision log entry), not yet validated, and
    whose target_date has already passed.

    'fail' is the only verdict excluded from validation; it never had
    enough reasoning to be worth checking against outcomes.
    """
    today = pendulum.now("UTC").to_date_string()
    response = (
        supabase.table("claims")
        .select("*")
        .in_("quality_verdict", ["pass", "partial"])
        .or_("verification_status.is.null,verification_status.eq.pending")
        .lt("target_date", today)
        .order("target_date", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_claims_for_expert_by_status(
    expert_id: str, statuses: list[str]
) -> list[dict[str, Any]]:
    """Return an expert's claims whose verification_status is in `statuses`."""
    response = (
        supabase.table("claims")
        .select("*")
        .eq("expert_id", expert_id)
        .in_("verification_status", statuses)
        .execute()
    )
    return response.data or []


def fetch_active_expert_ids() -> list[str]:
    """Return ids of all experts, for the end-of-batch credibility recompute."""
    response = supabase.table("experts").select("id").execute()
    return [row["id"] for row in (response.data or [])]


def fetch_unprocessed_content_pieces_for_strategies(limit: int = 100) -> list[dict[str, Any]]:
    """
    Fetch content pieces not yet run through Track B (strategy extraction),
    where strategies_processed_at is null.

    Independent cursor from fetch_unprocessed_content_pieces (Track A) —
    the two tracks run as separate passes over the same table and must not
    share processed-state columns. Same oldest-first ordering rationale
    (backfill imperative, technical plan §8).
    """
    response = (
        supabase.table("content_pieces")
        .select("*")
        .is_("strategies_processed_at", "null")
        .order("published_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_claims_by_content_piece(content_piece_id: str) -> list[dict[str, Any]]:
    """
    Return {id, entity} for every claim extracted from one content piece —
    used to resolve a strategy's linked_claim_entity (an entity name from
    the LLM) into actual claim UUIDs. Code does this match, not the LLM,
    since the LLM has no visibility into already-persisted claim rows.
    """
    response = (
        supabase.table("claims")
        .select("id,entity")
        .eq("content_piece_id", content_piece_id)
        .execute()
    )
    return response.data or []


def fetch_unfiltered_strategies(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return strategies that have not yet been quality-screened (Filter B).

    A strategy is "unfiltered" when quality_verdict IS NULL. Failed
    strategies (quality_verdict = 'fail') are NOT re-screened, mirroring
    fetch_unfiltered_claims.
    """
    response = (
        supabase.table("strategies")
        .select("*")
        .is_("quality_verdict", "null")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_synthesizable_claims(window_days: int = 90) -> list[dict[str, Any]]:
    """
    Return claims eligible for Layer 6 theme synthesis: quality-passed
    (pass OR partial, same promoted-partial convention as elsewhere),
    long-horizon (>= 12 months — the plan's "long-horizon claims only" bar
    for structural synthesis, stricter than claims.scope's 6-month
    out_of_scope threshold), with a non-null entity to cluster by, and a
    thesis that is STILL LIVE — target_date in the future.

    2026-07-09 change (see decision log): the old filter required
    claimed_at within `window_days` (90), which discarded 69% of
    otherwise-eligible claims — a 3-year thesis stated 4 months ago is
    still live for a long-horizon advisory; recency-of-utterance is a
    trading-system notion. A 3-year claimed_at backstop remains so a
    stale decade-horizon call can't headline fresh themes forever.
    `window_days` is kept in the signature for callers but no longer
    drives the recency cut.
    """
    today = pendulum.now("UTC").to_date_string()
    earliest = pendulum.now("UTC").subtract(years=3).to_iso8601_string()
    response = (
        supabase.table("claims")
        .select("*")
        .in_("quality_verdict", ["pass", "partial"])
        .gte("target_horizon_months", 12)
        .not_.is_("entity", "null")
        .gte("target_date", today)
        .gte("claimed_at", earliest)
        .execute()
    )
    return response.data or []


def fetch_experts_by_ids(expert_ids: list[str]) -> list[dict[str, Any]]:
    """Return expert rows (name, credibility scores) for a set of ids."""
    if not expert_ids:
        return []
    response = supabase.table("experts").select("*").in_("id", expert_ids).execute()
    return response.data or []


def fetch_themes_pending_fundamentals_check(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return active themes not yet run through Layer 7 (fundamentals sanity
    check): status='active', fundamentals_verdict IS NULL, oldest
    synthesized_at first.
    """
    response = (
        supabase.table("themes")
        .select("*")
        .eq("status", "active")
        .is_("fundamentals_verdict", "null")
        .order("synthesized_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_strategies_pending_embedding(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return quality-passed, in-scope strategies not yet embedded into
    Chroma (src/strategies/embeddings.py).
    """
    response = (
        supabase.table("strategies")
        .select("*")
        .in_("quality_verdict", ["pass", "partial"])
        .eq("scope", "in_scope")
        .is_("embedded_at", "null")
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_strategies_by_expert(expert_id: str) -> list[dict[str, Any]]:
    """
    Return one expert's quality-passed, in-scope, expert_extracted
    strategies — input to historical pattern detection
    (src/strategies/historical_patterns.py). source_type is restricted to
    expert_extracted so a pattern is never derived from other patterns or
    from CFA/institutional frameworks.
    """
    response = (
        supabase.table("strategies")
        .select("*")
        .eq("expert_id", expert_id)
        .eq("source_type", "expert_extracted")
        .in_("quality_verdict", ["pass", "partial"])
        .eq("scope", "in_scope")
        .execute()
    )
    return response.data or []


def fetch_existing_framework_source_names() -> set[str]:
    """
    Return every non-null source_name already in the strategies table —
    src/strategies/framework_loader.py's idempotency check (a framework
    file is only loaded once).
    """
    response = (
        supabase.table("strategies")
        .select("source_name")
        .not_.is_("source_name", "null")
        .execute()
    )
    return {row["source_name"] for row in (response.data or []) if row.get("source_name")}


def fetch_themes_ready_for_matching(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return active themes eligible for strategy matching: fundamentals
    pass/downgrade, OR index/macro entities where company fundamentals
    categorically don't apply (not_applicable/insufficient_data doesn't
    block those — founder policy, 2026-07-09 decision log; the entity
    allowlist lives in src/guidance/eligibility.py). reject always
    excludes. Input to src/strategies/matcher.py.
    """
    from src.guidance.eligibility import fundamentals_not_applicable_but_eligible

    response = (
        supabase.table("themes")
        .select("*")
        .eq("status", "active")
        .in_("fundamentals_verdict", ["pass", "downgrade", "not_applicable", "insufficient_data"])
        .limit(limit)
        .execute()
    )
    themes = response.data or []
    return [
        t
        for t in themes
        if t.get("fundamentals_verdict") in ("pass", "downgrade")
        or fundamentals_not_applicable_but_eligible(t)
    ]


def delete_theme_strategy_matches(theme_id: str) -> None:
    """
    Delete all existing match rows for one theme — theme_strategy_matches
    is a recomputed ranking cache (src/strategies/matcher.py), not an
    append-only log; re-matching replaces, never accumulates.
    """
    supabase.table("theme_strategy_matches").delete().eq("theme_id", theme_id).execute()


def fetch_active_users() -> list[dict[str, Any]]:
    """Return all active users — Layer 9 guidance generation's outer loop."""
    response = supabase.table("users").select("*").eq("is_active", True).execute()
    return response.data or []


def fetch_user_by_email(email: str) -> dict[str, Any] | None:
    """Look up a user by email — src/guidance/portfolio_seed.py's idempotency key."""
    response = supabase.table("users").select("*").eq("email", email).limit(1).execute()
    return response.data[0] if response.data else None


def fetch_portfolios_for_user(user_id: str) -> list[dict[str, Any]]:
    """Return every portfolio belonging to one user."""
    response = supabase.table("portfolios").select("*").eq("user_id", user_id).execute()
    return response.data or []


def fetch_portfolio_holdings(portfolio_id: str) -> list[dict[str, Any]]:
    """Return every holding (one row per tax lot) in one portfolio."""
    response = (
        supabase.table("portfolio_holdings").select("*").eq("portfolio_id", portfolio_id).execute()
    )
    return response.data or []


def fetch_active_guidance(theme_id: str, portfolio_id: str) -> list[dict[str, Any]]:
    """
    Return active/executing REAL guidance for one theme+portfolio pair —
    src/guidance/eligibility.py's duplicate-guidance gate.

    is_refusal=False matters: refusal rows also carry status='active', and
    counting them here meant a past refusal permanently blocked the same
    theme from ever generating real guidance once it became eligible
    (2026-07-10 audit finding).
    """
    response = (
        supabase.table("portfolio_guidance")
        .select("id")
        .eq("theme_id", theme_id)
        .eq("portfolio_id", portfolio_id)
        .eq("is_refusal", False)
        .in_("status", ["active", "executing"])
        .execute()
    )
    return response.data or []


def fetch_active_guidance_for_theme(theme_id: str) -> list[dict[str, Any]]:
    """
    Return active/executing REAL guidance across ALL portfolios for one
    theme — the theme-supersession cascade
    (src/synthesis/theme_synthesis.py) uses this to flip any guidance
    built on a theme that's about to be replaced, rather than leaving it
    looking as current as guidance generated an hour ago
    (2026-07-12 finding). is_refusal=False for the same reason as
    fetch_active_guidance above — refusals don't need this treatment.
    """
    response = (
        supabase.table("portfolio_guidance")
        .select("id")
        .eq("theme_id", theme_id)
        .eq("is_refusal", False)
        .in_("status", ["active", "executing"])
        .execute()
    )
    return response.data or []


def fetch_active_refusal(theme_id: str, portfolio_id: str, reason: str) -> list[dict[str, Any]]:
    """
    Return active refusal rows for one theme+portfolio with this exact
    reason — the refusal-dedup gate. The same refusal re-logged every run
    adds nothing to the SEBI record; one row per (theme, portfolio, reason)
    until something changes (2026-07-10 decision log).
    """
    response = (
        supabase.table("portfolio_guidance")
        .select("id")
        .eq("theme_id", theme_id)
        .eq("portfolio_id", portfolio_id)
        .eq("is_refusal", True)
        .eq("refusal_reason", reason)
        .eq("status", "active")
        .execute()
    )
    return response.data or []


def fetch_portfolio_active_guidance(portfolio_id: str) -> list[dict[str, Any]]:
    """
    All active/executing real guidance for a portfolio, with the fields the
    entity-overlap gate needs — a successor theme must not issue a second
    live recommendation on the same holdings the previous theme generation
    already covered.
    """
    response = (
        supabase.table("portfolio_guidance")
        .select("id,theme_id,target_allocation_change,specific_instruments")
        .eq("portfolio_id", portfolio_id)
        .eq("is_refusal", False)
        .in_("status", ["active", "executing"])
        .execute()
    )
    return response.data or []


def fetch_best_strategy_match(theme_id: str) -> dict[str, Any] | None:
    """
    Return the top-ranked (rank=1) strategy matched to one theme, or None
    if the matcher hasn't run / found nothing for this theme.
    """
    match_response = (
        supabase.table("theme_strategy_matches")
        .select("strategy_id")
        .eq("theme_id", theme_id)
        .eq("rank", 1)
        .limit(1)
        .execute()
    )
    if not match_response.data:
        return None
    return fetch_by_id("strategies", match_response.data[0]["strategy_id"])


def fetch_backtestable_strategies(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return strategies ready for Track B backtesting: quality-passed (pass OR
    partial — same promoted-partial convention as claims, 2026-07-04
    decision log), in_scope, and not yet backtested.
    """
    response = (
        supabase.table("strategies")
        .select("*")
        .in_("quality_verdict", ["pass", "partial"])
        .eq("scope", "in_scope")
        .eq("backtest_status", "pending")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []
