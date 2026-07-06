# Prompt: theme_synthesis_v1

> **Purpose:** Given a cluster of quality-passed, long-horizon claims (all
> about the same canonical entity, from possibly multiple experts and
> content pieces), classify each claim's reasoning independence relative to
> the others in the cluster, synthesize a structural thesis, assess cycle
> position, and surface dissenting views. This is the reasoning-quality-
> matters-most step in the pipeline — five experts agreeing means little if
> they all read the same Bloomberg piece, so the independence classification
> here is what makes or breaks the theme's eventual conviction level.
>
> **Used by:** `src/synthesis/theme_synthesis.py` (via `claude.synthesize`,
> Opus model — reasoning quality matters more than cost here)
>
> **Model:** Opus
>
> **STATUS: NEW, v1. REQUIRES CALIBRATION BEFORE SCALING.** No baseline
> exists yet. Run on real clusters once enough quality-passed long-horizon
> claims exist (currently near-zero — filter calibration is still the
> upstream gate) and manually review independence labels against the source
> claims before trusting this at scale, per `prompts/README.md`. With a
> single expert in the system today (Akshat), every cluster only has one
> voice — independence classification is trivial (nothing to compare
> against) until the expert roster grows. Record the first real calibration
> in `docs/decision_log.md`.
>
> **Version history:**
> - v1 (initial) — schema per `technical_execution_plan_v3.docx` §9.

---

You are analyzing a cluster of investment claims that are all about the
**same underlying entity** ({{entity_cluster_key}}), made by one or more
credible financial experts. Your job has two parts:

**Part 1 — Independence classification.** For each claim, decide whether
its reasoning is independent of, correlated with, or an echo of the other
claims in this cluster.

- `independent` — The expert built their own reasoning chain: distinct
  data points, a different analytical angle, or a conclusion reached via a
  different mechanism than the others in the cluster, even if they land on
  the same directional view.
- `correlated` — The expert's reasoning overlaps meaningfully with another
  claim's reasoning (shares a specific data point, a named event, or a
  framework) but adds some distinct element of their own.
- `echo` — The expert's reasoning is substantively the same fact/argument
  as another claim in the cluster, restated — no independent analytical
  contribution. This is the case that most needs catching: five experts
  citing the identical Fed announcement or the identical viral chart is one
  signal, not five.

**Two claims from the SAME expert are never "independent" of each other by
definition — if the same expert appears twice in a cluster, treat their
later claim as correlated or echo relative to their own earlier one**,
never independent (an expert can't corroborate themselves).

**Part 2 — Thesis synthesis.** Across the whole cluster, produce:

- `summary_thesis` — One or two sentences: what is the collective,
  synthesized directional thesis for this entity, reflecting genuine
  agreement (not just the majority claim's wording)? If the cluster is a
  single claim, the thesis is essentially that claim, cleaned up.
- `cycle_position` — Where in a market/economic cycle this entity currently
  sits, if the claims collectively suggest one (e.g. "early re-rating
  phase", "late-cycle, valuation-extended", "post-correction recovery").
  Use `null` if the claims don't collectively support a cycle-position
  read — do not invent one.
- `dissenting_views` — If any claim in the cluster meaningfully disagrees in
  direction with the majority (a bearish claim in an otherwise bullish
  cluster, or vice versa), summarize the dissent and its stated reasoning in
  1-2 sentences. `null` if the cluster is directionally unanimous.
- `instruments_mentioned` — Specific named instruments (tickers, funds,
  named indices) referenced across the claims' reasoning, beyond just the
  cluster's grouping entity itself. Use the same canonical naming rules as
  claim extraction (uppercase tickers, lowercase_with_underscores
  indices/sectors). Empty array if none are named beyond the entity itself.

---

## Input

**Entity:** {{entity_cluster_key}}
**Claims in this cluster:**

{{claims_block}}

(Each claim above includes: claim_id, expert_name, claim_text, reason_given,
direction, target_horizon_months, claimed_at.)

---

## Output format

Return ONLY valid JSON. No prose, no markdown code fences, no commentary.

```json
{
  "claim_independence": [
    {"claim_id": "<uuid>", "independence_label": "independent|correlated|echo"}
  ],
  "summary_thesis": "...",
  "cycle_position": "..." ,
  "dissenting_views": "...",
  "instruments_mentioned": ["..."]
}
```

`claim_independence` must include exactly one entry per claim_id in the
input, in any order.

Never mark the only claim in a single-claim cluster as anything but
`independent` — there is nothing to correlate or echo against.

---

## Worked examples

### Example A — independent-conviction cluster

Claims (entity: `indian_banking`):
1. Expert A: "PSU banks will re-rate over the next 18 months as credit
   growth accelerates" — reasoning cites Q3 credit growth data (14% YoY) and
   NPA ratios falling to a 10-year low.
2. Expert B: "Indian banking sector offers the best risk-adjusted return in
   the market over 2 years" — reasoning cites valuation gap vs historical
   PE band and RBI's stated durable-liquidity stance, no mention of credit
   growth data.
3. Expert C: "Private banks will outperform PSU banks, but the sector as a
   whole re-rates over 18-24 months" — reasoning cites a different
   mechanism (deposit market share shift), reaching a similar directional
   conclusion via distinct logic.

Expected: all three `independent` (three different mechanisms — credit
growth, valuation-vs-history, deposit share shift — converging on the same
direction is real signal, not an echo).

### Example B — echo cluster

Claims (entity: `gold`):
1. Expert A: "Gold rallies as the Fed cuts rates in September" — reasoning
   is entirely the Fed's September rate-cut announcement.
2. Expert B: "Gold set to rise on the September Fed rate cut" — reasoning is
   the same Fed announcement, same causal mechanism, no additional analysis.
3. Expert C: "With the Fed cutting in September, gold should benefit" —
   same event, same mechanism, restated a third time.

Expected: the first claim chronologically is `independent` (or `correlated`
if the LLM cannot establish which came first); the second and third are
`echo` — genuinely the same fact restated with no distinct analytical
contribution. `summary_thesis` should reflect this is one real signal (the
Fed decision), not three independent theses.

Return the JSON object now.
