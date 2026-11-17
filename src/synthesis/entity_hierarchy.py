"""
Entity hierarchy for theme clustering (Layer 6).

Exact-entity clustering fragments one economic story into several
under-counted clusters: claims on MSFT, us_software, global_ai and
us_tech are the same AI-infrastructure thesis, but as four separate
clusters none of them accumulates enough independent experts to cross
the medium-conviction bar. This map folds children into a parent cluster
key BEFORE clustering, so convergence is counted honestly at the level
where the thesis actually lives.

Deliberately conservative: only merge entities that are unambiguously
the same story. Over-merging would manufacture fake convergence — worse
than fragmentation, because conviction gates real guidance. Broad Indian
index buckets (nifty50 / nifty500 / smallcap / midcap) stay separate on
purpose: they are distinct risk buckets experts genuinely disagree
across. Deterministic code, never LLM (an LLM merge decision would be
unauditable). Grow this map as new entities appear in claims; record
additions in the decision log.
"""

from __future__ import annotations

# child entity -> parent cluster key. Parents don't need an entry —
# resolve() returns unknown/parent keys unchanged.
ENTITY_HIERARCHY: dict[str, str] = {
    # --- US tech / AI infrastructure complex ---
    "us_tech": "us_tech_ai",
    "global_ai": "us_tech_ai",
    "us_software": "us_tech_ai",
    "us_semiconductors": "us_tech_ai",
    "nasdaq": "us_tech_ai",
    "qqq": "us_tech_ai",  # Nasdaq-100 tracker — same story as nasdaq
    "NVDA": "us_tech_ai",
    "MSFT": "us_tech_ai",
    "GOOGL": "us_tech_ai",
    "META": "us_tech_ai",
    "AMZN": "us_tech_ai",
    "AAPL": "us_tech_ai",
    # --- Indian IT services ---
    "WIPRO": "indian_it",
    "PERSISTENT": "indian_it",
    "TCS": "indian_it",
    "INFY": "indian_it",
    "TECHM": "indian_it",
    "HCLTECH": "indian_it",
    # --- Indian banking ---
    "HDFCBANK": "indian_banking",
    "ICICIBANK": "indian_banking",
    "SBIN": "indian_banking",
    "KOTAKBANK": "indian_banking",
    "AXISBANK": "indian_banking",
    "PNB": "indian_banking",
    "BANKBARODA": "indian_banking",
    "EQUITAS": "indian_banking",
    "KARURVYSYA": "indian_banking",
    # --- Indian consumer tech / new-age platforms ---
    "ZOMATO": "indian_consumer_tech",
    "NYKAA": "indian_consumer_tech",
    "ZEPTO": "indian_consumer_tech",
    "SWIGGY": "indian_consumer_tech",
    "PAYTM": "indian_consumer_tech",
    # --- Crypto ---
    "BTC": "crypto_sector",
    "ETH": "crypto_sector",
    "SOL": "crypto_sector",
    # --- Spelling canonicalization (same company, two extractions) ---
    "SPCX": "SPACEX",
}


def resolve_entity_cluster(entity: str) -> str:
    """Parent cluster key for an entity; unknown entities map to themselves."""
    return ENTITY_HIERARCHY.get(entity, entity)
