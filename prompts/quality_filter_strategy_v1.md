# Prompt: quality_filter_strategy_v1

> **Purpose:** Score the specificity and completeness of an investment
> strategy on 5 dimensions and return a verdict of pass / partial / fail.
> Mirrors `quality_filter_v3` (claims), but strategies are judged on
> **actionability**, not directional reasoning — a strategy can be
> well-specified and still turn out to underperform; that's what
> backtesting (Layer 4 Track B) checks, not this filter.
>
> **Used by:** `src/quality/strategy_screener.py`
>
> **Model:** Sonnet (bulk)
>
> **STATUS: NEW, v1. REQUIRES CALIBRATION BEFORE SCALING.** Run on a
> 20-30 strategy held-out sample (once `strategy_extraction_v1` has
> produced real rows to screen) before trusting the pass rate at scale,
> same process as `quality_filter_v3`. No baseline distribution exists
> yet — record the first calibration result in `docs/decision_log.md`.
>
> **Version history:**
> - v1 (initial) — 5-dimension scoring per `technical_execution_plan_v3.docx` §7.

---

You are evaluating the **specificity and completeness** of an investment
strategy extracted from expert content. Your job is to filter out vague,
unimplementable methodology so the strategy library only accumulates
strategies specific enough to actually execute and, eventually, backtest.

**Important:** You are NOT evaluating whether this strategy would have
made money historically. You are evaluating whether it is stated
specifically enough to be implemented and tested at all. A vague strategy
("buy quality companies") could theoretically be right, but there's
nothing concrete enough here to act on or backtest — that's what makes it
low quality for this filter, regardless of the underlying idea's merit.

---

## Input

**Strategy text:** {{strategy_text}}
**Strategy type:** {{strategy_type}}
**Entity class:** {{entity_class}}
**Trigger conditions:** {{trigger_conditions}}
**Allocation rule:** {{allocation_rule}}
**Exit conditions:** {{exit_conditions}}
**Expert's stated reasoning:** {{reason_given}}

---

## Five dimensions to score

For each dimension, return one of: `strong`, `partial`, `weak`.

### 1. trigger_specificity

Is the applicability condition explicit and recognizable as a specific,
detectable market state?

- `strong` — A named, detectable regime or condition. "In rate-cut cycles", "when sector PE exceeds its 5-year average by 30%", "on a 20%+ drawdown from peak".
- `partial` — A vague qualitative condition. "When things look attractive", "in uncertain times".
- `weak` — No trigger at all, or a trigger so generic it never fails to apply ("always", stated with no rationale for when it wouldn't apply — note: a genuinely standing rule like "always keep 6 months expenses liquid" scores `partial` here, not `weak`, since the always-on nature is itself a specific, recognizable condition).

### 2. quantification

Are allocations, sizes, and thresholds expressed as numbers?

- `strong` — Specific percentages, ranges, or numeric thresholds. "40%, weighted by market cap", "trim at +30% vs. 200-day trend".
- `partial` — Some numbers present but with a vague qualifier. "A decent chunk, maybe 20-30%".
- `weak` — No numbers at all. "A meaningful allocation", "size it appropriately".

### 3. completeness

Does the strategy cover entry AND hold AND exit, or is it a narrow
single-stage rule that's still fully specified for its own stage?

- `strong` — Covers entry, holding period, AND exit condition, OR is a
  narrow single-stage strategy (e.g. `position_sizing` type) that fully
  specifies that one stage with no gaps.
- `partial` — Covers some stages but leaves at least one materially unaddressed (e.g. entry and allocation stated, but no exit condition at all for a strategy type where an exit would be expected).
- `weak` — Only one vague element present; most of the lifecycle is unaddressed.

### 4. scale_invariance

Does the rule work at ₹10 lakh and at ₹10 crore alike?

- `strong` — Percentage- or ratio-based. "40% of equity allocation."
- `partial` — Mixed — mostly percentage-based but with one fixed-rupee caveat.
- `weak` — Stated entirely in fixed rupee/dollar amounts that don't scale ("put ₹50,000 into this").

### 5. failure_awareness

Does the expert state when this strategy breaks, should be abandoned, or
what its failure mode looks like?

- `strong` — Explicit failure/abandonment condition. "Never add on a 50%+ drawdown — treat that as thesis invalidation."
- `partial` — Implicit risk acknowledgment only, e.g. a soft caveat without a concrete abandonment rule.
- `weak` — No acknowledgment that this strategy could fail or need abandoning.

---

## Overall verdict

After scoring all 5 dimensions, return an overall verdict using this rubric
(identical structure to the claim quality filter):

- **`pass`** — At least 3 dimensions strong, no more than 1 weak. This
  strategy is specific enough to enter the strategy library and eventually
  be backtested.
- **`partial`** — Anything between pass and fail. Usable with reduced
  weight once promoted (per the same partial-verdict convention as claims —
  see the 2026-07-04 decision log entry).
- **`fail`** — 3 or more dimensions weak. Too vague to implement or test;
  retained only for expert credibility signal, never promoted to the
  library.

---

## Output format

Return ONLY valid JSON. No prose, no markdown code fences, no commentary.

```json
{
  "scores": {
    "trigger_specificity": "strong|partial|weak",
    "quantification": "strong|partial|weak",
    "completeness": "strong|partial|weak",
    "scale_invariance": "strong|partial|weak",
    "failure_awareness": "strong|partial|weak"
  },
  "verdict": "pass|partial|fail",
  "justification": "One sentence explaining the verdict."
}
```

---

## Worked examples

### Example A — clear pass

strategy_text: "In rate-cut cycles, allocate up to 40% of equity to PSU
banks weighted by market cap, hold minimum 18 months, trim if the sector
runs 30% above its 200-day trend."

trigger_conditions: `{"type": "rate_cut_cycle", ...}`
allocation_rule: "Up to 40% of equity allocation, weighted by market cap"
exit_conditions: "Trim if the sector runs 30% above its 200-day trend"

Expected output:

```json
{
  "scores": {
    "trigger_specificity": "strong",
    "quantification": "strong",
    "completeness": "strong",
    "scale_invariance": "strong",
    "failure_awareness": "partial"
  },
  "verdict": "pass",
  "justification": "Fully quantified, percentage-based allocation with an explicit regime trigger and exit condition; only lacks an explicit abandonment rule beyond the trim trigger."
}
```

### Example B — partial (vague sizing, no exit)

strategy_text: "When valuations look stretched, trim your smallcap
exposure and add to largecaps instead."

trigger_conditions: "when valuations look stretched"
allocation_rule: null

Expected output:

```json
{
  "scores": {
    "trigger_specificity": "partial",
    "quantification": "weak",
    "completeness": "partial",
    "scale_invariance": "partial",
    "failure_awareness": "weak"
  },
  "verdict": "partial",
  "justification": "Directionally clear rebalancing idea but no numeric sizing, no explicit exit/re-entry condition, and a qualitative rather than measurable trigger."
}
```

### Example C — clear fail

strategy_text: "Buy good companies and hold for the long term."

trigger_conditions: null
allocation_rule: null
exit_conditions: null

Expected output:

```json
{
  "scores": {
    "trigger_specificity": "weak",
    "quantification": "weak",
    "completeness": "weak",
    "scale_invariance": "weak",
    "failure_awareness": "weak"
  },
  "verdict": "fail",
  "justification": "Generic buy-and-hold sentiment with no trigger, no sizing, no exit condition, and nothing implementable or backtestable."
}
```

Return the JSON object now.
