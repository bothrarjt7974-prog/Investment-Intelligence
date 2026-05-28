# Prompt: quality_filter_v1

> **Purpose:** Score an investment claim on 5 dimensions of reasoning quality
> and return a pass/partial/fail verdict.
>
> **Used by:** `src/quality/screener.py`
>
> **Model:** Sonnet (bulk)
>
> **Version history:**
> - v1 (initial) — 5-dimension scoring with overall verdict
>
> **Calibration notes:** Aim for ~30–40% pass, ~30% partial, ~30% fail across
> a representative sample. Adjust prompt if rates drift significantly.

---

You are evaluating the reasoning quality of an investment claim made by an
expert. Your job is to filter out low-quality claims so only well-reasoned
insights flow into our intelligence layer.

**Claim text:** {{claim_text}}
**Expert reasoning:** {{reason_given}}
**Claim type:** {{claim_type}}

Score the claim on these 5 dimensions. For each, return one of: `strong`,
`partial`, `weak`.

1. **reasoning_depth** — Does the expert provide a logical chain of reasoning,
   or just an assertion? `strong` = clear multi-step reasoning. `partial` =
   some reasoning but gaps. `weak` = bare assertion.

2. **data_support** — Does the expert cite data, sources, fundamentals, or
   numbers? `strong` = specific data points cited. `partial` = general
   reference to data. `weak` = no data referenced.

3. **why_explanation** — Does the expert explain WHY this view holds, not just
   WHAT they think? `strong` = explicit causal explanation. `partial` = some
   why but incomplete. `weak` = only what, no why.

4. **counter_argument_acknowledgment** — Does the expert acknowledge risks or
   the opposing view? `strong` = explicitly addresses counter-arguments.
   `partial` = brief mention of risks. `weak` = one-sided, no acknowledgment.

5. **falsifiability** — Is the claim falsifiable — can it be checked against
   future market outcomes? `strong` = specific testable prediction.
   `partial` = directional but vague. `weak` = unfalsifiable opinion.

## Overall verdict

After scoring, return an overall verdict:

- `pass` = 3+ dimensions `strong`, 0 `weak`. Claim enters the active intelligence pool.
- `partial` = mixed but mostly `strong`/`partial`. Claim enters but with lower weight.
- `fail` = 2+ dimensions `weak`, OR claim is non-falsifiable opinion only.
  Claim is excluded from synthesis.

## Output format

Return ONLY valid JSON. No prose before or after.

```json
{
  "scores": {
    "reasoning_depth": "strong|partial|weak",
    "data_support": "strong|partial|weak",
    "why_explanation": "strong|partial|weak",
    "counter_argument_acknowledgment": "strong|partial|weak",
    "falsifiability": "strong|partial|weak"
  },
  "verdict": "pass|partial|fail",
  "justification": "One sentence explaining the verdict."
}
```

## Example

For a claim "Infosys will outperform Nifty IT next year because INR
depreciation boosts dollar earnings, the company guided 7% revenue growth,
and current PE of 22 is below sector average — though competitive pressure
from TCS and Wipro remains a risk":

```json
{
  "scores": {
    "reasoning_depth": "strong",
    "data_support": "strong",
    "why_explanation": "strong",
    "counter_argument_acknowledgment": "strong",
    "falsifiability": "strong"
  },
  "verdict": "pass",
  "justification": "Multi-step reasoning with specific data, clear causal logic, acknowledges competitive risk, and produces a testable directional prediction."
}
```

Content:
