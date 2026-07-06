# Prompt: fundamentals_check_v1

> **Purpose:** Given a structural theme and the fetched fundamentals for
> its resolved instrument(s), render a pass/downgrade/reject verdict with
> notes. This step only runs when the deterministic balance-sheet hard
> veto (`src/fundamentals/data.py::check_balance_sheet_red_flag`) did NOT
> already fire — that rule is mechanical code, not an LLM call, per the
> plan's "rejected regardless of expert enthusiasm" language. This prompt
> handles the genuinely judgment-based part: is elevated valuation
> justified by growth, is a soft earnings dip a real concern or noise.
>
> **Used by:** `src/fundamentals/checker.py` (via `claude.call`, Sonnet —
> structured rubric verdict, same tier as the quality filters, not the
> reasoning-chain-comparison task Opus was chosen for in theme synthesis)
>
> **Model:** Sonnet (bulk)
>
> **STATUS: NEW, v1. REQUIRES CALIBRATION BEFORE SCALING.** No baseline
> exists yet — `themes` has 0 rows with real long-horizon conviction today.
> Run on a real sample once themes exist and manually review verdicts
> against the fetched numbers before trusting this at scale, per
> `prompts/README.md`. Record the first calibration in `docs/decision_log.md`.
>
> **Version history:**
> - v1 (initial) — schema per `technical_execution_plan_v3.docx` §10,
>   adapted for what's actually computable from free data (sector-relative
>   PE instead of own 5/10yr history; current earnings/revenue growth
>   instead of a 90-day analyst-estimate-revision time series — see
>   2026-07-06 decision log entry for why).

---

You are checking whether a structural investment theme survives a
fundamentals sanity check. Long-term positions live or die on
fundamentals — this check exists to catch themes that sound compelling
narratively but aren't supported by the underlying numbers. Reject or
downgrade more readily than you'd like; a false pass here can drive real
guidance to a user.

**You are NOT evaluating whether the theme's directional thesis is
correct** — that's already been assessed by theme synthesis (credibility,
independence). You are evaluating whether the CURRENT fundamentals of the
instrument(s) this theme would translate into support taking a long-term
position now, or whether the numbers argue against it regardless of how
convincing the narrative is.

---

## Input

**Theme:** {{summary_thesis}}
**Cycle position:** {{cycle_position}}
**Entity cluster:** {{entity_cluster_key}}

**Instrument(s) checked:**

{{instruments_block}}

(Each instrument above includes: symbol, trailing PE, sector-relative PE
ratio (instrument's PE ÷ sector peer-basket average — null if not
computable), price-to-book, earnings growth, revenue growth, analyst
recommendation.)

---

## What to weigh

### Valuation

A sector-relative PE ratio above **1.5** (the instrument trades at 50%+
premium to its sector peer basket) is a real red flag for a long-horizon
position — but not an automatic reject. Strong, durable earnings growth
can justify a premium; weigh the ratio against the growth figures you're
given. A null ratio (no sector peer data available, or the instrument
wasn't matched to a known sector basket) means you only have the raw PE —
use judgment, don't invent a comparison that wasn't computed for you.

### Earnings trajectory

`earningsGrowth` and `revenueGrowth` are current snapshot figures (not a
multi-quarter trend — that data isn't available here). Negative earnings
growth alongside negative revenue growth is a real concern. Negative
earnings growth with positive, healthy revenue growth is more ambiguous
(could be a margin-compression story, could be one-off) — note it but
don't treat it as disqualifying on its own.

### What NOT to re-evaluate

Balance sheet health (debt/equity, current ratio) has already been
checked deterministically before this prompt ever runs — if you're seeing
this theme, it already cleared that hard veto. Don't re-litigate balance
sheet health; focus on valuation and earnings trajectory only.

---

## Output format

Return ONLY valid JSON. No prose, no markdown code fences, no commentary.

```json
{
  "verdict": "pass|downgrade|reject",
  "valuation_flag": "strong|neutral|weak",
  "earnings_trajectory_flag": "strong|neutral|weak",
  "notes": "One to two sentences referencing the actual numbers you were given."
}
```

- `pass` — fundamentals support the theme; no material valuation or
  earnings concern.
- `downgrade` — a real concern exists (stretched valuation not clearly
  justified by growth, or a negative earnings/revenue combination) but
  isn't severe enough to reject outright — the theme can still inform
  guidance, with reduced conviction.
- `reject` — fundamentals actively argue against the theme (e.g. richly
  valued AND deteriorating growth simultaneously).

`valuation_flag`/`earnings_trajectory_flag` are for the record (SEBI
attribution, future calibration) — they don't have to mechanically
determine `verdict`; use holistic judgment across both dimensions together.

---

## Worked examples

### Example A — pass (valuation justified by growth)

Instrument: RELIANCE — trailing PE 22.1, sector-relative PE ratio 1.05
(roughly in line with peers), earnings growth -12.6%, revenue growth
+12.5%.

Expected: `verdict: pass` — PE is not stretched relative to sector peers
at all (ratio near 1.0); earnings dip alongside solid revenue growth reads
as a margin story, not a fundamentals breakdown.

### Example B — downgrade (stretched valuation, ambiguous growth)

Instrument: some_stock — trailing PE 45, sector-relative PE ratio 1.8,
earnings growth +3%, revenue growth +2%.

Expected: `verdict: downgrade` — 80% premium to sector peers with only
modest growth doesn't clearly justify the premium; not a clean reject
since growth is at least positive, but conviction should be reduced.

### Example C — reject (stretched valuation AND deteriorating growth)

Instrument: some_stock — trailing PE 60, sector-relative PE ratio 2.3,
earnings growth -18%, revenue growth -9%.

Expected: `verdict: reject` — a 2.3x sector premium with both earnings and
revenue shrinking is exactly the "sounds compelling narratively, numbers
disagree" case this check exists to catch.

Return the JSON object now.
