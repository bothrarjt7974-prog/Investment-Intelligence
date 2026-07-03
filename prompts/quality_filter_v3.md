# Prompt: quality_filter_v3

> **Purpose:** Score the reasoning quality of an investment claim on 5
> dimensions and return a verdict of pass / partial / fail.
>
> **Used by:** `src/quality/screener.py`
>
> **Model:** Sonnet (bulk)
>
> **Version history:**
>
> - v1 (initial) — basic 5-dimension scoring
> - v2 — adds explicit rubric examples per dimension, tighter verdict
>   logic, and instruction to use the reason_given field as primary
>   evidence (not the claim_text alone)
> - v3 — loosens ONLY the counter_argument_acknowledgment rubric.
>   Calibration run on real data showed 18/20 claims scoring weak on
>   this dimension because confident experts rarely hedge explicitly.
>   "Strong" now includes implicit trade-off presentation — comparing
>   against alternatives, quantifying the downside case, or framing
>   the thesis against what it competes with. Adds worked Example B
>   demonstrating this. No other rubric changed.
>
> **Calibration target:** Roughly 30-50% pass, 30-50% partial, 10-20%
> fail on Akshat-style dense expert content. If pass exceeds 70%, the
> filter has stopped discriminating — tighten. If pass falls below 15%,
> re-check this dimension first.

---

You are evaluating the **reasoning quality** of an investment claim
extracted from expert content. Your job is to filter out low-reasoning
claims so the downstream intelligence layer only synthesizes well-supported
analysis.

**Important:** You are NOT evaluating whether the claim will turn out
correct. You are evaluating whether the expert showed rigorous thinking
when they made it. A claim can be high-quality reasoning and still be
wrong about the future. A claim can be low-quality reasoning and still
turn out right. We only score the reasoning here. Outcome validation
happens in a separate layer.

---

## Input

**Claim text:** {{claim_text}}
**Claim type:** {{claim_type}}
**Entity:** {{entity}}
**Expert's stated reasoning:** {{reason_given}}

The `reason_given` field is your PRIMARY evidence. It captures the
expert's actual logical chain. The claim_text alone is just the
conclusion — what matters for quality is the reasoning behind it.

---

## Five dimensions to score

For each dimension, return one of: `strong`, `partial`, `weak`.

### 1. reasoning_depth

Does the expert provide a logical chain of reasoning, or just an assertion?

- `strong` — Multi-step causal reasoning. "A leads to B because C, and
  historically D has shown this pattern."
- `partial` — Some reasoning but with gaps. Maybe one logical step is
  shown but the underlying mechanism isn't explained.
- `weak` — Bare assertion. "I think X will happen." No reasoning shown.

### 2. data_support

Does the expert cite specific data, fundamentals, or numbers?

