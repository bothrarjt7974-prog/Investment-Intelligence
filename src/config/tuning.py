"""
Central registry of every tunable quality parameter in the pipeline.

WHY this exists: quality thresholds were scattered as hardcoded constants
across 15 modules, which made it impossible to see — let alone adjust —
how strict each layer is without reading source code. This module is the
single catalog: each parameter carries its default, safe range, and a
plain-language explanation of what turning it up or down does downstream.

How overrides work:
  - Defaults live here in code (they mirror the founder-reviewed values
    that shipped in each module).
  - Admin overrides are stored in config/tuning_overrides.json at the
    repo root (written by the Streamlit "Quality Tuning" page).
  - Pipeline modules read values via `param("<key>")` at import time, so
    an override takes effect the next time a pipeline job starts — not
    mid-run. This is deliberate: a batch job should run under one
    consistent configuration.

Values are clamped to [min_value, max_value] on read, so a hand-edited
overrides file can never push a threshold into a nonsensical range.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

OVERRIDES_PATH = Path(__file__).resolve().parents[2] / "config" / "tuning_overrides.json"


@dataclass(frozen=True)
class TuningParameter:
    """One adjustable quality knob, with everything an admin needs to reason about it."""

    key: str  # dotted id, e.g. "quality.out_of_scope_horizon_months"
    layer: str  # which pipeline layer owns it (drives dashboard grouping)
    title: str  # short human name
    default: float | int
    min_value: float | int
    max_value: float | int
    step: float | int
    unit: str  # "%", "months", "chars", "count", "score", "ratio", "fraction"
    plain: str  # layman explanation of what this controls
    raise_effect: str  # what happens downstream if you increase it
    lower_effect: str  # what happens downstream if you decrease it
    internal: bool = field(default=False)  # proprietary scoring logic — never expose outside admin


# ---------------------------------------------------------------------------
# The registry. Order matters only for dashboard display (grouped by layer).
# Defaults MUST mirror the values the owning modules shipped with — changing
# a default is a decision-log event, same as changing the module constant was.
# ---------------------------------------------------------------------------

_P = TuningParameter

PARAMETERS: dict[str, TuningParameter] = {
    p.key: p
    for p in [
        # --- Layer 2: Extraction -------------------------------------------
        _P(
            key="extraction.min_transcript_chars",
            layer="Layer 2 · Extraction",
            title="Minimum transcript length",
            default=500, min_value=100, max_value=5000, step=50, unit="chars",
            plain=(
                "A video transcript shorter than this is considered too thin to "
                "contain a real investment claim (e.g. a 20-second Short). It is "
                "skipped without spending any Claude API money."
            ),
            raise_effect="Fewer, longer videos get processed — saves API cost, may miss short but dense clips.",
            lower_effect="More short clips get processed — more API spend, more junk extractions.",
        ),
        # --- Layer 3: Normalization ----------------------------------------
        _P(
            key="normalization.claim_similarity_threshold",
            layer="Layer 3 · Normalization",
            title="Claim duplicate-detection sensitivity",
            default=0.50, min_value=0.10, max_value=0.95, step=0.05, unit="fraction",
            plain=(
                "When an expert repeats the same claim twice in one video, we keep "
                "only one copy. Two claims whose text overlaps by at least this "
                "fraction are treated as the same claim."
            ),
            raise_effect="Stricter matching — fewer merges, more near-duplicate claims survive.",
            lower_effect="Looser matching — more merges, risk of collapsing genuinely different claims into one.",
        ),
        _P(
            key="normalization.strategy_similarity_threshold",
            layer="Layer 3 · Normalization",
            title="Strategy duplicate-detection sensitivity",
            default=0.50, min_value=0.10, max_value=0.95, step=0.05, unit="fraction",
            plain="Same duplicate-merging idea as for claims, applied to extracted strategies.",
            raise_effect="Fewer strategy merges — more near-duplicates in the library.",
            lower_effect="More merges — risk of collapsing distinct strategies together.",
        ),
        # --- Layer 4: Quality filters --------------------------------------
        _P(
            key="quality.out_of_scope_horizon_months",
            layer="Layer 4 · Quality filters",
            title="Trading-call cutoff horizon",
            # 7, not 6, since the 2026-07-09 audit: the check is strict-less-than,
            # and "short timeframe" claims land on exactly 6 months (the bucket
            # median), so at 6 every short-term trading call (support levels,
            # near-term currency moves) stamped in_scope. 7 puts the entire
            # short bucket out of scope for guidance, as the product line
            # ("we are investors") always intended. See decision log.
            default=7, min_value=1, max_value=24, step=1, unit="months",
            plain=(
                "We are investors, not traders. Any claim that resolves faster than "
                "this many months is a trading call: it still counts toward the "
                "expert's track record, but it can never drive portfolio guidance."
            ),
            raise_effect="Stricter investor discipline — more claims excluded from guidance.",
            lower_effect="Shorter-term claims become guidance-eligible — drifts toward trading territory.",
        ),
        # --- Layer 5: Validation (Track A — claims) ------------------------
        _P(
            key="validation.directional_move_threshold_pct",
            layer="Layer 5 · Validation",
            title="Minimum move to call a direction",
            default=2.0, min_value=0.5, max_value=10.0, step=0.5, unit="%",
            plain=(
                "When checking whether a bullish/bearish call came true, the price "
                "must have moved at least this much in the claimed direction. Smaller "
                "moves are treated as noise, and the claim is scored 'partial'."
            ),
            raise_effect="Harder to be scored correct — expert credibility scores drop across the board.",
            lower_effect="Tiny moves count as wins — credibility scores inflate.",
        ),
        _P(
            key="validation.neutral_correct_band_pct",
            layer="Layer 5 · Validation",
            title="'Stays flat' correct band",
            default=5.0, min_value=1.0, max_value=15.0, step=0.5, unit="%",
            plain=(
                "For a claim that says a price will stay flat/range-bound: if the "
                "actual move stayed within ± this band, the claim is fully correct."
            ),
            raise_effect="Easier for neutral claims to be correct.",
            lower_effect="Stricter — neutral claims fail on modest drift.",
        ),
        _P(
            key="validation.neutral_partial_band_pct",
            layer="Layer 5 · Validation",
            title="'Stays flat' partial band",
            default=10.0, min_value=2.0, max_value=25.0, step=0.5, unit="%",
            plain=(
                "The wider band around a 'stays flat' claim: a move inside this (but "
                "outside the correct band) scores 'partial' instead of 'incorrect'."
            ),
            raise_effect="More neutral claims land on partial instead of incorrect.",
            lower_effect="More neutral claims scored flatly wrong.",
        ),
        _P(
            key="validation.price_target_partial_band",
            layer="Layer 5 · Validation",
            title="Price-target near-miss band",
            default=0.05, min_value=0.01, max_value=0.20, step=0.01, unit="fraction",
            plain=(
                "If an expert named a specific price target and the actual price got "
                "within this fraction of it (default 5%), the claim scores 'partial' "
                "rather than 'incorrect' — a near miss."
            ),
            raise_effect="More generous near-miss credit for price targets.",
            lower_effect="Price targets must be nearly exact to earn partial credit.",
        ),
        # --- Layer 5: Validation (Track B — strategy backtests) ------------
        _P(
            key="backtest.min_trigger_windows",
            layer="Layer 5 · Backtesting",
            title="Minimum historical episodes",
            default=3, min_value=1, max_value=10, step=1, unit="count",
            plain=(
                "A strategy is only backtested if its trigger fired at least this "
                "many separate times in history. One lucky episode is an anecdote, "
                "not evidence."
            ),
            raise_effect="Higher evidence bar — fewer strategies earn a backtest score.",
            lower_effect="Thin evidence accepted — backtest scores less trustworthy.",
        ),
        _P(
            key="backtest.default_horizon_months",
            layer="Layer 5 · Backtesting",
            title="Default backtest holding period",
            default=12, min_value=3, max_value=60, step=1, unit="months",
            plain=(
                "When a strategy doesn't state its own holding period, we measure its "
                "historical performance over this many months after each trigger."
            ),
            raise_effect="Judges strategies on longer holding periods.",
            lower_effect="Judges on shorter periods — drifts toward trading-style evaluation.",
        ),
        _P(
            key="triggers.default_drawdown_threshold_pct",
            layer="Layer 5 · Backtesting",
            title="Default 'market correction' definition",
            default=-20.0, min_value=-50.0, max_value=-5.0, step=1.0, unit="%",
            plain=(
                "When a strategy says 'buy the dip' without naming a number, this is "
                "what counts as a dip: a fall of this size from the recent peak."
            ),
            raise_effect="(toward 0) Milder falls count as corrections — triggers fire more often.",
            lower_effect="(more negative) Only severe crashes count — triggers fire rarely.",
        ),
        _P(
            key="triggers.currency_move_threshold_pct",
            layer="Layer 5 · Backtesting",
            title="Significant currency move",
            default=5.0, min_value=1.0, max_value=20.0, step=0.5, unit="%",
            plain="How big a currency move (e.g. INR vs USD) must be, within the move window, to count as a trigger event.",
            raise_effect="Currency triggers fire less often.",
            lower_effect="Currency triggers fire on routine fluctuation.",
        ),
        _P(
            key="triggers.currency_move_window_days",
            layer="Layer 5 · Backtesting",
            title="Currency move window",
            default=90, min_value=10, max_value=365, step=5, unit="days",
            plain="The lookback window over which the currency move above is measured.",
            raise_effect="Slower, larger trends qualify as triggers.",
            lower_effect="Only sharp, fast moves qualify.",
        ),
        _P(
            key="triggers.episode_gap_days",
            layer="Layer 5 · Backtesting",
            title="Gap between trigger episodes",
            default=90, min_value=10, max_value=365, step=5, unit="days",
            plain=(
                "Two trigger firings closer together than this are counted as one "
                "episode, so a single long crash isn't double-counted as many wins."
            ),
            raise_effect="Fewer, more distinct episodes counted.",
            lower_effect="Clustered firings count separately — inflates episode counts.",
        ),
        # --- Layer 5: Credibility scoring (PROPRIETARY) --------------------
        _P(
            key="credibility.default_score",
            layer="Layer 5 · Credibility (internal)",
            title="Neutral starting credibility",
            default=50.0, min_value=0.0, max_value=100.0, step=1.0, unit="score",
            plain=(
                "Every expert starts here (out of 100) until enough of their claims "
                "have been validated against real outcomes to earn a track record."
            ),
            raise_effect="New/unproven experts carry more weight in themes.",
            lower_effect="Unproven experts carry less weight until validated.",
            internal=True,
        ),
        _P(
            key="credibility.shrinkage_k",
            layer="Layer 5 · Credibility (internal)",
            title="Track-record trust speed",
            default=20, min_value=1, max_value=100, step=1, unit="count",
            plain=(
                "How many validated claims an expert needs before we trust their "
                "measured hit rate about as much as the neutral default. Below this, "
                "their score stays pulled toward 50."
            ),
            raise_effect="Slower to trust a hot streak — scores move cautiously.",
            lower_effect="A few lucky calls can swing an expert's score quickly.",
            internal=True,
        ),
        _P(
            key="credibility.short_horizon_max_months",
            layer="Layer 5 · Credibility (internal)",
            title="Short-horizon bucket boundary",
            default=12, min_value=3, max_value=24, step=1, unit="months",
            plain="Claims resolving faster than this are scored in the expert's 'short horizon' track record bucket.",
            raise_effect="More claims count as short-horizon.",
            lower_effect="Fewer claims count as short-horizon.",
            internal=True,
        ),
        _P(
            key="credibility.long_horizon_max_months",
            layer="Layer 5 · Credibility (internal)",
            title="Long-horizon bucket boundary",
            default=36, min_value=12, max_value=120, step=1, unit="months",
            plain="Claims resolving between the short boundary and this go in the 'long' bucket; beyond it, 'structural'.",
            raise_effect="Long bucket absorbs more of the very-long claims.",
            lower_effect="More claims classified as structural.",
            internal=True,
        ),
        # --- Layer 6: Theme synthesis (PROPRIETARY thresholds) -------------
        _P(
            key="themes.high_min_signal",
            layer="Layer 6 · Theme synthesis (internal)",
            title="HIGH conviction: independent voices needed",
            default=3.0, min_value=1.0, max_value=10.0, step=0.5, unit="count",
            plain=(
                "How many effectively-independent experts must agree before a theme "
                "can be HIGH conviction. Correlated experts count as half a voice."
            ),
            raise_effect="HIGH conviction becomes rarer — fewer strong guidance events.",
            lower_effect="Easier HIGH conviction — riskier guidance from thinner agreement.",
            internal=True,
        ),
        _P(
            key="themes.high_min_credibility",
            layer="Layer 6 · Theme synthesis (internal)",
            title="HIGH conviction: credibility floor",
            default=65.0, min_value=50.0, max_value=95.0, step=1.0, unit="score",
            plain="The credibility-weighted score a theme's experts must average for HIGH conviction.",
            raise_effect="Only well-proven experts can produce HIGH themes.",
            lower_effect="Unproven experts can push themes to HIGH.",
            internal=True,
        ),
        _P(
            key="themes.medium_min_signal",
            layer="Layer 6 · Theme synthesis (internal)",
            title="MEDIUM conviction: independent voices needed",
            default=2.0, min_value=1.0, max_value=10.0, step=0.5, unit="count",
            plain=(
                "The bar for MEDIUM conviction — the minimum level at which a theme "
                "becomes eligible for guidance at all."
            ),
            raise_effect="Fewer themes reach guidance eligibility.",
            lower_effect="Single-ish voices can reach guidance — weakens the convergence principle.",
            internal=True,
        ),
        _P(
            key="themes.medium_min_credibility",
            layer="Layer 6 · Theme synthesis (internal)",
            title="MEDIUM conviction: credibility floor",
            default=50.0, min_value=30.0, max_value=90.0, step=1.0, unit="score",
            plain="Credibility floor for MEDIUM conviction (50 = the neutral default every expert starts at).",
            raise_effect="Guidance waits for experts with proven track records.",
            lower_effect="Below-neutral experts can still drive guidance.",
            internal=True,
        ),
        _P(
            key="themes.direction_split_max_minority",
            layer="Layer 6 · Theme synthesis (internal)",
            title="Directional-disagreement tolerance",
            default=0.25, min_value=0.0, max_value=0.5, step=0.05, unit="fraction",
            plain=(
                "Experts in a theme must broadly agree on direction. If the "
                "minority view (e.g. bearish claims in a mostly-bullish cluster) "
                "exceeds this share of the directional claims, the theme is "
                "'contested' and its conviction is capped at LOW — it can never "
                "drive guidance until the disagreement resolves."
            ),
            raise_effect="More internal disagreement tolerated before a theme is blocked.",
            lower_effect="Even small dissent caps the theme — guidance requires near-unanimity.",
            internal=True,
        ),
        _P(
            key="themes.correlated_expert_weight",
            layer="Layer 6 · Theme synthesis (internal)",
            title="Correlated-expert discount",
            default=0.5, min_value=0.1, max_value=1.0, step=0.05, unit="fraction",
            plain=(
                "When two experts clearly share the same reasoning (one echoes the "
                "other), each counts as this fraction of an independent voice, so an "
                "echo chamber can't fake broad agreement."
            ),
            raise_effect="Echoing experts count almost as full voices.",
            lower_effect="Echoing experts barely count — themes need truly independent sources.",
            internal=True,
        ),
        # --- Layer 7: Fundamentals -----------------------------------------
        _P(
            key="fundamentals.max_debt_to_equity",
            layer="Layer 7 · Fundamentals",
            title="Balance-sheet veto: max debt/equity",
            default=200.0, min_value=50.0, max_value=500.0, step=10.0, unit="ratio ×100",
            plain=(
                "Hard safety veto: if a company carries more than this much debt "
                "relative to shareholder equity (200 = 2× equity), no theme about it "
                "can produce guidance, no matter how many experts love it."
            ),
            raise_effect="More leveraged companies survive the veto.",
            lower_effect="Stricter safety — more themes rejected on debt.",
        ),
        _P(
            key="fundamentals.min_current_ratio",
            layer="Layer 7 · Fundamentals",
            title="Balance-sheet veto: min current ratio",
            default=1.0, min_value=0.5, max_value=3.0, step=0.1, unit="ratio",
            plain=(
                "Second half of the safety veto: the company must have at least this "
                "much in near-term assets per rupee of near-term bills (1.0 = can "
                "just cover them)."
            ),
            raise_effect="Stricter liquidity requirement — more rejections.",
            lower_effect="Cash-tight companies survive the veto.",
        ),
        _P(
            key="fundamentals.sector_pe_red_flag_ratio",
            layer="Layer 7 · Fundamentals",
            title="Overvaluation red flag vs sector",
            default=1.5, min_value=1.1, max_value=3.0, step=0.1, unit="ratio",
            plain=(
                "If a stock is priced at more than this multiple of its sector's "
                "average price-to-earnings (1.5 = 50% more expensive than peers), "
                "the theme gets flagged as expensive and downgraded."
            ),
            raise_effect="Fewer overvaluation downgrades — pricier stocks pass.",
            lower_effect="More themes downgraded as too expensive.",
        ),
        # --- Layer 8: Strategy matching -------------------------------------
        _P(
            key="matcher.candidate_pool_size",
            layer="Layer 8 · Strategy matching",
            title="Shortlist size from library",
            default=20, min_value=5, max_value=100, step=5, unit="count",
            plain="How many candidate strategies the semantic search pulls from the library before scoring and ranking them.",
            raise_effect="Wider search — better recall, slightly slower.",
            lower_effect="Narrower search — may miss a good but oddly-worded strategy.",
        ),
        _P(
            key="matcher.semantic_weight",
            layer="Layer 8 · Strategy matching",
            title="Ranking weight: meaning match",
            default=0.5, min_value=0.0, max_value=1.0, step=0.05, unit="fraction",
            plain=(
                "How much the final strategy ranking cares about how well the "
                "strategy's meaning fits the theme. The three ranking weights "
                "(meaning, credibility, backtest) should sum to 1.0."
            ),
            raise_effect="Best-fitting strategy wins even from an unproven source.",
            lower_effect="Fit matters less than pedigree and track record.",
        ),
        _P(
            key="matcher.credibility_weight",
            layer="Layer 8 · Strategy matching",
            title="Ranking weight: source credibility",
            default=0.3, min_value=0.0, max_value=1.0, step=0.05, unit="fraction",
            plain="How much the ranking cares about the credibility of whoever stated the strategy.",
            raise_effect="Proven experts' strategies dominate matching.",
            lower_effect="Source reputation matters less.",
        ),
        _P(
            key="matcher.backtest_weight",
            layer="Layer 8 · Strategy matching",
            title="Ranking weight: backtest evidence",
            default=0.2, min_value=0.0, max_value=1.0, step=0.05, unit="fraction",
            plain="How much the ranking cares about the strategy's historical backtest performance.",
            raise_effect="Historically-validated strategies dominate matching.",
            lower_effect="Backtest history matters less.",
        ),
        _P(
            key="matcher.cfa_default_credibility",
            layer="Layer 8 · Strategy matching",
            title="Textbook-framework credibility",
            default=75.0, min_value=50.0, max_value=100.0, step=1.0, unit="score",
            plain=(
                "CFA/institutional frameworks have no YouTube track record, so they "
                "get this fixed credibility — deliberately above the expert default "
                "of 50, because they're peer-reviewed methodology."
            ),
            raise_effect="Textbook frameworks beat expert strategies more often.",
            lower_effect="Expert-stated strategies win more ties.",
        ),
        _P(
            key="matcher.neutral_backtest_score",
            layer="Layer 8 · Strategy matching",
            title="Unbacktested-strategy neutral score",
            default=50.0, min_value=0.0, max_value=100.0, step=1.0, unit="score",
            plain="The backtest score assumed for strategies that haven't been backtested yet (neither rewarded nor punished).",
            raise_effect="Untested strategies rank better.",
            lower_effect="Untested strategies rank worse — favors the already-tested.",
        ),
        _P(
            key="patterns.min_repetitions",
            layer="Layer 8 · Strategy matching",
            title="Historical-pattern repetition bar",
            default=3, min_value=2, max_value=10, step=1, unit="count",
            plain=(
                "An expert must state essentially the same methodology in at least "
                "this many separate videos before it's promoted to a durable "
                "'historical pattern' in the library."
            ),
            raise_effect="Only truly recurring methodology gets promoted.",
            lower_effect="Twice-mentioned ideas get promoted as patterns.",
        ),
        # --- Layer 9: Guidance ----------------------------------------------
        _P(
            key="guidance.already_aligned_threshold_pct",
            layer="Layer 9 · Guidance",
            title="'Already aligned' portfolio share",
            default=15.0, min_value=5.0, max_value=50.0, step=1.0, unit="%",
            plain=(
                "If the user's portfolio already has at least this % in a theme's "
                "assets, we refuse to generate 'buy more' guidance — they're already "
                "positioned. Silence is a valid output."
            ),
            raise_effect="More increase-exposure guidance generated for already-invested users.",
            lower_effect="System goes quiet sooner — more refusals.",
        ),
        _P(
            key="guidance.default_review_months",
            layer="Layer 9 · Guidance",
            title="Default 'time to review' when nothing else states one",
            default=12, min_value=1, max_value=60, step=1, unit="months",
            plain=(
                "Every piece of guidance gets a 'review by' date so it doesn't look "
                "as fresh six months later as it did the day it was generated. This "
                "is the fallback used only when neither the matched strategy nor the "
                "theme itself states a holding period — those are always preferred."
            ),
            raise_effect="Guidance without a stated horizon stays flagged 'current' longer.",
            lower_effect="Guidance without a stated horizon gets flagged for review sooner.",
        ),
        _P(
            key="guidance.max_review_months",
            layer="Layer 9 · Guidance",
            title="Longest guidance can go without a review",
            default=6, min_value=1, max_value=36, step=1, unit="months",
            plain=(
                "How long you plan to HOLD an idea and how long until you CHECK it "
                "again are different things. A 5-year holding horizon should still "
                "be re-checked against fresh expert views and prices well before "
                "then. Every 'review by' date is capped at this many months."
            ),
            raise_effect="Long-horizon guidance stays flagged 'current' longer.",
            lower_effect="All guidance gets flagged for a re-check sooner.",
        ),
        _P(
            key="guidance.volatile_max_review_months",
            layer="Layer 9 · Guidance",
            title="Review cap for volatile assets (crypto)",
            default=3, min_value=1, max_value=12, step=1, unit="months",
            plain=(
                "Tighter review cap for asset classes that can move 50% in a "
                "quarter (crypto). A crypto idea checked in July shouldn't still "
                "look current in October."
            ),
            raise_effect="Crypto guidance stays flagged 'current' longer.",
            lower_effect="Crypto guidance gets flagged for a re-check sooner.",
        ),
        _P(
            key="sizing.conservative_max_position_pct",
            layer="Layer 9 · Guidance",
            title="Conservative: max theme position",
            default=5.0, min_value=1.0, max_value=25.0, step=0.5, unit="%",
            plain="Hard cap on how much of a conservative user's portfolio any single theme may target. The AI cannot exceed this — code clamps it.",
            raise_effect="Bigger positions allowed for cautious users.",
            lower_effect="Even smaller maximum positions.",
        ),
        _P(
            key="sizing.conservative_max_instrument_pct",
            layer="Layer 9 · Guidance",
            title="Conservative: max single instrument",
            default=3.0, min_value=0.5, max_value=15.0, step=0.5, unit="%",
            plain="Cap on any single stock/fund within a theme position, for conservative users.",
            raise_effect="More concentration in one instrument allowed.",
            lower_effect="Forces more diversification.",
        ),
        _P(
            key="sizing.moderate_max_position_pct",
            layer="Layer 9 · Guidance",
            title="Moderate: max theme position",
            default=10.0, min_value=2.0, max_value=30.0, step=0.5, unit="%",
            plain="Same theme-position cap, for moderate-risk users.",
            raise_effect="Bigger positions allowed.",
            lower_effect="Smaller maximum positions.",
        ),
        _P(
            key="sizing.moderate_max_instrument_pct",
            layer="Layer 9 · Guidance",
            title="Moderate: max single instrument",
            default=5.0, min_value=1.0, max_value=20.0, step=0.5, unit="%",
            plain="Single-instrument cap for moderate-risk users.",
            raise_effect="More concentration allowed.",
            lower_effect="Forces more diversification.",
        ),
        _P(
            key="sizing.aggressive_max_position_pct",
            layer="Layer 9 · Guidance",
            title="Aggressive: max theme position",
            default=15.0, min_value=3.0, max_value=40.0, step=0.5, unit="%",
            plain="Theme-position cap for aggressive-risk users.",
            raise_effect="Bigger positions allowed.",
            lower_effect="Smaller maximum positions.",
        ),
        _P(
            key="sizing.aggressive_max_instrument_pct",
            layer="Layer 9 · Guidance",
            title="Aggressive: max single instrument",
            default=8.0, min_value=1.0, max_value=25.0, step=0.5, unit="%",
            plain="Single-instrument cap for aggressive-risk users.",
            raise_effect="More concentration allowed.",
            lower_effect="Forces more diversification.",
        ),
        # --- Chat ------------------------------------------------------------
        _P(
            key="query.max_claims_in_context",
            layer="Chat · Grounded Q&A",
            title="Claims shown to the chat model",
            default=15, min_value=5, max_value=50, step=1, unit="count",
            plain="How many of the most relevant validated claims are handed to Claude when answering a chat question. More = richer answers, higher cost per question.",
            raise_effect="Richer answers, higher per-question cost.",
            lower_effect="Cheaper, but the model may lack context.",
        ),
    ]
}


# ---------------------------------------------------------------------------
# Override storage
# ---------------------------------------------------------------------------

def load_overrides() -> dict[str, Any]:
    """Read the overrides file; missing or corrupt file means 'no overrides'."""
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read tuning overrides ({}); using defaults", exc)
        return {}


def save_overrides(overrides: dict[str, Any]) -> None:
    """Persist overrides atomically; only known keys, only non-default values."""
    cleaned = {}
    for key, value in overrides.items():
        spec = PARAMETERS.get(key)
        if spec is None:
            continue
        coerced = _coerce(spec, value)
        if coerced != spec.default:
            cleaned[key] = coerced
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cleaned, indent=2, sort_keys=True) + "\n")
    tmp.replace(OVERRIDES_PATH)


def _coerce(spec: TuningParameter, value: Any) -> float | int:
    """Clamp to the safe range and match the default's type (int stays int)."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return spec.default
    num = max(float(spec.min_value), min(float(spec.max_value), num))
    return int(round(num)) if isinstance(spec.default, int) else num


def param(key: str) -> float | int:
    """
    The value every pipeline module should read: override if set, else default.

    Reads the overrides file on each call — modules call this once at import
    time, so the cost is negligible and jobs always start with fresh values.
    """
    spec = PARAMETERS[key]
    overrides = load_overrides()
    if key in overrides:
        return _coerce(spec, overrides[key])
    return spec.default


def effective_values() -> dict[str, float | int]:
    """All parameters with overrides applied — used by the dashboard."""
    overrides = load_overrides()
    return {
        key: _coerce(spec, overrides[key]) if key in overrides else spec.default
        for key, spec in PARAMETERS.items()
    }
