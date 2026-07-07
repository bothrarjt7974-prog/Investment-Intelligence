# Prompt: claim_extraction_v3

> **Purpose:** Extract every distinct directional investment CLAIM from a piece
> of expert content and return structured JSON with canonical, validatable
> fields. Strategies ("how to invest") are explicitly OUT of scope — they are
> extracted by the parallel Track B pass (strategy_extraction_v1).
>
> **Used by:** `src/extraction/extractor.py`
>
> **Model:** Sonnet (bulk)
>
> **STATUS: SUPERSEDED by claim_extraction_v4 (2026-07-07).** Was wired
> into `extractor.py` at Phase B kickoff but never recalibrated at scale
> before v4 replaced it — v3 shares v2's thin `reason_given` capture, which
> the 2026-07-04 diagnosis identified as the reason `quality_filter_v3`
> only cleared 1.6% of real claims. Kept here for reference and rollback.
>
> **Version history:**
> - v1 (initial) — basic extraction with 8-field schema
> - v2 — canonical entity naming, stricter strength rules, currency-pair
>        geography rule, target_horizon_months field, strategy-with-instrument
>        edge case, tighter recall guidance
> - v3 — dual-extraction alignment (technical_execution_plan_v3 §5): REMOVES
>        the `strategy` claim type — methodological statements now belong to
>        Track B and are skipped here with an explicit skip rule; renames
>        output field `type` → `claim_type` to match the DB column; volume
>        guidance aligned to plan (5-15 typical); frameworks/constraints
>        called out as skips (Track B captures them as strategy_type=framework)

---

You are analyzing investment content from a credible financial expert.

**Expert:** {{expert_name}}
**Source:** {{source_type}}
**Date:** {{published_at}}

Your task: identify every distinct investment CLAIM in the content below.
A claim is a statement where the expert asserts something about **what will
happen** — to markets, an asset, a sector, or an economic condition.

**What is NOT a claim — skip these, another extraction pass handles them:**

A statement about **how to invest** is a STRATEGY, not a claim. Do not
extract it, even partially. A separate Track B pass extracts strategies with
their own schema. Skip anything whose core content is:

- Allocation rules — "keep 5-10% of your portfolio in gold"
- Entry/exit methodology — "buy in tranches over 6 months", "trim when the
  sector runs 30% above trend"
- Position sizing or concentration limits — "never more than 10% in one stock"
- Rebalancing rules — "rebalance annually"
- Analytical frameworks — "I evaluate stocks on earnings quality, valuation,
  and capital allocation"

**The dividing line:** "PSU banks will outperform over 2 years" = claim
(extract it). "In rate-cut cycles I put 40% into PSU banks and hold 18
months" = strategy (skip it). When one sentence contains both — "banking
will re-rate, so I'm allocating 40% there" — extract ONLY the directional
part as a claim ("banking will re-rate") and skip the allocation part.

**Extraction principle:** Aim for 5 to 15 high-quality claims per piece.
Only extract claims the expert genuinely commits to or builds reasoning
around. Skip throwaway statements, asides, and pleasantries even if they
mention markets. Better to have 6 strong claims than 14 weak ones.

For each claim, return a JSON object with these EXACT fields:

---

### `claim_text` — the testable form

The specific testable form of the claim in ONE sentence. Make it specific
and verifiable. Preserve numbers, percentages, and timeframes the expert
gave. Rewrite into one clean sentence even if the expert rambled.

- ✅ Good: "Infosys will outperform Nifty IT index over next 12 months due to rupee depreciation tailwind."
- ❌ Bad: "He says Infosys is good."

---

### `claim_type` — claim category

One of: `macro`, `sector`, `stock_pick`, `overview`

- `macro` — about global economy, currencies, rates, geopolitics, commodities, GDP
- `sector` — about an industry segment (IT, banking, pharma, US tech as a group)
- `stock_pick` — about a specific named company (Reliance, Apple, Tesla)
- `overview` — context/education/history without a directional view or prediction

If a claim could fit multiple types, pick the most specific. A claim about
Apple is `stock_pick`, not `sector`. A claim about US tech as a category
is `sector`, not `stock_pick`.

**There is no `strategy` type in v3.** If you are tempted to use one, the
statement is a strategy — skip it entirely per the rules above.

---

### `geography` — region affected

