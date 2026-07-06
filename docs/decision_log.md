# Decision Log

A running log of meaningful product and architecture decisions.
Every entry follows the same structure. Add new entries at the top.

---

## 2026-07-06 — Layer 7: fundamentals sanity check built

**Decision:** Build the fundamentals check per `technical_execution_plan_v3.docx`
§10: `migrations/008_fundamentals.sql` (inline verdict columns on `themes`,
not a new table — 1:1 relationship, same convention as
`claims.quality_verdict`), `src/fundamentals/data.py` (instrument
resolution, yfinance snapshot fetching, sector-relative valuation,
deterministic balance-sheet veto), `prompts/fundamentals_check_v1.md`
(Sonnet, new), `src/fundamentals/checker.py` (orchestration).

**I tested what's actually free before designing this** (ran yfinance
directly against real tickers): current-snapshot fundamentals (trailing
PE, P/B, debt/equity, current ratio, earnings/revenue growth, analyst
recommendation) are available for Indian and US large-caps, no new
dependency. Two things the plan literally asks for are NOT available for
free, so v1 substitutes the closest computable analogue:

1. **Valuation: sector-relative, not own 5/10yr history.** No free
   historical PE time series exists. Confirmed with user before building.
   I also verified NSE sector indices (`^NSEBANK`, `^CNXIT`, `^NSEI`) don't
   expose PE via yfinance at all (empirically checked — all `None`; only US
   sector ETFs like `XLK` do), so a real sector-relative check needed a
   small curated peer-ticker basket (`SECTOR_PEER_TICKERS` in
   `src/fundamentals/data.py`, same curation spirit as `ENTITY_SYMBOL_MAP`
   — a v1 seed list, not exhaustive) rather than a single index lookup.
   The plan's "1.5x historical average is a red flag" threshold is reused
   as 1.5x sector-peer average.
2. **Earnings trajectory: current growth sign, not a 90-day estimate-
   revision time series.** "Three consecutive analyst estimate cuts" needs
   historical estimate data not available for free; v1 uses current
   `earningsGrowth`/`revenueGrowth` as the closest available proxy.

**Instrument resolution reuses theme_synthesis's own output** —
`instruments_mentioned` (already populated by `theme_synthesis_v1`) is
checked first; falls back to `entity_cluster_key` itself if it's
ticker-shaped; otherwise (a sector/index/macro-level theme with nothing
named) the verdict is `not_applicable`, never a fabricated opinion about
unnamed instruments.