- `strong` — Specific numbers, percentages, ratios, dates, named sources
  ("PE of 22 vs sector PE of 28", "Q3 revenue grew 18%", "10-year average
  is 14%").
- `partial` — General reference to data without specifics ("earnings have
  been growing", "valuations are high").
- `weak` — No data references at all.

### 3. why_explanation

Does the expert explain WHY this conclusion follows, not just WHAT they
conclude?

- `strong` — Explicit causal explanation. "X will happen BECAUSE Y, AND
  Z is the underlying mechanism."
- `partial` — Some why but incomplete. Maybe the immediate cause is given
  but not the deeper reasoning.
- `weak` — Only the what, no why. "Reliance is a buy" with no explanation.

### 4. counter_argument_acknowledgment

Does the expert acknowledge risks, opposing views, or trade-offs —
explicitly OR implicitly?

Confident experts often do not say "the risk is X" out loud. They show
risk awareness by presenting trade-offs: comparing the recommended option
against alternatives, quantifying what the downside looks like, or framing
why this choice beats what it competes with. That counts.

- `strong` — ANY of the following:
  - Explicitly addresses counter-arguments or risks ("this could fail
    if X", "the bear case is Y", "the main risk is Z"), OR
  - Presents the trade-off by comparing against a clear alternative
    ("bonds yield 6-7% post-tax versus 10-12% real inflation, so
    equities despite volatility"), OR
  - Quantifies or characterizes the downside scenario even without
    labeling it a "risk" ("even if growth halves, current valuation
    is covered").
- `partial` — Implicit risk awareness only — hedge words like "could",
  "might", "if conditions hold", OR mentions one downside without
  engaging it.
- `weak` — One-sided confident assertion with no acknowledgment of
  alternatives, downside, or trade-offs at all.

### 5. falsifiability

Is the claim falsifiable — can it be checked against future market outcomes?

- `strong` — Specific testable prediction. Has a directional outcome plus
  either a number or a timeframe ("will rise X% in Y months").
- `partial` — Directional but vague on magnitude or timing.
- `weak` — Unfalsifiable opinion, philosophy, or framework statement
  with no testable outcome.

---

## Overall verdict

After scoring all 5 dimensions, return an overall verdict using this rubric:

- **`pass`** — At least 3 dimensions strong, no more than 1 weak.
  This claim is well-reasoned and worth aggregating into intelligence.

- **`partial`** — Anything between pass and fail. Mixed quality.
  The downstream synthesis can use these but with reduced weight.

- **`fail`** — 3 or more dimensions weak, OR the claim is pure
  unfalsifiable opinion. This claim is too thin to inform
  intelligence and should be excluded from synthesis.

---

## Output format

Return ONLY valid JSON. No prose, no markdown code fences, no commentary.

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

---

## Worked examples

### Example A — clear pass (explicit risk acknowledgment)

claim_text: "Infosys will outperform Nifty IT over next 12 months due
to rupee depreciation tailwind."

reason_given: "INR has fallen 6.2% against USD in last 9 months which
directly boosts dollar-revenue earnings for IT exporters; Infosys
guided 7% revenue growth for FY26 vs sector average 5%; current PE of
22 is below sector PE of 28 providing margin of safety; main risk
remains competitive pressure from TCS and Wipro on large deal wins."

Expected output:

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
  "justification": "Multi-step reasoning with specific data, clear causal logic, explicit risk acknowledgment, and testable 12-month directional prediction."
}
```

### Example B — pass (IMPLICIT trade-off, no explicit hedging)

claim_text: "Indian equities remain the superior long-term allocation
versus fixed income for the next 3-5 years."

reason_given: "Indian bonds yield 6-7% post-tax while real inflation
for the investing class runs 10-12%, meaning fixed income guarantees
negative real returns; Nifty has delivered 12-13% CAGR over any
rolling 10-year window since 2002; even in the worst entry year (2007
peak), 10-year equity returns beat bonds by 4 percentage points
annually."

Note: the expert never says "the risk is..." — but the entire argument
is structured as a trade-off against the alternative (bonds), with the
downside case quantified (worst entry year still wins). That is strong
counter-argument acknowledgment under this rubric.

Expected output:

```json
{
  "scores": {
    "reasoning_depth": "strong",
    "data_support": "strong",
    "why_explanation": "strong",
    "counter_argument_acknowledgment": "strong",
    "falsifiability": "partial"
  },
  "verdict": "pass",
  "justification": "Quantified trade-off framing against the fixed-income alternative including the worst-case scenario, specific data, clear causal logic; slightly vague on testable magnitude."
}
```

### Example C — partial

claim_text: "Indian banking sector looks attractive at current levels."

reason_given: "RBI has cut rates and credit growth is picking up.
Valuations look reasonable."

Expected output:

```json
{
  "scores": {
    "reasoning_depth": "partial",
    "data_support": "weak",
    "why_explanation": "partial",
    "counter_argument_acknowledgment": "weak",
    "falsifiability": "partial"
  },
  "verdict": "partial",
  "justification": "Directional thesis with some reasoning but no specific data, no risk or trade-off framing, and vague on timeframe."
}
```

### Example D — clear fail

claim_text: "Gold should be in everyone's portfolio."

reason_given: "Gold has always been a good store of value."

Expected output:

```json
{
  "scores": {
    "reasoning_depth": "weak",
    "data_support": "weak",
    "why_explanation": "weak",
    "counter_argument_acknowledgment": "weak",
    "falsifiability": "weak"
  },
  "verdict": "fail",
  "justification": "Generic assertion with no specific reasoning, no data, no trade-off framing, and no testable prediction."
}
```

Return the JSON object now.
