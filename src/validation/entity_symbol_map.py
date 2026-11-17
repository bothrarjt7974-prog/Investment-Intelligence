"""
Canonical entity -> external data source mapping for market data lookups.

Every claim's `entity` field (as normalized in src/extraction/normalization.py)
needs a concrete price series to validate against. This module is the single
place that answers "where do I get data for X" — deterministic, no LLM.

Design principle: never force a bad proxy. If an entity has no free data
source with a real price series, it goes in UNMAPPABLE and claims about it
resolve to verification_status='data_unavailable' rather than being scored
against something that doesn't actually represent the claim.
"""

from __future__ import annotations

from loguru import logger

# Each value is (fetch_symbol, source). fetch_symbol is the external ticker
# passed to yfinance; None means the entity has no free automated feed and is
# backed by a manual CSV in data/manual/ instead (see market_data.load_manual_csv).
ENTITY_SYMBOL_MAP: dict[str, tuple[str | None, str]] = {
    # --- Indian stocks (yfinance, .NS suffix) ---
    # Not exhaustive — this covers the large-caps that show up most often in
    # claims. Anything else uppercase with geography='india' falls through to
    # the .NS fallback rule in resolve_entity() below.
    "RELIANCE": ("RELIANCE.NS", "yfinance"),
    "TCS": ("TCS.NS", "yfinance"),
    "INFY": ("INFY.NS", "yfinance"),
    "HDFCBANK": ("HDFCBANK.NS", "yfinance"),
    "ICICIBANK": ("ICICIBANK.NS", "yfinance"),
    "SBIN": ("SBIN.NS", "yfinance"),
    "ASIANPAINT": ("ASIANPAINT.NS", "yfinance"),
    # Zomato rebranded to Eternal Ltd in mid-2024; ZOMATO.NS is delisted on
    # Yahoo Finance (verified empirically, 404/no-data). ETERNAL.NS is the
    # live successor ticker for the same company.
    "ZOMATO": ("ETERNAL.NS", "yfinance"),
    "HINDUNILVR": ("HINDUNILVR.NS", "yfinance"),
    "BAJFINANCE": ("BAJFINANCE.NS", "yfinance"),
    "KOTAKBANK": ("KOTAKBANK.NS", "yfinance"),
    "LT": ("LT.NS", "yfinance"),
    "MARUTI": ("MARUTI.NS", "yfinance"),
    "TITAN": ("TITAN.NS", "yfinance"),
    "SUNPHARMA": ("SUNPHARMA.NS", "yfinance"),
    "WIPRO": ("WIPRO.NS", "yfinance"),
    "AXISBANK": ("AXISBANK.NS", "yfinance"),
    "ADANIENT": ("ADANIENT.NS", "yfinance"),
    # Tata Motors demerged its commercial and passenger vehicle businesses;
    # TATAMOTORS.NS is delisted on Yahoo Finance (verified empirically,
    # 404/no-data). TMPV.NS (Tata Motors Passenger Vehicles) is the closest
    # live successor — most claims about "Tata Motors" as a stock pick are
    # about the passenger vehicle business.
    "TATAMOTORS": ("TMPV.NS", "yfinance"),
    "ITC": ("ITC.NS", "yfinance"),
    "NTPC": ("NTPC.NS", "yfinance"),
    "POWERGRID": ("POWERGRID.NS", "yfinance"),
    "ONGC": ("ONGC.NS", "yfinance"),
    "COALINDIA": ("COALINDIA.NS", "yfinance"),
    "PAYTM": ("PAYTM.NS", "yfinance"),
    "NYKAA": ("NYKAA.NS", "yfinance"),
    "IRCTC": ("IRCTC.NS", "yfinance"),
    # 2026-07-08 growth batch — tickers real themes/claims/holdings already
    # reference (fundamentals checks were starving on these).
    "BHARTIARTL": ("BHARTIARTL.NS", "yfinance"),
    "DIXON": ("DIXON.NS", "yfinance"),
    "MAXHEALTH": ("MAXHEALTH.NS", "yfinance"),
    "FORTIS": ("FORTIS.NS", "yfinance"),
    "MEDANTA": ("MEDANTA.NS", "yfinance"),
    "KIMS": ("KIMS.NS", "yfinance"),
    "YATHARTH": ("YATHARTH.NS", "yfinance"),
    "NMDC": ("NMDC.NS", "yfinance"),
    "TECHM": ("TECHM.NS", "yfinance"),
    "PERSISTENT": ("PERSISTENT.NS", "yfinance"),
    "MUTHOOTFIN": ("MUTHOOTFIN.NS", "yfinance"),
    "BANKBARODA": ("BANKBARODA.NS", "yfinance"),
    "PNB": ("PNB.NS", "yfinance"),
    "LUPIN": ("LUPIN.NS", "yfinance"),
    "BEL": ("BEL.NS", "yfinance"),
    "MARICO": ("MARICO.NS", "yfinance"),
    "SBILIFE": ("SBILIFE.NS", "yfinance"),
    "JIOFIN": ("JIOFIN.NS", "yfinance"),
    "HEROMOTOCO": ("HEROMOTOCO.NS", "yfinance"),
    "heromotoco": ("HEROMOTOCO.NS", "yfinance"),
    "INDIGO": ("INDIGO.NS", "yfinance"),
    "IOC": ("IOC.NS", "yfinance"),
    "DABUR": ("DABUR.NS", "yfinance"),
    "NESTLEIND": ("NESTLEIND.NS", "yfinance"),
    # Equitas Small Finance Bank post-merger NSE symbol
    "EQUITAS": ("EQUITASBNK.NS", "yfinance"),
    "KARURVYSYA": ("KARURVYSYA.NS", "yfinance"),
    "HARIOMPIPE": ("HARIOMPIPE.NS", "yfinance"),
    "JSL": ("JSL.NS", "yfinance"),
    "SOLARINDS": ("SOLARINDS.NS", "yfinance"),
    "ERIS": ("ERIS.NS", "yfinance"),
    "RADICO": ("RADICO.NS", "yfinance"),
    "JKCEMENT": ("JKCEMENT.NS", "yfinance"),
    "BLUESTARCO": ("BLUESTARCO.NS", "yfinance"),
    "VMM": ("VMM.NS", "yfinance"),
    "CHOLAFIN": ("CHOLAFIN.NS", "yfinance"),
    "BAJAJ-AUTO": ("BAJAJ-AUTO.NS", "yfinance"),
    "M&M": ("M&M.NS", "yfinance"),
    "HIMADRI": ("HIMADRI.NS", "yfinance"),
    "VISHNU": ("VISHNU.NS", "yfinance"),
    # Nagarro SE trades on Frankfurt, not NSE — Indian-founded, German-listed
    "NAGARRO": ("NA9.DE", "yfinance"),
    "SWIGGY": ("SWIGGY.NS", "yfinance"),
    "BSE": ("BSE.NS", "yfinance"),
    "GREENLAM": ("GREENLAM.NS", "yfinance"),
    "CGPOWER": ("CGPOWER.NS", "yfinance"),
    "PATELENG": ("PATELENG.NS", "yfinance"),
    # 2026-10-05 batch — entities stuck at data_unavailable in real claims.
    # Lowercase keys are the spellings extraction actually produced (same
    # convention as "heromotoco" above).
    "PNBHOUSING": ("PNBHOUSING.NS", "yfinance"),
    "pnb_hf": ("PNBHOUSING.NS", "yfinance"),
    "IDFCFIRSTB": ("IDFCFIRSTB.NS", "yfinance"),
    "idfc_first_bank": ("IDFCFIRSTB.NS", "yfinance"),
    "AUBANK": ("AUBANK.NS", "yfinance"),
    "au_small_finance_bank": ("AUBANK.NS", "yfinance"),
    "bajfinance": ("BAJFINANCE.NS", "yfinance"),
    # LIC's NSE symbol is LICI, not LIC.
    "LIC": ("LICI.NS", "yfinance"),
    "HDFCAMC": ("HDFCAMC.NS", "yfinance"),
    "hdfc amc": ("HDFCAMC.NS", "yfinance"),
    # --- US stocks (yfinance, bare ticker) ---
    "AAPL": ("AAPL", "yfinance"),
    "MSFT": ("MSFT", "yfinance"),
    "GOOGL": ("GOOGL", "yfinance"),
    "AMZN": ("AMZN", "yfinance"),
    "META": ("META", "yfinance"),
    "NVDA": ("NVDA", "yfinance"),
    "TSLA": ("TSLA", "yfinance"),
    "NFLX": ("NFLX", "yfinance"),
    "AMD": ("AMD", "yfinance"),
    "TSM": ("TSM", "yfinance"),
    "MSTR": ("MSTR", "yfinance"),
    "JPM": ("JPM", "yfinance"),
    "XOM": ("XOM", "yfinance"),
    "CVX": ("CVX", "yfinance"),
    "COST": ("COST", "yfinance"),
    "FDX": ("FDX", "yfinance"),
    "MCDONALDS": ("MCD", "yfinance"),
    "MCD": ("MCD", "yfinance"),
    "KFC": ("YUM", "yfinance"),  # KFC is a Yum! Brands chain, not separately listed
    # Samsung Electronics, Korea Exchange
    "SAMSUNG": ("005930.KS", "yfinance"),
    # --- Crypto (yfinance) ---
    "BTC": ("BTC-USD", "yfinance"),
    "ETH": ("ETH-USD", "yfinance"),
    "SOL": ("SOL-USD", "yfinance"),
    # --- Indian indices (yfinance) ---
    "nifty50": ("^NSEI", "yfinance"),
    "nifty500": ("^CRSLDX", "yfinance"),
    "nifty_midcap150": ("NIFTYMIDCAP150.NS", "yfinance"),
    "nifty_smallcap250": ("NIFTYSMLCAP250.NS", "yfinance"),
    # --- US / global indices (yfinance) ---
    "nasdaq": ("^IXIC", "yfinance"),
    "sp500": ("^GSPC", "yfinance"),
    "qqq": ("QQQ", "yfinance"),
    "dow": ("^DJI", "yfinance"),
    # --- Indian sector proxies (yfinance NSE sector indices) ---
    "indian_it": ("^CNXIT", "yfinance"),
    "indian_banking": ("^NSEBANK", "yfinance"),
    "indian_pharma": ("^CNXPHARMA", "yfinance"),
    "indian_auto": ("^CNXAUTO", "yfinance"),
    "indian_fmcg": ("^CNXFMCG", "yfinance"),
    "indian_metals": ("^CNXMETAL", "yfinance"),
    "indian_realestate": ("^CNXREALTY", "yfinance"),
    "indian_energy": ("^CNXENERGY", "yfinance"),
    "indian_midcap": ("NIFTYMIDCAP150.NS", "yfinance"),
    "indian_smallcap": ("NIFTYSMLCAP250.NS", "yfinance"),
    "indian_equity_market": ("^NSEI", "yfinance"),
    # "broad_equity" is strategy_extraction_v2's catch-all entity_class for
    # general (not sector-specific) allocation strategies — "invest in
    # individual stocks during a bull run" with no named sector. Mapped to
    # Nifty 50 as an India-MVP default: a fair proxy (broad Indian equity
    # exposure ≈ Nifty), not a forced one, given the current expert roster
    # is India-focused (2026-07-07 decision log). Revisit once a US-focused
    # expert's broad_equity strategies need a different default.
    "broad_equity": ("^NSEI", "yfinance"),
    # --- US sector proxies (yfinance sector ETFs — free, liquid, long history) ---
    "us_tech": ("XLK", "yfinance"),
    # Hierarchy parent for us_tech/global_ai/mega-cap AI names (see
    # src/synthesis/entity_hierarchy.py) — same proxy as us_tech.
    "us_tech_ai": ("XLK", "yfinance"),
    "us_software": ("IGV", "yfinance"),
    "us_semiconductors": ("SOXX", "yfinance"),
    "us_banking": ("KBE", "yfinance"),
    "us_healthcare": ("XLV", "yfinance"),
    "us_energy": ("XLE", "yfinance"),
    "us_defense": ("ITA", "yfinance"),
    "us_consumer": ("XLY", "yfinance"),
    "us_equities": ("^GSPC", "yfinance"),
    "us_smallcap": ("IWM", "yfinance"),
    # --- Thematic proxies (imperfect by nature — judgment calls, revisit) ---
    # BOTZ (robotics & AI ETF) is the closest free, liquid proxy for "AI" as a
    # theme. It over-indexes on robotics/industrial names vs. pure-play LLM
    # companies, so treat correctness on this one with extra skepticism.
    "global_ai": ("BOTZ", "yfinance"),
    # Using BTC as a stand-in for "crypto sector" claims: it's the dominant
    # asset by market cap and most sector-wide claims ("crypto rallies/dumps")
    # move with BTC. This will misrepresent alt-season-specific claims.
    "crypto_sector": ("BTC-USD", "yfinance"),
    # --- Currencies (yfinance) ---
    "inr_usd": ("INR=X", "yfinance"),
    "jpy_usd": ("JPY=X", "yfinance"),
    "dxy": ("DX-Y.NYB", "yfinance"),
    "eur_usd": ("EURUSD=X", "yfinance"),
    # --- Commodities (yfinance) ---
    "gold": ("GC=F", "yfinance"),
    "silver": ("SI=F", "yfinance"),
    "crude_oil": ("CL=F", "yfinance"),
    # --- Manual source (no free API — small CSV tables in data/manual/) ---
    # fetch_symbol is None: these are never pulled from yfinance. Rows come in
    # via market_data.load_manual_csv() and are looked up by this same key as
    # `symbol` in market_data. FRED has a free API for fed_rate/us_inflation
    # but requires an API-key signup — TODO: swap to FRED once a key exists,
    # not required for this build (manual CSV is ~30 rows, good enough now).
    "rbi_rate": (None, "manual"),
    "fed_rate": (None, "manual"),
    "india_inflation": (None, "manual"),
    "us_inflation": (None, "manual"),
    "india_gdp": (None, "manual"),
    "us_gdp": (None, "manual"),
    # --- 2026-11-18 rescue batch: entities stuck at data_unavailable ---
    # VEDANTA.NS does not exist; the correct NSE symbol for Vedanta Ltd is VEDL.NS.
    "VEDANTA": ("VEDL.NS", "yfinance"),
    # indian_airlines: IndiGo (INDIGO.NS) is ~60% domestic aviation by seats;
    # sector-wide claims will be proxied here — flag correctness with skepticism.
    "indian_airlines": ("INDIGO.NS", "yfinance"),
}