One of: `india`, `us`, `global`, `emerging_markets`

**Critical rules:**

- **Currency pairs** are tagged by the FIRST currency. `inr_usd` is always `india`. `jpy_usd` is always `global` (Japan not in our scope). `usd_dominance` is `us`.
- **Trade dynamics** between specific countries (US-India trade, US-China tariffs) are `global`.
- **Country-specific policy** is tagged with that country (Indian tax policy → `india`, Fed rates → `us`).
- When a claim is about how a US event affects Indian markets, tag the PRIMARY market the claim is about — usually `india` if the conclusion is "this hits Indian assets".

---

### `timeframe` — when the prediction resolves

One of: `short` (under 1 year), `medium` (1–3 years), `long` (3+ years)

If the expert states an explicit horizon (e.g., "next 6 months", "by 2030"),
extract that into `target_horizon_months` below regardless of which bucket
you choose. Use the bucket that best matches the explicit horizon.

---

### `target_horizon_months` — specific resolution horizon

An integer number of months from the claim date to when this claim would
be falsifiable. Rules:

- If the expert states an explicit horizon, use that number ("next 6 months" → 6).
- If not stated, use the bucket median: `short` → 6, `medium` → 24, `long` → 60.
- For pure opinion claims with no testable horizon, set to `null`.

This field is essential — the validation engine uses it, not the timeframe bucket.

---

### `strength` — claim conviction

One of: `strong`, `soft`, `opinion`

- `strong` — Use this when the claim has ANY of: specific numbers, specific timeframe, clear directional commitment ("will rise/fall"), or a falsifiable prediction. The expert is putting their reputation on it.
  - ✅ "Reliance will hit ₹3000 within 12 months" — has number + timeframe
  - ✅ "Indian midcap will underperform large cap in 2026" — clear directional commitment
  - ✅ "US tech is at a 10-15% discount and offers better value than Indian equities" — specific quantification

- `soft` — Use ONLY when the expert explicitly hedges with words like "might", "could", "I lean toward", "probably", "I'd be cautious", or expresses uncertainty about direction.
  - ✅ "Gold might continue to rally if DXY weakens further" — explicit "might"
  - ✅ "I lean bullish on banking but acknowledge the credit risk" — explicit hedge

- `opinion` — Use ONLY when there is no falsifiable prediction. This is commentary or thesis without a testable outcome.
  - ✅ "SIP investing is structurally disadvantaged because IPO timing exploits inflows" — thesis statement, not a prediction
  - ✅ "GDP growth doesn't predict stock returns" — framework belief stated as commentary
  - ❌ NOT opinion: "Bitcoin will see a 20% correction soon" — this is a soft or strong prediction depending on hedging

**When in doubt between strong and soft, ask: did the expert hedge their language?**
If no, it's strong. The default should be strong, not soft.

---

### `entity` — the specific subject

The specific stock, sector, or factor the claim is about. **Strict canonical naming rules — these matter for downstream convergence detection:**

**Stock tickers:** ALWAYS uppercase, no exchange suffix.
- ✅ `RELIANCE`, `INFY`, `AAPL`, `NVDA`, `BTC`, `ZOMATO`
- ❌ `Reliance`, `reliance`, `RELIANCE.NS`, `BTC_USD` (use just `BTC`)

**Indices:** ALWAYS lowercase_with_underscores.
- ✅ `nifty50`, `nifty_midcap150`, `nifty_smallcap250`, `nasdaq`, `sp500`, `qqq` (when referring to index)
- ❌ `NIFTY50`, `Nifty 50`, `NASDAQ`

**Sectors:** ALWAYS lowercase_with_underscores, geography prefix when relevant.
- ✅ `indian_it`, `indian_banking`, `indian_pharma`, `us_tech`, `us_software`, `us_semiconductors`, `global_ai`
- ❌ `IT`, `Indian IT`, `indian_IT`, `indian_technology` (use `indian_it`)
- Canonical sector names to use: `indian_it`, `indian_banking`, `indian_pharma`, `indian_auto`, `indian_fmcg`, `indian_realestate`, `indian_defense`, `indian_metals`, `indian_energy`, `indian_consumer_tech`, `indian_midcap`, `indian_smallcap`, `us_tech`, `us_software`, `us_semiconductors`, `us_banking`, `us_healthcare`, `us_energy`, `us_defense`, `us_consumer`, `global_ai`, `crypto_sector`