**Balance sheet health is a deterministic hard veto in code, not an LLM
judgment call** — the plan says "rejected regardless of expert
enthusiasm," which is a mechanical rule (`debtToEquity > 200 AND
currentRatio < 1.0`, both must be present) enforced in
`src/fundamentals/data.py::check_balance_sheet_red_flag` before the LLM is
even called. Valuation and earnings trajectory ARE LLM judgment calls on
top of the code-computed numbers (Sonnet, not Opus — structured rubric
verdict like the quality filters, not reasoning-chain comparison).

**Bug found and fixed while building this:** `normalization.TICKER_PATTERN`
capped ticker recognition at 8 characters, but 6 real tickers already in
`ENTITY_SYMBOL_MAP` are 9-10 characters (ICICIBANK, KOTAKBANK, ASIANPAINT,
HINDUNILVR, BAJFINANCE, TATAMOTORS) — `normalize_entity` was silently
lowercasing these instead of uppercasing them, which would have made them
unresolvable via `entity_symbol_map.py`'s case-sensitive exact-match
lookup whenever they arrived from an LLM in lowercase. Widened to 10 chars.
This affected Track A/B normalization too, not just this layer — a
pre-existing latent bug, not something introduced by fundamentals work,
just surfaced by it.

**Revisit:** once real themes exist to check (0 today) and
`fundamentals_check_v1` gets its first calibration run. Revisit
`SECTOR_PEER_TICKERS` coverage as themes surface sectors not yet in the
seed list. Revisit the earnings-trajectory proxy and 1.5x threshold once
enough real verdicts accumulate to compare against actual outcomes.

---

## 2026-07-06 — Layer 6: structural theme synthesis built

**Decision:** Build theme synthesis per `technical_execution_plan_v3.docx`
§9: `migrations/007_themes.sql` (new `themes` table), `prompts/theme_synthesis_v1.md`
(Opus, new), `src/internal/theme_scoring.py` (convergence/credibility
weighting — proprietary, see below), `src/synthesis/theme_synthesis.py`
(clustering + orchestration). Built now even with a single expert in the
system (Akshat) and near-zero quality-passed long-horizon claims —
same "build ahead of data" pattern as Track A validation and Track B
backtesting: the code is general for the multi-expert case the layer
exists for, and sits idle until the expert roster and claim volume justify
it.

**Three scoped judgment calls:**

1. **Clustering is deterministic (by canonical `entity`), not embedding-
   based.** The plan's long-term vision uses OpenAI embeddings + Chroma for
   semantic theme clustering, but neither is wired into this codebase yet
   (config fields exist in `settings.py`; nothing ingests embeddings), and
   the plan itself schedules that integration at Layer 7 (matcher), not
   here. v1 produces narrower per-entity/sector themes (a "US tech" theme
   stays separate from a "GOOGL" theme) rather than broad cross-entity
   narratives. **Revisit:** once Chroma is actually stood up for Layer 7 —
   at that point clustering could adopt the same embedding infrastructure.

2. **Convergence scoring lives in `src/internal/theme_scoring.py`,
   proprietary.** `src/internal/README.md` already lists "Convergence
   detection scoring rules" as moat logic belonging in that directory
   alongside `scoring.py`'s credibility formula — this is exactly that.
   The LLM classifies each claim's reasoning as independent/correlated/echo
   relative to its cluster; the weighting arithmetic (echo claims collapse
   into ONE signal regardless of how many experts echo, per the plan's own
   §9 spec) and the conviction-level thresholds (HIGH: effective signal
   count >= 3 AND credibility-weighted score >= 65; MEDIUM: >= 2 AND >= 50;
   a single-contributing-expert cluster is always LOW regardless of that
   expert's credibility) are v1 placeholders, same "documented for
   calibration" status as every other threshold in this codebase.

3. **Claims with `entity IS NULL` are out of scope for v1 clustering** —
   pure macro/opinion claims with no groupable subject can't be clustered
   deterministically. Logged, not silently dropped. Revisit if this proves
   to exclude a meaningful share of otherwise-synthesizable claims.

**What would change this:** Once 5-10 experts are active and real
quality-passed long-horizon claims accumulate, recalibrate the conviction
thresholds and clustering approach against real convergence patterns
rather than the single-Akshat baseline this was built against.

**Revisit:** once the expert roster expands past 1 (multi-expert
convergence is the entire point of this layer) and once `theme_synthesis_v1`
gets its first real calibration run.

---

## 2026-07-06 — Layer 4 Track B: strategy backtesting engine built

**Decision:** Build the strategy backtesting engine per
`technical_execution_plan_v3.docx` §8: `src/validation/trigger_vocabulary.py`
(detector registry) + `src/validation/backtest.py` (simulation harness),
writing `backtest_record`/`backtest_status` on the `strategies` table. Built
now even though `strategies` has 0 rows (Track B extraction not yet
calibrated/run) — same "build ahead of data" pattern as Track A validation.

**Four scoped judgment calls, recorded for future-you:**

1. **Only 5 of 7 trigger types have detectors.**
   `rate_cut_cycle`/`rate_hike_cycle` (RBI/Fed manual CSVs), `drawdown_threshold`
   and `currency_regime` (market_data price series), and `always_on` (stride
   sampling) are implemented. `valuation_extreme_cheap`/`valuation_extreme_rich`
   are deliberately NOT implemented — they need historical PE/valuation data
   we don't ingest anywhere (Layer 6 fundamentals, not built). A strategy
   with one of those two trigger types gets `backtest_status='insufficient_history'`
   with an explicit reason, never a technical proxy (e.g. price-vs-moving-
   average) standing in for a fundamentals concept the expert didn't state.
   **Revisit:** once Layer 6 fundamentals data is ingested.

2. **Exit rule is time-based only.** No parsing of `exit_conditions` free
   text into a programmatic exit signal — every simulated window holds for
   exactly `holding_horizon_months` (default 12 if unstated), then exits.
   Trigger detection is already the hard part of Track B per the plan's own
   "honest engineering note"; exit-condition NLP is separate scope.
   **Revisit:** if backtest results look systematically wrong because real
   exits would have fired earlier/later than the fixed horizon.

3. **Benchmark = single buy-and-hold CAGR over the instrument's full
   available history, scaled to the strategy's horizon** — not a sampled
   distribution of random entry-point windows. Simpler, avoids an
   additional "what counts as random" design fork, and directly answers the
   plan's literal ask ("comparison vs buy-and-hold benchmark").

4. **India-default geography heuristic.** Strategies have no `geography`
   column. Rate-cycle detectors use `rbi_rate` unless the resolved
   fetch_symbol is unambiguously non-Indian (no `.NS` suffix); `currency_regime`
   hardcodes `inr_usd` regardless of the strategy's own entity. Reasonable
   given the MVP is single-expert (Akshat), India-focused content.
   **Revisit:** once non-India strategies actually appear (multi-expert
   expansion).

**Trigger vocabulary lives in code (`TRIGGER_DETECTORS` dict), not a DB
table**, despite the plan's tech-stack section saying it should "live in
the database, versioned" — this repo already has the equivalent precedent
in `ENTITY_SYMBOL_MAP` (`src/validation/entity_symbol_map.py`), a Python
dict for the same reason: a trigger type is meaningless without its
detector function, and functions can't live in a DB row.

**Revisit:** after the first real strategies pass Filter B and get
backtested — check whether the 90-day episode-gap dedup and the 20%/5%
thresholds produce sensible window counts on real data, not just synthetic
test series.

---

## 2026-07-06 — Phase B kickoff: Track B strategy extraction built; expert_sources deferred to Phase H

**Decision:** Build Track B (strategy extraction) per `technical_execution_plan_v3.docx`
§5: new `strategies` table (migration 006), `prompts/strategy_extraction_v1.md`,
`src/extraction/strategy_extractor.py`, `normalize_strategies()` in
`src/extraction/normalization.py`, Filter B (`prompts/quality_filter_strategy_v1.md`
+ `src/quality/strategy_screener.py`). Also activate `claim_extraction_v3` in
`src/extraction/extractor.py` in the same change, per that prompt's own header
instruction ("activate in the same change as strategy_extraction_v1").

**What was explicitly deferred:** The v3 plan bundles a second migration into
Phase B — the `expert_sources` multi-source model (renaming
`content_pieces.expert_id`, splitting `processed_at` into
`claims_processed_at`/`strategies_processed_at` at the expert_sources level).
That refactor is NOT done. Instead, Track B's own processing cursor
(`strategies_processed_at`/`strategies_processing_error`) was added directly
to `content_pieces`, independent of the expert_sources model.

**Context:** The project is still single-expert (Akshat), single-source
(YouTube only) — Pattern 2/3 (blogs, podcasts) don't activate until Phase H.
The expert_sources refactor's entire benefit (per-medium credibility,
multi-source ingestion) doesn't exist until a second source type is added.
Renaming a column on a live table and touching working ingestion code
(`src/ingestion/youtube.py`, `src/ingestion/run_all.py`) and the Track A
extractor for a benefit that isn't real yet was judged unnecessary risk.

**Options considered:**
- Full Phase B as documented (both migrations together) — matches the plan
  literally, but touches live ingestion code for no near-term payoff
- Strategy extraction only, defer expert_sources until Phase H actually needs
  it (chosen) — same functional outcome for the strategy stream, none of the
  ingestion-layer risk
- Skip strategy extraction until expert_sources ships first — reverses the
  plan's own stated rationale for inserting Track B early ("shares the
  extraction pattern already proven with claims")

**Choice:** Strategy extraction now; expert_sources deferred to Phase H
(when a second ingestion pattern actually activates).

**Reasoning:** "Do not expand scope until the current phase operates
reliably" — expert_sources is scope expansion for a capability (multi-source
experts) that doesn't exist yet. Track B's processing cursor doesn't need
expert_sources at all; it's an independent column on content_pieces.

**Prompt calibration status:** Both new prompts (`strategy_extraction_v1`,
`quality_filter_strategy_v1`) are freshly drafted, not yet calibrated against
real output — no baseline distribution exists. Per `prompts/README.md`, run
each on a 20-30 piece/strategy held-out sample before trusting output at
backlog scale; record the first calibration result as a follow-up entry here.
`claim_extraction_v3` is now wired into the extractor but likewise not yet
re-measured against v2 on real output.

**What would change this:** Once a second ingestion pattern (blog/podcast,
Phase H) is actually being built, the expert_sources migration becomes
necessary at that point, not before.

**Revisit:** Phase H kickoff (expert_sources), and after the first
strategy_extraction_v1 / quality_filter_strategy_v1 / claim_extraction_v3
calibration runs (prompt trust).

---

## 2026-07-04 — "Quality-passed" redefined to include partial verdicts

**Decision:** Downstream layers (starting with validation's
`fetch_validatable_claims`) now select claims where
`quality_verdict IN ('pass', 'partial')`, not `= 'pass'` alone. `fail`
remains the only excluded verdict. Updated in `src/db/queries.py`,
`src/quality/screener.py` docstring, and `CLAUDE.md`.

**Context:** The v3 recalibration (same-day entry above) confirmed on 157
real claims that `quality_verdict='pass'` clears at ~1.6% (2 of 122
screened) — the filter's own diagnosis is that `claim_text` +
`reason_given` never contain enough material to score "strong" on any
dimension, not that the claims themselves are low-quality. Gating
everything downstream on `pass` alone would starve validation,
credibility scoring, and eventually synthesis of claims indefinitely
while a v4 extraction fix (richer reasoning capture) is designed and
tested.

**Options considered:**
- Build a v4 extraction prompt (richer `reason_given`), test on a small
  batch, re-measure before touching the gate (more rigorous, but leaves
  every downstream layer starved in the meantime)
- Manually review a sample of partial/fail claims first to sanity-check
  the diagnosis (free, but still leaves the gate closed while reviewing)
- Promote `partial` to usable now (chosen) — unblocks validation and
  credibility scoring immediately using the 95 partial-verdict claims
  already in hand

**Choice:** Promote `partial` to usable now.

**Reasoning:** `partial` claims still passed the rubric's baseline (not
3+ weak) — they have *some* reasoning, just not exhaustively demonstrated
given the compressed input. Excluding them entirely was rejecting claims
for a filter-input limitation, not a claim-quality problem. `fail` (3+
weak dimensions) is a meaningfully different bar and stays excluded.

**What would change this:** If a v4 extraction prompt (richer verbatim
reasoning) later gets `pass` alone above the original 30-50% target on a
real sample, tighten back to `pass` only — this is a stopgap for the
current filter-input limitation, not a permanent rubric philosophy.

**Revisit:** When/if a v4 extraction prompt is built and re-measured.

---

## 2026-07-04 — Horizon-based claim scoping: out_of_scope under 6 months

**Decision:** Claims with target_horizon_months < 6 are tagged
scope='out_of_scope' deterministically during quality screening (migration
005, `derive_scope` in src/quality/screener.py). They still flow through
validation and count toward expert credibility; guidance-facing layers
(theme synthesis onward) filter on scope='in_scope'.

**Context:** Technical plan v3 §7 requires it ("Short-horizon claims and
trading strategies... are marked out_of_scope for guidance regardless of
quality score"). The audit found no scope field existed anywhere.

**Options considered:**
- Tag in the extraction prompt (LLM) — violates "code does mechanics"
- Tag in the screener as a deterministic post-step (chosen)
- Defer to Phase B with strategy scoping — leaves trading calls eligible
  for guidance in the meantime

**Choice:** Deterministic tagging in the screener now; threshold 6 months
per the plan. Claims with no horizon (null) stay in_scope — they resolve
to opinion_only in validation and are filtered by verification_status.

**What would change this:** If theme synthesis shows 6 months cuts off
claims that feed valid 12-month theses, reconsider the boundary (the plan's
long-horizon synthesis input is 12+ months anyway).

**Revisit:** When Layer 5 (synthesis) is built.

---

## 2026-07-04 — Quality filter v3 wired into screener (was silently on v2)

**Decision:** Point src/quality/screener.py at prompts/quality_filter_v3.md.

**Context:** The layer-1-to-4 audit found the screener still hardcoded
quality_filter_v2 while CLAUDE.md and the calibration work assumed v3 (the
loosened counter_argument_acknowledgment rubric) was live. All calibration
measurements taken before this date ran against v2.

**Choice:** One-line prompt-name change; v3's variables and output schema
are identical to v2 so no parser changes. Recalibrate on the standard
20-claim sample immediately after and record the distribution here.

**What would change this:** If v3's pass rate lands outside the 30-50%
target band, iterate to v4 per prompts/README.md (never overwrite v3).

**Recalibration result (2026-07-04, same 22-claim sample):**
v2 baseline: 0 pass / 15 partial / 5 fail (2 unscreened).
v3: 0 pass / 17 partial / 5 fail — 0% pass, target not met.
Per-dimension diagnosis: counter_argument_acknowledgment still scored weak
on 19/22 despite the v3 loosening, AND "strong" is scarce on every
dimension (1-4 of 22). Root cause hypothesis: the filter only sees
claim_text (one sentence) + reason_given (a 1-2 sentence summary) — too
little material to ever score "strong" on reasoning_depth or data_support,
regardless of rubric wording. A v4 likely needs richer input (verbatim
expert reasoning or a transcript excerpt), not further rubric loosening.
Sample caveat: 22 claims from very few videos; re-measure on a broader
sample before concluding.

**Revisit:** v4 design decision — pending founder review of the diagnosis.

---

## 2026-07-04 — Track A validation thresholds and credibility formula v1

**Decision:** Ship Layer 4 Track A (claim outcome validation + credibility
scoring) with these parameters:

- Directional claims: correct if return exceeds ±2% in the predicted
  direction; |return| ≤ 2% is partial regardless of direction; else incorrect.
- Neutral claims: correct within ±5%, partial within ±10%, else incorrect.
- Price targets: correct if the daily-close series ever touched the target
  in the window; partial within 5% of target; else incorrect.
- Credibility (src/internal/scoring.py, proprietary): outcome values
  correct=1.0 / partial=0.5 / incorrect=0.0; strength weights strong=1.0 /
  soft=0.5; recency decay 1.0 (<2y) / 0.5 (2-4y) / 0.25 (>4y); shrinkage
  n/(n+20) toward a neutral 50; three horizon buckets (<12 / 12-36 / >36
  months); overall = claim-count-weighted bucket average; sample_size_flag
  below 20 validated claims.

**Context:** Layer 4 build (2026-07-04). Free data only: yfinance for all
price series, manual CSVs for RBI/Fed rates and CPI (FRED integration is a
documented TODO — avoids blocking on an API key signup).

**Reasoning:** The ±2% band separates a real directional call from noise
over multi-month windows. Shrinkage at k=20 means a 5-for-5 streak scores
~60, not 100 — no expert gets crowned on a small sample. Recency decay
tracks that an expert's edge can fade; strength weighting makes confident
wrongness cost more than hedged wrongness.

**What would change this:** MFE/MAE are already stored per claim
(unused) — scoring v2 can reward "right but early." Macro-indicator claims
(rates, CPI) may deserve bands defined in level changes (bps) rather than
percentage returns; revisit once enough macro claims validate.

**Revisit:** After the first full backlog validation run.

---

## 2026-07-04 — v3 architecture adopted; structural migrations deferred to Phase B

**Decision:** Adopt technical_execution_plan_v3 (multi-source expert_sources
model, dual extraction claims + strategies) as the target architecture, but
defer its structural migrations (expert_sources, strategies tables,
claims_processed_at/strategies_processed_at split, source_quality_baseline
prior, per-medium credibility) until Phase B, per the plan's own build
sequence and the "do not expand scope until the current phase operates
reliably" rule.

**Context:** The v3 plan was written while Phase A (claim quality filter
calibration) was still the gate. An audit on 2026-07-04 confirmed the code
is a coherent v2-era implementation; claim_extraction_v3.md is drafted and
activates together with strategy_extraction_v1 in Phase B.

**What would change this:** Nothing expected — this is sequencing, not
disagreement with v3.

**Revisit:** Phase B kickoff.

---

## 2026-05-28 — Visual + Whisper enrichment: design now, build later

**Decision:** Add visual frame extraction and Whisper re-transcription as a
PHASE-TWO capability. For the MVP, build only transcript-only ingestion using
YouTube auto-captions. But make all schema and code changes now so phase two
slots in without rebuilding.

**Context:** Transcripts miss on-screen charts/data (the proof behind claims),
and old auto-captions (pre-2021) are unreliable for numbers. Both problems are
solved by downloading the video and processing it ourselves — Whisper for clean
audio, vision AI for on-screen data.

**Options considered:**
- Build full visual + Whisper extraction into the MVP (too expensive: ~10x cost
  per video, needs GPU infra, blocks fast MVP)
- Ignore the issue entirely (loses the proof layer and mishandles old content)
- Design the seams now, build the capability in phase two (chosen)

**Choice:** Design-now-build-later, with a tiered rollout in phase two:
- Tier 1 (MVP): transcript only, YouTube auto-captions, all content
- Tier 2 (phase 2): + Whisper re-transcription for old/high-value content
- Tier 3 (phase 2): + visual frame extraction for flagship chart-heavy content

**Reasoning:** Most reasoning is spoken, so transcript-only proves the product
cheaply. Visual extraction (~₹15-35/video) and Whisper (~₹10/video) are 10x the
transcript-only cost, so they must be applied surgically and only after funding.
Designing the seams now (schema fields, swappable capture interface, config
flags) costs nothing today and avoids a painful migration later.

**Changes made now (MVP) to accommodate phase two:**
- Migration 002 adds: transcript_source, transcript_confidence, visual_data,
  has_visual_extraction, enrichment_tier on content_pieces; new visual_evidence
  table; supporting_visual_id + spoken_at_seconds on claims
- New swappable capture abstraction: capture_types.py, capture_base.py
- MVP capture refactored to implement the interface: youtube_capture.py
- Phase-two stubs with full implementation plans: whisper_capture.py,
  visual_capture.py
- Config flags (all default OFF): enable_whisper, enable_visual_extraction,
  tier-selection thresholds

**What would change this:** If users in beta strongly signal that visual proof
is essential for trust (not just nice-to-have), pull tier 3 forward. If costs
come down significantly (cheaper vision models), the tiering thresholds loosen.

**Revisit:** Post pre-seed funding, when there's budget for enrichment compute.

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