# Abstract macro/geopolitical entities that claims reference but that have no
# price series at all — not stocks, not indices, not rates. Documented here so
# they resolve to data_unavailable deliberately, not by silent fallthrough.
UNMAPPABLE: frozenset[str] = frozenset(
    {
        "us_china_tensions",
        "global_liquidity",
        "geopolitical_risk",
        "trade_war",
        "regulatory_risk",
        "market_sentiment",
        "global_supply_chain",
        # 2026-10-05: policy/input-cost entities seen in real claims — no
        # tradeable series that represents them honestly.
        "india_corporate_tax",
        "polypropylene_prices",
        # The Tata *group* (a conglomerate, ~25 listed companies) — no single
        # series. Specific companies (TCS, TATAMOTORS) are mapped individually.
        "TATA",
        # 2026-11-18: sector concept with no clean single-series proxy.
        "indian_consumer_tech",
        # Small/likely-delisted company; DYNACAB.NS returns 404. If a real
        # NSE-listed company surfaces with this name, add to ENTITY_SYMBOL_MAP.
        "DYNACAB",
    }
)

# Real companies experts talk about that are NOT publicly traded — no price
# series, no fundamentals filings. Fundamentals checks short-circuit these to
# 'not_applicable' instead of hunting for a ticker that doesn't exist.
# Revisit each on IPO (several here have expected listings).
PRIVATE_ENTITIES: frozenset[str] = frozenset(
    {
        "OPENAI",
        "ANTHROPIC",
        "SPACEX",
        "SPCX",  # alternate extraction spelling of SpaceX
        "ZEPTO",
        "NSE",  # NSE India itself — unlisted as of 2026
        "NPS",  # pension product, not a company
        "PDVSA",  # Venezuelan state oil company — no listed equity
        "ZERODHA",  # Indian broker, unlisted as of 2026
    }
)


