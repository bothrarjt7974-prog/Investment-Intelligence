# Prompt: strategy_extraction_v1

> **Purpose:** Extract every distinct investment STRATEGY (a methodological
> statement about HOW to invest — trigger, allocation, sizing, entry, hold,
> exit) from a piece of expert content and return structured JSON. Claims
> ("what will happen") are explicitly OUT of scope — they are extracted by
> the parallel Track A pass (`claim_extraction_v3`).
>
> **Used by:** `src/extraction/strategy_extractor.py`
>
> **Model:** Sonnet (bulk)
>
> **STATUS: NEW, v1. REQUIRES CALIBRATION BEFORE SCALING.** This is the
> first version of this prompt — there is no prior baseline to compare
> against. Before pointing `run_strategy_extraction` at the full backlog,
> run it on a 20-30 piece held-out sample (per `prompts/README.md`'s
> "Testing a new prompt" process), manually review the output against the
> source transcripts, and record the calibration result in
> `docs/decision_log.md` — the same discipline `claim_extraction_v3` and
> `quality_filter_v3` went through. Expect 0-3 strategies per piece (plan
> §5: strategies are rarer than claims); a piece that yields 0 is a normal,
> correct result, not a failure.
>
> **Version history:**
> - v1 (initial) — schema per `technical_execution_plan_v3.docx` §5 and §14.

---

You are analyzing investment content from a credible financial expert.

**Expert:** {{expert_name}}
**Source:** {{source_type}}
**Date:** {{published_at}}

Your task: identify every distinct investment STRATEGY in the content below.
A strategy is a statement where the expert describes **how to invest** — a
methodology, not a direction. Strategies have a trigger (when it applies),
an allocation rule (how much), an entry method, a holding horizon, and
(ideally) an exit condition.

**What is NOT a strategy — skip these, another extraction pass handles them:**

A statement about **what will happen** is a CLAIM, not a strategy. Do not
extract it, even partially. A separate Track A pass extracts claims with
its own schema. Skip anything whose core content is a directional
prediction with no accompanying methodology — "PSU banks will outperform
over 2 years" on its own is a claim, not a strategy.

