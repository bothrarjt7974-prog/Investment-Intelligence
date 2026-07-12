"""
Plain-language translation for guidance display (Layer 9, display-only).

Pure string mapping over the fixed vocabularies the pipeline already
produces — strategy source_type, sizing_source, and the six refusal-reason
templates in src/guidance/eligibility.py and src/guidance/generator.py.
Nothing here changes what's stored; it only changes what a general,
non-technical user sees. Never invents a reason or hides the underlying
fact — every mapped label has the original text available alongside it in
the UI (2026-07-13: founder read the app as a general user and found
internal pipeline vocabulary — "superseded," "re-synthesis," raw enum
values — leaking straight into the product).
"""

from __future__ import annotations

SOURCE_TYPE_LABELS: dict[str, str] = {
    "expert_extracted": "an expert's own stated method",
    "historical_pattern": "a method one expert has repeated many times",
    "cfa_framework": "a textbook (CFA) investing framework",
    "institutional": "a professional/institutional framework",
}


def source_type_label(source_type: str | None) -> str:
    """Plain description of where an execution methodology came from."""
    if not source_type:
        return "an unspecified methodology"
    return SOURCE_TYPE_LABELS.get(source_type, source_type)


SIZING_SOURCE_LABELS: dict[str, str] = {
    "strategy_allocation_rule": "sized using the strategy's own stated rule",
    "risk_profile_cap": "capped by your risk profile, not the strategy",
    "reduction_not_capped": "a full exit — no sizing cap applies",
}


def sizing_source_label(sizing_source: str | None) -> str:
    """
    Plain description of how a position's size was decided. The two
    longer fallback strings build_attribution() already writes
    ("strategy's own stated position sizing" / "no explicit sizing rule
    stated by the matched strategy") are already plain English and pass
    through unchanged — only the short code-like values need translation.
    """
    if not sizing_source:
        return "not recorded"
    return SIZING_SOURCE_LABELS.get(sizing_source, sizing_source)


def refusal_reason_label(reason: str | None) -> str:
    """
    Short, plain-language headline for one of the six fixed refusal-reason
    templates (src/guidance/eligibility.py::check_theme_eligible,
    src/guidance/generator.py's duplicate/entity-overlap/already-aligned
    paths). Matched by stable substrings from those exact templates — if a
    template's wording ever changes, update the match here too. Falls back
    to the raw reason (still true, just less polished) for anything
    unrecognized rather than hiding it.
    """
    if not reason:
        return "No reason recorded"
    r = reason.lower()
    if r.startswith("theme conviction is"):
        return "Not enough expert agreement yet"
    if r.startswith("theme fundamentals_verdict is"):
        return "Financials didn't check out"
    if r == "no matched execution strategy found for this theme":
        return "No concrete strategy matched"
    if r == "active or executing guidance already exists for this theme and portfolio":
        return "Already recommended for this"
    if "already covers this theme's target entities" in r:
        return "Would overlap another active recommendation"
    if r == "portfolio is already sufficiently aligned with this theme's entity/instruments":
        return "You're already well-positioned here"
    return reason


_UNITS = (
    (365, "year"),
    (30, "month"),
    (7, "week"),
    (1, "day"),
)


def humanize_days(days: int) -> str:
    """
    A day-count as a human duration ("about 3 months," "about 2 years")
    instead of a raw number — "1826d remaining" means nothing to a general
    user (2026-07-13 finding). Always rounds to the single largest unit;
    this is a readability aid, not a precise countdown.
    """
    n = abs(days)
    if n == 0:
        return "today"
    for size, name in _UNITS:
        if n >= size:
            count = round(n / size)
            if count == 0:
                continue
            unit = name if count == 1 else f"{name}s"
            return f"about {count} {unit}"
    return "today"