def resolve_entity(entity: str | None, geography: str | None) -> tuple[str, str] | None:
    """
    Resolve a claim's canonical entity name to (fetch_symbol, source).

    Lookup order:
      1. Exact match in ENTITY_SYMBOL_MAP (case-sensitive, as stored).
      2. Explicit UNMAPPABLE or PRIVATE_ENTITIES membership -> None
         (documented, not silent — an unlisted company has no price series).
      3. Geography-aware ticker fallback: an uppercase-looking ticker not in
         the map gets .NS appended for geography='india', or used bare for
         geography='us'. Fallback firing is logged so we can promote it to
         an explicit map entry later.
      4. Otherwise -> None (unmappable, caller should mark data_unavailable).

    fetch_symbol is None for manual-source entities (e.g. 'rbi_rate') — the
    caller still gets ('manual', symbol) semantics via the source string, but
    there's no yfinance ticker to fetch; price_on() etc. read from market_data
    rows already loaded by load_manual_csv().
    """
    if not entity:
        return None

    if entity in UNMAPPABLE or entity in PRIVATE_ENTITIES:
        return None

    if entity in ENTITY_SYMBOL_MAP:
        fetch_symbol, source = ENTITY_SYMBOL_MAP[entity]
        if fetch_symbol is None:
            # Manual entities resolve to (symbol, 'manual') — market_data
            # rows for these are keyed by the canonical entity name itself.
            return (entity, source)
        return (fetch_symbol, source)

    if not entity.isupper() or not entity.replace(".", "").isalnum():
        return None

    if geography == "india":
        logger.warning(
            f"Entity '{entity}' not in ENTITY_SYMBOL_MAP — falling back to "
            f"'{entity}.NS'. Add an explicit entry if this is a real, "
            f"recurring ticker."
        )
        return (f"{entity}.NS", "yfinance")

    if geography == "us":
        logger.warning(
            f"Entity '{entity}' not in ENTITY_SYMBOL_MAP — falling back to "
            f"bare ticker '{entity}'. Add an explicit entry if this is a "
            f"real, recurring ticker."
        )
        return (entity, "yfinance")

    return None
