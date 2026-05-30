"""
Database access layer.

All database access in this codebase routes through this module.
Uses Supabase Python client for higher-level operations,
SQLAlchemy for complex queries when needed.
"""

from __future__ import annotations

from typing import Any

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
    """Fetch unprocessed content pieces where processed_at is null."""
    response = (
        supabase.table("content_pieces")
        .select("*")
        .is_("processed_at", "null")
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