**The dividing line:** "PSU banks will outperform over 2 years" = claim
(skip it here). "In rate-cut cycles I put 40% into PSU banks, weighted by
market cap, and hold 18 months" = strategy (extract it). When one sentence
contains both — "banking will re-rate, so I'm allocating 40% there, holding
18 months, trimming if the sector runs 30% above trend" — extract ONLY the
methodology part as a strategy ("in this setup, 40% to banking, 18-month
hold, trim at +30% vs trend") and record the directional entity via
`linked_claim_entity` (see below) so the two extraction tracks can be
joined back together after the fact.

**Extraction principle:** Strategies are rare — expect 0 to 3 per piece
(plan §5). Only extract a statement if the expert states an actual
methodology, not just a general belief ("I like to diversify" is too vague
to extract; "I keep 5-10% in gold and rebalance annually" is specific
enough). Do not manufacture a strategy where the expert only expressed a
directional view. Returning an empty array for a piece with no real
methodology is the correct, expected output — do not force one.

For each strategy, return a JSON object with these EXACT fields:

---

### `strategy_text` — the methodology, condensed

The expert's stated methodology in their own logic, condensed to 1-2
sentences. Preserve every number, percentage, and named condition they
gave — do not generalize "40 percent" into "a large chunk."

- ✅ Good: "In rate-cut cycles, allocate up to 40% of equity to PSU banks weighted by market cap, hold minimum 18 months, trim if the sector runs 30% above its 200-day trend."
- ❌ Bad: "He likes PSU banks sometimes."

---

### `strategy_type` — methodology category

One of: `allocation`, `entry_methodology`, `exit_methodology`,
`position_sizing`, `rebalancing`, `risk_management`, `full_playbook`,
`framework`

- `allocation` — how much to put where (asset class, sector, geography split)
- `entry_methodology` — how to enter a position (lump sum, DCA, on pullback)
- `exit_methodology` — when/how to trim or exit
- `position_sizing` — per-position caps, concentration limits
- `rebalancing` — periodic rebalancing rules
- `risk_management` — stop-loss-equivalent rules, hedging, drawdown response
- `full_playbook` — a complete entry+hold+exit methodology stated together (most common — use this when the statement covers multiple stages rather than forcing it into one narrower type)
- `framework` — an analytical/diagnostic model for evaluating investments ("I evaluate stocks on earnings quality, valuation, and capital allocation") rather than an allocation action itself

If a statement fits both `full_playbook` and a narrower type, prefer
`full_playbook` only when it genuinely spans entry AND hold AND exit;
otherwise pick the single most specific type.

---

### `trigger_conditions` — when this strategy applies

A JSON object describing the applicability condition, however the expert
stated it. Keep it close to their actual words plus a normalized `type`
field so the same trigger phrased differently still matches. Example:

```json
{"type": "rate_cut_cycle", "description": "RBI cutting rates in consecutive policy meetings"}
```

Use `null` if the expert stated no condition at all (the strategy is a
standing/always-on rule, e.g. "I always keep 6 months expenses in a liquid
fund"). Common `type` values to reuse when they fit: `rate_cut_cycle`,
`rate_hike_cycle`, `valuation_extreme_cheap`, `valuation_extreme_rich`,
`drawdown_threshold`, `currency_regime`, `always_on`. Invent a new
lowercase_with_underscores type only if none of these fit — be
conservative, prefer reusing an existing type.

---

### `entity_class` — what this strategy applies to

The sector, asset class, geography, or specific instrument(s) this
strategy operates on. Use the **same canonical naming rules** as claim
extraction (`claim_extraction_v3.md`'s entity rules): stock tickers
UPPERCASE no suffix, indices/sectors/macro factors lowercase_with_underscores.
For a strategy spanning multiple named instruments or a broad category
("equity" as an asset class generally), use the most specific canonical
term that still covers what the expert said; use `broad_equity` for a
strategy that isn't sector/instrument specific at all.

---

### `allocation_rule` — how much

The allocation stated, in the expert's terms — percentages, weights, or
ranges. Preserve the exact numbers. `null` if the strategy doesn't specify
an allocation (e.g., a pure exit-timing rule).

- ✅ "Up to 40% of equity allocation, weighted by market cap"
- ✅ "5-10% of total portfolio"

---

### `position_sizing` — scale rules

Per-position caps or concentration limits, if stated. `null` if not
addressed.

- ✅ "Never more than 10% in a single stock"

---

### `entry_methodology` — how to enter

How to build the position: lump sum, DCA over a stated period, on a
pullback, etc. `null` if not addressed.

- ✅ "Lump sum on trigger confirmation"
- ✅ "DCA over 6 months"

---

### `holding_horizon_months` — stated minimum/target hold

An integer number of months, if the expert stated a holding period or
minimum hold. `null` if not stated. Do not invent a bucket-median fallback
here — unlike claims, there is no default to fall back to; leave it `null`
when genuinely unstated.

---

### `exit_conditions` — when to trim or exit

The stated exit trigger — a valuation threshold, thesis-break condition, or
time-based rule. `null` if the expert stated no exit condition at all (a
half-specified strategy is still worth extracting — completeness is scored
downstream by the quality filter, not enforced here).

- ✅ "Trim if the sector runs 30% above its 200-day trend"
- ✅ "Exit if the original thesis breaks (e.g., earnings deteriorate 2 consecutive quarters)"

---

### `risk_framework` — stated risk controls

Any stated risk control, hedge, or failure-mode acknowledgment specific to
executing this strategy (distinct from the general claim-level counter-
argument acknowledgment). `null` if not addressed.

- ✅ "Never add on a 50%+ drawdown — treat that as thesis invalidation, not a buying opportunity"

---

### `reason_given` — the expert's rationale for the methodology

A 1-2 sentence summary, in your own words, of WHY the expert uses this
particular methodology (not why the underlying asset will do well — that
belongs to the linked claim, if any).

---

### `linked_claim_entity` — the directional claim this operationalizes

The canonical entity name (same naming rules as `entity_class` above) of a
directional claim made **elsewhere in this same piece** that this strategy
operationalizes — e.g., if the expert says "banking will re-rate" earlier
and later states the PSU-bank allocation strategy, set this to `indian_banking`
or the specific entity they claimed on. Set to `null` if:
- the strategy is stated standalone with no accompanying directional claim in this content, or
- you are not confident which claim (if any) it pairs with.

**Do not invent a claim ID or guess at a claim you have not actually seen
stated in this content.** This field is a hint for downstream code to
resolve into an actual database link — a wrong guess here would misattribute
a strategy to a claim it doesn't belong to, so leave it `null` when unsure.

---

## Critical output rules

1. **Return ONLY a valid JSON array.** No prose before or after. No markdown code fences. Just `[{...}, {...}]`.

2. **0 to 3 strategies per piece is normal.** Most content has zero explicit strategies — experts state claims far more often than methodology. An empty array `[]` is a valid, expected, and common output. Never manufacture a strategy to avoid returning an empty array.

3. **Skip claims entirely.** Directional predictions with no accompanying methodology are Track A's job. Extract only the methodology when a statement mixes both.

4. **Never invent a strategy that isn't in the content.** If the expert didn't state an actual methodology (trigger, allocation, sizing, entry, or exit — at least one of these must be concretely present), don't extract it. "I'm bullish and diversify" is too vague; skip it.

5. **If the same methodology is restated multiple times, include it only once** — use the most complete version stated.

6. **Skip pleasantries, intros, outros, and sponsor mentions.**

7. **Preserve specificity.** If the expert said "40 percent weighted by market cap", don't soften to "a significant allocation."

---

## Example output format

```json
[
  {
    "strategy_text": "In rate-cut cycles, allocate up to 40% of equity to PSU banks weighted by market cap, hold minimum 18 months, trim if the sector runs 30% above its 200-day trend.",
    "strategy_type": "full_playbook",
    "trigger_conditions": {"type": "rate_cut_cycle", "description": "RBI cutting rates in consecutive policy meetings"},
    "entity_class": "indian_banking",
    "allocation_rule": "Up to 40% of equity allocation, weighted by market cap",
    "position_sizing": null,
    "entry_methodology": "Lump sum on trigger confirmation",
    "holding_horizon_months": 18,
    "exit_conditions": "Trim if the sector runs 30% above its 200-day trend",
    "risk_framework": null,
    "reason_given": "Rate cuts historically re-rate PSU banks faster than private banks due to their funding-cost sensitivity, so entering at the cycle turn captures the re-rating.",
    "linked_claim_entity": "indian_banking"
  },
  {
    "strategy_text": "Keep 5-10% of portfolio in BTC as an asymmetric bet for medium-risk investors, rebalance annually, never add on 50%+ drawdowns.",
    "strategy_type": "allocation",
    "trigger_conditions": null,
    "entity_class": "BTC",
    "allocation_rule": "5-10% of total portfolio",
    "position_sizing": null,
    "entry_methodology": null,
    "holding_horizon_months": null,
    "exit_conditions": null,
    "risk_framework": "Never add on a 50%+ drawdown — treat that as thesis invalidation, not a buying opportunity",
    "reason_given": "Small asymmetric allocations to high-volatility assets can meaningfully improve portfolio returns without threatening overall capital if sized correctly.",
    "linked_claim_entity": null
  }
]
```

Content:
