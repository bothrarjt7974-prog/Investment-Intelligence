"""
Simple migration runner.

What this file does:
- Looks for SQL files in the migrations/ folder.
- Runs each file in order.
- Keeps track of what already ran so it does not repeat work.

How to run it:
    python -m scripts.run_migrations
"""

from __future__ import annotations

from pathlib import Path

import psycopg2
from loguru import logger

from src.config.settings import settings

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def ensure_migrations_table(cur: psycopg2.extensions.cursor) -> None:
    """Create the tracking table used to remember which SQL files were already run."""
    # If the table does not exist yet, create it once.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def get_applied(cur: psycopg2.extensions.cursor) -> set[str]:
    """Read the list of migration files that have already been applied."""
    # Ask the database which migration names were already saved.
    cur.execute("SELECT filename FROM schema_migrations;")
    return {row[0] for row in cur.fetchall()}


def apply_migration(cur: psycopg2.extensions.cursor, path: Path) -> None:
    """Run one SQL file and then mark it as done."""
    # Read the SQL file and execute it.
    logger.info(f"Applying: {path.name}")
    sql = path.read_text(encoding="utf-8")
    cur.execute(sql)
    cur.execute(
        "INSERT INTO schema_migrations (filename) VALUES (%s);",
        (path.name,),
    )
    logger.info(f"Applied: {path.name}")


def main() -> None:
    # Start the migration process.
    if not MIGRATIONS_DIR.exists():
        logger.error(f"No migrations directory: {MIGRATIONS_DIR}")
        return

    # Find all SQL migration files and sort them so they run in order.
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        logger.info("No migrations to run")
        return

    # Open a database connection to the Postgres server.
    conn = psycopg2.connect(settings.database_url)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            # Make sure the tracking table exists before anything else.
            ensure_migrations_table(cur)
            conn.commit()

            # Check which migrations are already done.
            applied = get_applied(cur)
            pending = [f for f in files if f.name not in applied]

            if not pending:
                logger.info("All migrations already applied")
                return

            logger.info(f"Found {len(pending)} pending migration(s)")
            # Run only the SQL files that have not been applied yet.
            for path in pending:
                apply_migration(cur, path)
                conn.commit()

            logger.info("All migrations applied successfully")

    except Exception:
        logger.exception("Migration failed — rolling back")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
