"""
Deterministic guidance relevance/staleness computation (Layer 9).

Code, not LLM — same "code does mechanics" split as sizing.py and
eligibility.py. Two fields get computed here and stamped onto every real
guidance row at generation time:

  relevant_as_of: a snapshot of the source theme's synthesized_at date.
    Snapshot, not a live join, because the theme can later be superseded —
    this answers "what was true when this was generated," not "what's
    true now."
  review_by: a deterministic expiry date, so a guidance event a user opens
    weeks later can be flagged as due for review instead of looking
    exactly as current as one generated an hour ago (2026-07-12 finding —
    no structured relevance tracking existed before this module).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.config.tuning import param


def _as_date(value: Any) -> date | None:
    """Coerce a date/datetime/ISO string into a date, or None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def relevant_as_of(theme: dict[str, Any]) -> date | None:
    """Snapshot of the source theme's synthesis date."""
    return _as_date(theme.get("synthesized_at"))


def _add_months(base: date, months: int) -> date:
    """Same 30.44-days/month approximation used elsewhere in this codebase
    (src/extraction/normalization.py::compute_target_date)."""
    return base + timedelta(days=int(months * 30.44))


def compute_review_by(
    generated_at: date,
    matched_strategy: dict[str, Any] | None,
    theme: dict[str, Any] | None,
) -> date:
    """
    Deterministic expiry date, in priority order:
      1. The matched strategy's own stated holding_horizon_months.
      2. The theme's own stated time_horizon_years.
      3. A tunable fallback default (guidance.default_review_months) —
         used only when neither the strategy nor the theme states a
         horizon, which real data shows does happen (e.g. an
         entry-methodology strategy with no holding period stated).
    """
    if matched_strategy and matched_strategy.get("holding_horizon_months"):
        return _add_months(generated_at, int(matched_strategy["holding_horizon_months"]))

    if theme and theme.get("time_horizon_years"):
        months = round(float(theme["time_horizon_years"]) * 12)
        return _add_months(generated_at, months)

    return _add_months(generated_at, int(param("guidance.default_review_months")))
