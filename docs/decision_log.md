# Decision Log

A running log of meaningful product and architecture decisions.
Every entry follows the same structure. Add new entries at the top.

---

## 2026-05-26 — Quality filter pass rate target

**Decision:** Calibrate the quality filter to roughly 30–40% pass, 30% partial,
30% fail across a representative sample of claims.

**Context:** Building the v1 quality filter. Need a target distribution
to calibrate against.

**Options considered:**
- 60–70% pass (too permissive, defeats the filter's purpose)
- 30–40% pass (aggressive filtering, ensures only high-quality claims flow through)
- 10–15% pass (too strict, kills signal even from credible experts)

**Choice:** 30–40% pass.

**Reasoning:** The product's value depends on aggressive filtering. Even
credible experts produce some unsupported claims in long-form content.
Filtering at 30–40% leaves enough signal while preserving the "rigorous
synthesis" promise to users.

**What would change this:** If empirical testing shows we're rejecting
claims that would have been useful (e.g. macro context claims that are
hard to falsify but valuable for understanding), we may add a separate
"context" track that has looser quality criteria.

**Revisit:** 2026-08-26 — after 8 weeks of running on real data.

---

## 2026-05-26 — Sonnet for bulk, Opus for synthesis

**Decision:** Use Claude Sonnet for bulk claim extraction and classification.
Use Claude Opus only for final synthesis steps (briefing generation,
reasoning chain comparison).

**Context:** Cost vs quality tradeoff on Claude API.

**Options considered:**
- Opus for everything (highest quality, highest cost)
- Sonnet for everything (lower cost, possibly worse on complex synthesis)
- Mixed approach (Sonnet bulk, Opus synthesis)

**Choice:** Mixed approach.

**Reasoning:** Claim extraction is structured and well-defined — Sonnet
handles it reliably. Synthesis involves comparing reasoning chains and
producing nuanced narrative output where Opus's depth matters. Routing
helps keep monthly API costs in the ₹3–6k range during MVP phase.

**What would change this:** If Sonnet's extraction quality drops below
acceptable threshold (e.g. mis-classification rate above 15%), upgrade
to Opus for extraction too.

**Revisit:** 2026-07-26 — after 2 months of cost and quality data.

---

## 2026-05-26 — Starting expert universe of 10

**Decision:** Begin with exactly 10 experts (7 Indian, 3 US YouTube channels).

**Context:** Need to scope the MVP narrowly enough to maintain quality.

**Options considered:**
- 5 experts (too narrow, not enough convergence signal)
- 10 experts (balance of depth and coverage)
- 25+ experts (too broad, dilutes quality control)

**Choice:** 10 experts.

**Reasoning:** Depth of accuracy on fewer sources beats shallow coverage
of many. 10 experts produces enough diversity for convergence detection
while keeping daily ingestion manageable and quality calibration tight.

**What would change this:** Once the pipeline is stable for 3+ months
and quality calibration is reliable, expand to 20–25 experts.

**Revisit:** 2026-09-26.

---

<!-- Template for new entries:

## YYYY-MM-DD — Short title

**Decision:** One sentence.

**Context:** What triggered this decision.

**Options considered:**
- Option A
- Option B

**Choice:** What you chose.

**Reasoning:** Why.

**What would change this:** What evidence would make you revise.

**Revisit:** When to check this decision still holds.

-->
