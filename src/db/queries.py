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
    Return claims ready for outcome validation: quality-passed, not yet
    validated, and whose target_date has already passed.
    """
    today = pendulum.now("UTC").to_date_string()
    response = (
        supabase.table("claims")
        .select("*")
        .eq("quality_verdict", "pass")
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