**Macro factors:** ALWAYS lowercase_with_underscores.
- ✅ `inr_usd`, `fed_rate`, `rbi_rate`, `crude_oil`, `gold`, `silver`, `dxy`, `us_inflation`, `india_inflation`, `india_gdp`, `us_gdp`, `global_liquidity`, `us_china_tensions`
- ❌ `oil_price` (use `crude_oil`), `BTC_USD` (use `BTC`), `INR` (use `inr_usd`), `gold_silver` (split into two claims)

**If you encounter something that doesn't fit any canonical name, create a new one following the lowercase_with_underscores convention with geography prefix where relevant. Be conservative — prefer adapting an existing canonical name over inventing a new one.**

---

### `direction` — bullish, bearish, or neutral

One of: `bullish`, `bearish`, `neutral`, `na`

- `bullish` — expert expects the entity to rise / outperform / improve
- `bearish` — expert expects the entity to fall / underperform / deteriorate
- `neutral` — expert expects flat/sideways movement, or explicitly says "no strong view"
- `na` — use ONLY for `overview` claims where direction doesn't apply

For `stock_pick`, direction should almost never be `na`. If you're tempted to use `na` for a stock pick, the claim is probably actually `overview` — or a strategy you should be skipping.

---

### `reason_given` — the why behind the claim

A 1–2 sentence summary of the expert's REASONING for the claim, in your own words. Capture WHY they think this, not just WHAT.

This field is essential — convergence detection later compares reasoning chains across experts to determine if multiple agreements are independent or echoes of each other.

**Be faithful to the expert's actual logic.** If they cite specific data ("PE of 22 vs sector PE of 28"), preserve it. If they invoke a framework ("when DXY falls, gold rises historically"), preserve it. If they reference an event ("Fed pivot in March"), preserve it.

---

## Critical output rules

1. **Return ONLY a valid JSON array.** No prose before or after. No markdown code fences. Just `[{...}, {...}]`.

2. **5 to 15 claims per piece is the target.** If you find fewer than 3 distinct substantive claims, the content is mostly conversational — return what you find. Never exceed 15 claims; if you're tempted to, you're extracting filler.

3. **Skip strategies entirely.** Allocation rules, entry/exit methodology, sizing, rebalancing, frameworks — a separate pass extracts them. Extract only the directional claim embedded in a mixed statement, never the methodology.

4. **If a claim is hedged, mark `strength: soft`.** If it's a specific prediction, mark `strong`. If it's commentary without a falsifiable prediction, mark `opinion`. Do not default to `soft` when uncertain — apply the rules above.

5. **If the same claim is made multiple times, include it only once** — use the strongest version stated.

6. **Skip pleasantries, intros, outros, and sponsor mentions.**

7. **Never invent claims that aren't in the content.** If the expert didn't say it, don't extract it.

8. **Preserve specificity.** If the expert said "PE of 1,250x", don't soften to "high PE". If they said "next 6 months", don't generalize to "soon".

---

## Example output format

```json
[
  {
    "claim_text": "INR will continue depreciating against USD over the next 6 months due to widening current account deficit.",
    "claim_type": "macro",
    "geography": "india",
    "timeframe": "short",
    "target_horizon_months": 6,
    "strength": "strong",
    "entity": "inr_usd",
    "direction": "bearish",
    "reason_given": "Current account deficit is widening due to oil imports, and Fed unlikely to cut rates soon which keeps the dollar structurally strong."
  },
  {
    "claim_text": "Indian IT sector offers better risk-reward than consumer tech like Zomato in 2026 due to valuation gap.",
    "claim_type": "sector",
    "geography": "india",
    "timeframe": "medium",
    "target_horizon_months": 12,
    "strength": "strong",
    "entity": "indian_it",
    "direction": "bullish",
    "reason_given": "IT stocks have acceptable earnings with non-stretched valuations while Zomato has strong earnings but extremely rich valuations, making risk-reward unattractive under the earnings-plus-valuation framework."
  }
]
```

(Note the absence of a strategy example — "hold 5-10% of portfolio in Bitcoin"
would have been extracted under v2; under v3 it is skipped here and captured
by Track B instead.)

Content:
