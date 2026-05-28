# Prompt: claim_extraction_v1

> **Purpose:** Extract every distinct investment claim from a piece of expert
> content and return structured JSON.
>
> **Used by:** `src/extraction/extractor.py`
>
> **Model:** Sonnet (bulk)
>
> **Version history:**
> - v1 (initial) — basic extraction with 8-field schema

---

You are analyzing investment content from a credible financial expert.

**Expert:** {{expert_name}}
**Source:** {{source_type}}
**Date:** {{published_at}}

Your task: identify every distinct investment claim or insight in the content
below. A "claim" is any statement where the expert is asserting something
about markets, an asset, an economic condition, or a strategy.

For each claim, return a JSON object with these EXACT fields:

- `claim_text`: The specific testable form of the claim in ONE sentence.
  Make it specific and verifiable. Example of good: "Infosys will outperform
  Nifty IT index over next 12 months due to rupee depreciation tailwind."
  Example of bad: "He says Infosys is good."

- `type`: One of: `macro`, `sector`, `stock_pick`, `strategy`, `overview`
  - `macro` = about global economy, currencies, rates, geopolitics
  - `sector` = about an industry (IT, banking, pharma, etc.)
  - `stock_pick` = about a specific company
  - `strategy` = about allocation, portfolio construction, frameworks
  - `overview` = context/education without a directional view

- `geography`: One of: `india`, `us`, `global`, `emerging_markets`

- `timeframe`: One of: `short` (under 1 year), `medium` (1–3 years), `long` (3+ years)

- `strength`: One of: `strong`, `soft`, `opinion`
  - `strong` = specific prediction with numbers or clear direction
  - `soft` = directional but vague
  - `opinion` = commentary without a falsifiable prediction

- `entity`: The specific stock ticker, sector name, or macro factor.
  For Indian stocks use NSE ticker (e.g., "RELIANCE", "INFY").
  For US stocks use ticker (e.g., "AAPL"). For sectors use a standard name
  ("indian_banking", "us_tech"). For macro use a factor name ("inr_usd", "fed_rate").
  Use `null` for `overview` and `strategy` types.

- `direction`: One of: `bullish`, `bearish`, `neutral`, `na`
  Use `na` for `overview` and `strategy` types.

- `reason_given`: A 1–2 sentence summary of the expert's REASONING for the
  claim, in your own words. Capture WHY they think this, not just WHAT.
  This is essential for convergence detection later.

## Critical instructions

1. Return ONLY a valid JSON array. No prose before or after.
2. If a claim is hedged ("could", "might", "I'd be cautious"), still extract
   it but mark `strength: soft`.
3. If a statement is purely educational with no view, mark `type: overview`.
4. Skip pleasantries, intros, and sponsor mentions.
5. If the same claim is repeated, include it only once.
6. Aim for 5–15 claims per piece. If you find fewer than 3 distinct claims,
   the content is mostly conversational — return what you find.

## Example output format

```json
[
  {
    "claim_text": "INR will continue depreciating against USD over the next 6 months due to widening current account deficit.",
    "type": "macro",
    "geography": "india",
    "timeframe": "short",
    "strength": "strong",
    "entity": "inr_usd",
    "direction": "bearish",
    "reason_given": "Current account deficit widening because of oil imports, and Fed unlikely to cut rates soon which keeps dollar strong."
  }
]
```

Content:
