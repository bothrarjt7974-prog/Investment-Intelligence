"""
Seed the experts table with the starting 10 experts.

Run once after setting up the database:
    python -m scripts.seed_experts
"""

from __future__ import annotations

from loguru import logger

from src.db.queries import insert_one, supabase

# Edit this list to set your starting expert universe.
# Replace handles with the actual YouTube handles you want to track.
STARTING_EXPERTS = [
    # --- Indian YouTubers ---
    # {"name": "Ankur Warikoo", "handle": "@warikoo", "source_type": "youtube", "geography": "india"},
    # {"name": "Pranjal Kamra", "handle": "@pranjalkamra", "source_type": "youtube", "geography": "india"},
    # {"name": "CA Rachana Ranade", "handle": "@CARachanaRanade", "source_type": "youtube", "geography": "india"},
    {"name": "Akshat Shrivastava", "handle": "@AkshatZayn", "source_type": "youtube", "geography": "india"},
    # {"name": "Shankar Nath", "handle": "@shankarnath", "source_type": "youtube", "geography": "india"},
    # {"name": "Labour Law Advisor", "handle": "@LabourLawAdvisor", "source_type": "youtube", "geography": "india"},
    # {"name": "Nikhil Kamath", "handle": "@nikhilkamathcio", "source_type": "youtube", "geography": "india"},
    # --- US YouTubers ---
    # {"name": "Graham Stephan", "handle": "@GrahamStephan", "source_type": "youtube", "geography": "us"},
    # {"name": "Andrei Jikh", "handle": "@AndreiJikh", "source_type": "youtube", "geography": "us"},
    # {"name": "The Plain Bagel", "handle": "@ThePlainBagel", "source_type": "youtube", "geography": "us"},
]


def upsert_expert(expert: dict[str, str]) -> None:
    """Insert an expert if they don't already exist."""
    existing = (
        supabase.table("experts")
        .select("id")
        .eq("handle", expert["handle"])
        .eq("source_type", expert["source_type"])
        .limit(1)
        .execute()
    )

    if existing.data:
        logger.info(f"Already exists: {expert['name']}")
        return

    insert_one("experts", expert)
    logger.info(f"Inserted: {expert['name']}")


def main() -> None:
    logger.info(f"Seeding {len(STARTING_EXPERTS)} experts")
    for expert in STARTING_EXPERTS:
        upsert_expert(expert)
    logger.info("Seeding complete")


if __name__ == "__main__":
    main()
