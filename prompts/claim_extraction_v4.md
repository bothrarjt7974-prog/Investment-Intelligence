# Prompt: claim_extraction_v4

> **Purpose:** Extract every distinct directional investment CLAIM from a piece
> of expert content and return structured JSON with canonical, validatable
> fields. Strategies ("how to invest") are explicitly OUT of scope — they are
> extracted by the parallel Track B pass (strategy_extraction_v2).
>
> **Used by:** `src/extraction/extractor.py`
>
> **Model:** Sonnet (bulk)
>
> **STATUS: CALIBRATED (2026-07-07) — material pass-rate improvement on a
> 20-piece sample, not yet confirmed at full-backlog scale.** Fixes two
> documented defects found by measuring v3 output at real volume (see
> 2026-07-07 decision log entry):
>
> 1. `quality_filter_v3` cleared only 1.6% of real claims (2/122) at `pass`.
>    The 2026-07-04 diagnosis found the root cause: `reason_given` was a
>    1-2 sentence *paraphrase* — too thin for the filter's 5 dimensions
>    (reasoning_depth, data_support, ...) to ever score "strong" on. v4
>    asks for the verbatim reasoning chain instead of a summary.
> 2. `verification_status='conditional_unresolved'` exists in the schema
>    (claims/migration 001) but nothing ever assigns it — "if RBI cuts,
>    banks re-rate" was force-validated as if unconditional. v4 adds
>    `is_conditional`/`condition_text` so the validator can finally use it
>    honestly (`src/validation/validator.py`).
>
> **Calibration result (20 fresh pieces, 40 claims, re-screened through
> unchanged `quality_filter_v3`): 3 pass / 33 partial / 4 fail = 8% pass**,
> up from the v3 baseline's ~1.6%. Manually spot-checked 3 `pass` claims —
> `reason_given` carries real mechanism, numbers, and counter-arguments,
> not padding. Still only a 20-piece sample; re-confirm the distribution
> holds before/during the full 732-piece backlog run, not just trust this
> single batch indefinitely.
>
> **Version history:**
> - v1 (initial) — basic extraction with 8-field schema
> - v2 — canonical entity naming, stricter strength rules, currency-pair
>        geography rule, target_horizon_months field, strategy-with-instrument
>        edge case, tighter recall guidance
> - v3 — dual-extraction alignment: removes `strategy` claim type, renames
>        `type` → `claim_type`. Never recalibrated against real output —
>        wired into `extractor.py` at Phase B kickoff, superseded here
>        before a full-backlog run ever happened.
> - v4 — richer verbatim `reason_given` (fixes quality-filter input
>        poverty); adds `is_conditional`/`condition_text` (fixes the
>        force-scored-as-unconditional gap). Schema otherwise unchanged
>        from v3 — same claim_type/geography/timeframe/strength/entity rules.

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

**There is no `strategy` type.** If you are tempted to use one, the
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

### `reason_given` — the expert's verbatim reasoning chain

**This is the field that changed most in v4 — read carefully.** Prior
versions asked for a 1-2 sentence *summary*. That produced claims too thin
for the quality filter to ever score "strong" on reasoning depth or data
support, even when the expert's actual reasoning was substantive. v4 asks
for the reasoning chain itself, not your condensation of it.

Capture the expert's FULL stated reasoning, in 3-6 sentences when that much
material exists in the content. Preserve, verbatim or near-verbatim:

