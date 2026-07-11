# Prompt: claim_extraction_v5

> **Purpose:** Extract every distinct directional investment CLAIM from a piece
> of expert content and return structured JSON with canonical, validatable
> fields. Strategies ("how to invest") are explicitly OUT of scope — they are
> extracted by the parallel Track B pass (strategy_extraction_v2).
>
> **Used by:** `src/extraction/extractor.py` (NOT YET WIRED IN — v4 remains
> active until this is calibrated per project convention: one prompt change
> at a time, re-measured on a sample before scaling)
>
> **Model:** Sonnet (bulk)
>
> **STATUS: CALIBRATED (2026-07-11) — real improvement, not a complete
> fix.** Targets two specific, narrow defects found in the 2026-07-09/11
> data audits, not a rewrite:
>
> 1. **Non-financial content survives extraction as usable claims.** The
>    audit found real claims about fire-brigade readiness, scam-punishment
>    policy, and FSI zoning corruption — accurate, well-reasoned, and
>    completely uninvestable. They pass the quality filter as `partial`
>    (a testable, well-reasoned claim about *anything* scores fine on the
>    filter's 5 reasoning dimensions) and sit in the claims table forever
>    with no downstream consumer that can ever act on them. v4's scope
>    rules only distinguish claim-vs-strategy; they never asked "is this
>    about an investable entity at all." v5 adds that check.
> 2. **17 pieces failed extraction twice with "Malformed JSON from Claude
>    claim extraction."** 15 of 17 are short (110-600 word) Hindi/Hinglish
>    transcripts. Root cause could not be directly inspected (the pipeline
>    doesn't log raw failed responses — see the code-side note below), but
>    the pattern points at two plausible mechanisms: (a) thin content near
>    the extraction floor produces a hedging, part-prose response that
>    still contains a stray `[` (so it isn't caught by the existing
>    `NonFinancialContentError` path, which only fires when the response
>    has no `[` at all — see `extractor.py::parse_claims_response`), and
>    (b) Hindi/Hinglish reasoning text containing literal quotation marks
>    (common in code-switched transliteration, e.g. quoting a phrase like
>    "setting chalta hai") breaks JSON string escaping. v5 adds an
>    explicit empty-array instruction for thin content and an explicit
>    escaping reminder for non-English text.
>
> **What this draft does NOT attempt:** the audit's third finding
> (9.9% of claims have no `entity`) is left alone — a spot-check of
> entity-null claims found several genuinely entity-less opinion claims
> ("SIP investing is structurally disadvantaged..."), which is correct
> behavior, not a defect. Bundling an unrelated fix here would make it
> impossible to attribute any pass-rate change to a single cause.
>
> **Calibration result (17 known-failure pieces + 15-piece random control
> sample, screened through unchanged `quality_filter_v3`):**
>
> - **Malformed-JSON failures: 13 of 17 fixed (76%).** 12 of the 17 now
>   correctly resolve to empty output (including the 2 genuine junk pieces
>   — movie trailers mislabeled 'hi'); 1 (the OYO IPO piece) extracted 3
>   real claims. **4 still fail** — 3 Hindi (the escaping fix did not
>   eliminate every case) and 1 English (Shankar Nath's 5274-word
>   "15 High-Growth Companies" piece — almost certainly output truncation
>   on a long structured list, an unrelated failure mode this draft
>   doesn't address). Not a complete fix; a real improvement.
> - **Control sample pass rate: 11.9% (14/118), vs the v4 baseline's 7.5%
>   (3/40).** Not a strict head-to-head (different pieces), but no
>   evidence of regression — if anything a positive signal.
> - **Spot-checked the "empty" outcomes against source transcripts** for
>   the most concerning cases (a startup-investing video, a "how I
>   analyze stocks" video, a debt-clearing advice call, a rent-vs-buy
>   explainer) — all are strategy/framework/personal-advice content the
>   **pre-existing** v1-v4 rules would also exclude, not new v5
>   over-triggering on real investment claims.
>
> **Open follow-up (not this draft's scope):** the remaining 4 failures
> need their own diagnosis — the pipeline doesn't log raw failed
> responses, so root-causing them further requires adding that logging
> first. The English truncation case suggests `max_tokens` may need
> raising for content with many distinct claims.
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
>        ACTIVE (2026-07-07 calibration, 8% pass on 20-piece sample).
> - v5 — adds an explicit non-investable-content exclusion rule (fixes
>        real-but-uninvestable claims surviving as usable, e.g. fire
>        safety, scam punishment); adds an empty-array instruction for
>        thin/marginal content and a JSON-escaping reminder for
>        Hinglish/Hindi reasoning text (fixes repeat malformed-JSON
>        failures on short Hindi transcripts). Schema unchanged from v4.

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

**What is NOT investment content — skip entirely, even if well-reasoned
and testable (new in v5):**

Some statements have the *shape* of a claim — a clear assertion, real
reasoning, even a testable outcome — but aren't about anything a portfolio
can hold or avoid. This system exists to guide portfolio allocation, so a
claim only earns extraction if a reasonable person could act on it by
buying, selling, or avoiding an investable entity (a stock, sector, index,
currency, commodity, or macro factor that moves asset prices).

Skip statements whose core content is:

- **Regulatory or policy commentary with no investable entity** —
  "fire brigades are under-resourced", "FSI zoning norms are routinely
  violated through corruption", "financial-crime punishment is too lenient
  to deter repeat offenders". These can be rigorously reasoned and even
  cite real data — that doesn't make them investment claims. There is no
  ticker, sector, or macro factor a portfolio can express a view on here.
- **General life/career advice with no market view** — "always negotiate
  your salary", "an emergency fund should cover 3-6 months of expenses" as
  a standalone statement (note: this becomes a STRATEGY, not a skip, if
  framed as a portfolio allocation rule — see the strategy rules above).
- **Pure biography, history, or anecdote with no forward-looking market
  view** — "here's how Warren Buffett built his fortune", "I made this
  mistake in 2021" told purely as a story, with no claim about what will
  happen next to anything investable.

**The test:** could an investor act on this by buying, selling, or
avoiding something? If the honest answer is "there's nothing here to buy,
sell, or avoid — it's a fact or opinion about the world," skip it, no
matter how well-argued. When genuinely unsure whether a claim clears this
bar, err toward extracting it — this rule is for the clear cases (fire
safety, crime policy, general life advice), not a license to under-extract
real sector or macro claims phrased indirectly.

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

**JSON escaping reminder for non-English/code-switched content (new in
v5):** when the source content is in Hindi, Hinglish, or another
non-English language, your `reason_given` and `claim_text` may naturally
include quoted phrases or code-switched terms (e.g. quoting the phrase
"setting chalta hai"). Escape every literal double-quote character inside
a JSON string value with `\"` — never leave an unescaped `"` inside a
string. When in doubt, prefer paraphrasing a quoted phrase in your own
words over reproducing it verbatim with quote marks, since a paraphrase
that stays valid JSON is worth more downstream than a verbatim quote that
breaks parsing.

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

3. **If the content contains no extractable investment claims at all, return an empty array `[]` — do not explain why in prose (new in v5).** This applies to genuinely non-financial content (movie clips, career advice with no market view) and to thin/marginal content where you're unsure any claim clears the bar. `[]` is always valid output; a partial explanation mixed with an incomplete array is not. When even one or two claims are extractable, extract just those — you do not need to hit 5 to return a non-empty array.

4. **Skip strategies entirely.** Allocation rules, entry/exit methodology, sizing, rebalancing, frameworks — a separate pass extracts them. Extract only the directional claim embedded in a mixed statement, never the methodology.

5. **Skip non-investable content entirely**, even when well-reasoned and testable — see the "What is NOT investment content" section above. Regulatory/safety/policy commentary with no investable entity, general life advice, and pure biography/anecdote with no forward-looking market view are not claims.

6. **If a claim is hedged, mark `strength: soft`.** If it's a specific prediction, mark `strong`. If it's commentary without a falsifiable prediction, mark `opinion`. Do not default to `soft` when uncertain — apply the rules above.

7. **If the same claim is made multiple times, include it only once** — use the version with the richest stated reasoning, not just the first or last mention.

8. **Skip pleasantries, intros, outros, and sponsor mentions.**

9. **Never invent claims that aren't in the content.** If the expert didn't say it, don't extract it.

10. **Preserve specificity.** If the expert said "PE of 1,250x", don't soften to "high PE". If they said "next 6 months", don't generalize to "soon".

11. **Escape every literal quotation mark inside a JSON string value.** This applies to all content but matters most for non-English/code-switched reasoning — see the `reason_given` section above.

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
Track B instead. Also note there is no example of skipped non-financial
content — see the "What is NOT investment content" section above for
concrete examples of what to leave out.)

If nothing in the content clears the bar for extraction, return exactly:
```json
[]
```

Content:
