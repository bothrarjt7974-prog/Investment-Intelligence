-- 013: structured guidance relevance/staleness tracking
--
-- Before this migration, portfolio_guidance had exactly one structured
-- timestamp (generated_at, auto-set at insert) and no way to know how
-- long a guidance event stays valid, or whether it's still valid at all.
-- execution_horizon/position_holding_horizon are free-text LLM prose, not
-- parseable, and status is never programmatically transitioned after
-- insert anywhere in the codebase — not even when the source theme itself
-- gets superseded by a later synthesis run (2026-07-12 finding; the
-- 2026-07-10 entity-overlap fix explicitly worked around this by blocking
-- *new* duplicate guidance rather than fixing existing stale rows).
--
-- relevant_as_of: a SNAPSHOT of the source theme's synthesized_at (cast to
--   date) at generation time. Deliberately a snapshot, not a live join —
--   the theme can later be superseded, and this field answers "what was
--   true when this was generated," independent of what's true now.
-- review_by: deterministically computed expiry (src/guidance/relevance.py)
--   from the matched strategy's holding_horizon_months, falling back to
--   the theme's time_horizon_years, falling back to a tunable default.
--   NULL for refusal rows — they don't carry an "in effect" period.
-- stale_reason: populated only when status is programmatically flipped to
--   'superseded' post-generation by the theme-supersession cascade
--   (src/synthesis/theme_synthesis.py) — distinct from a manual/founder
--   status change, which leaves this NULL.

ALTER TABLE portfolio_guidance
    ADD COLUMN IF NOT EXISTS relevant_as_of DATE,
    ADD COLUMN IF NOT EXISTS review_by DATE,
    ADD COLUMN IF NOT EXISTS stale_reason TEXT;