- Every specific number, ratio, or percentage they cite ("PE of 22 vs sector
  PE of 28", "revenue grew 18% YoY", "10-year average CAGR of 14%")
- Every named event, data point, or source they reference ("Fed pivot in
  March", "Q3 earnings call", "RBI's April policy statement")
- Every framework or mechanism they invoke ("when DXY falls, gold
  historically rises because...")
- Any counter-argument, risk, or alternative they weigh against ("the risk
  is X, but Y outweighs it because...")

**Do not compress a multi-step argument into one sentence just because you
can.** If the expert walks through three linked reasons, give three linked
sentences. If the expert genuinely only gave one thin reason ("because
it's a good company"), report that faithfully and briefly — do not pad or
invent additional reasoning that wasn't stated.

**Still your own words, not a transcript quote-dump** — condense filler,
repetition, and false starts, but never condense away the substance:
numbers, mechanisms, and named references survive; "um", "so basically",
and restatements do not.

- ❌ Too thin (the v3-era failure mode): "IT stocks have acceptable earnings with non-stretched valuations while Zomato has strong earnings but extremely rich valuations, making risk-reward unattractive."
- ✅ v4 target: "Indian IT trades at roughly 22x forward earnings against a 10-year average of 24x, while earnings growth has held at 8-10% YoY through the last four quarters despite the sector-wide slowdown narrative. Zomato, by contrast, trades at over 150x forward earnings despite only recently turning profitable, and its take-rate has been under pressure from Swiggy's aggressive discounting in the same quarters. The expert frames this as a valuation-versus-durability tradeoff: IT's growth is slower but priced fairly and backed by four quarters of consistent execution, while Zomato's growth story requires the current take-rate to hold, which the competitive dynamics argue against."

---

### `is_conditional` — is this an if/then claim?

`true` or `false`.

Some claims are stated conditionally: "IF the Fed cuts in September, THEN
gold rallies" is fundamentally different from an unconditional "gold will
rally" — the first is only testable if the condition itself resolves, and
force-validating it as if unconditional produces a false negative when the
condition never materializes.

- `true` — the claim's directional prediction depends on a stated
  condition/trigger event that may or may not happen.
- `false` — the claim is a direct, unconditional prediction (the large
  majority of claims — most experts state direction without an explicit
  precondition).

When `true`, also fill in `condition_text` (see next). When `false`, set
`condition_text` to `null`.

**Do not mark a claim conditional just because it has a causal explanation**
("gold rallies because the Fed will likely cut" is still an unconditional
prediction — the expert is asserting the Fed WILL cut, not conditioning on
IF they do). Only mark `is_conditional: true` when the expert explicitly
frames the prediction as contingent on something not yet resolved ("if",
"assuming", "should X happen", "in the event that").

---

### `condition_text` — the stated condition (nullable)

A one-sentence statement of the condition itself, in the expert's terms,
when `is_conditional` is `true`. `null` when `is_conditional` is `false`.

- ✅ "The Fed cuts rates in September."
- ✅ "India and the US sign the proposed trade deal before year-end."

---

## Critical output rules

1. **Return ONLY a valid JSON array.** No prose before or after. No markdown code fences. Just `[{...}, {...}]`.

2. **5 to 15 claims per piece is the target.** If you find fewer than 3 distinct substantive claims, the content is mostly conversational — return what you find. Never exceed 15 claims; if you're tempted to, you're extracting filler.

3. **Skip strategies entirely.** Allocation rules, entry/exit methodology, sizing, rebalancing, frameworks — a separate pass extracts them. Extract only the directional claim embedded in a mixed statement, never the methodology.

4. **If a claim is hedged, mark `strength: soft`.** If it's a specific prediction, mark `strong`. If it's commentary without a falsifiable prediction, mark `opinion`. Do not default to `soft` when uncertain — apply the rules above.

5. **If the same claim is made multiple times, include it only once** — use the version with the richest stated reasoning, not just the first or last mention.

6. **Skip pleasantries, intros, outros, and sponsor mentions.**

7. **Never invent claims that aren't in the content.** If the expert didn't say it, don't extract it. This applies doubly to `reason_given` in v4 — richer capture means more material to preserve, never more material to invent.

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
    "reason_given": "The current account deficit has widened to roughly 2.1% of GDP over the last two quarters, driven primarily by oil imports, which the expert notes have not fallen despite softer crude prices because of higher import volumes. Separately, the Fed has signaled it is unlikely to cut rates before Q2 next year given still-elevated core inflation readings, which keeps the dollar structurally supported against emerging-market currencies. The expert frames these as compounding pressures rather than independent factors: a widening deficit needs external financing, and a strong dollar makes that financing more expensive, reinforcing the depreciation pressure.",
    "is_conditional": false,
    "condition_text": null
  },
  {
    "claim_text": "If the RBI cuts rates at its next policy meeting, PSU banks will re-rate over the following two quarters.",
    "claim_type": "sector",
    "geography": "india",
    "timeframe": "short",
    "target_horizon_months": 8,
    "strength": "strong",
    "entity": "indian_banking",
    "direction": "bullish",
    "reason_given": "The expert's mechanism is that PSU banks carry a higher proportion of floating-rate liabilities than private banks, so a repo rate cut compresses their funding costs faster than it compresses lending yields, expanding net interest margins in the near term. He cites the 2020 cutting cycle as the historical precedent, where PSU bank stocks re-rated roughly 20-30% within two quarters of the first cut. He explicitly conditions this on the RBI actually cutting at the next meeting, noting that if the RBI holds instead (which he considers plausible given recent inflation prints), this mechanism does not activate.",
    "is_conditional": true,
    "condition_text": "The RBI cuts rates at its next monetary policy meeting."
  }
]
```

(Note the absence of a strategy example — "hold 5-10% of portfolio in Bitcoin"
would have been extracted under v1/v2; it is skipped here and captured by
Track B instead.)

Content:
