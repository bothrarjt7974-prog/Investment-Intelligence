"""
Tests for the strategy extraction pipeline (Track B).

Mirrors tests/test_extractor.py's coverage: response parsing and the
run-loop's failure handling. All DB and LLM access is monkeypatched.
"""

from __future__ import annotations

import pytest

from src.extraction import strategy_extractor
from src.extraction.extractor import NonFinancialContentError
from src.extraction.strategy_extractor import (
    _resolve_linked_claim_ids,
    parse_strategies_response,
)

# Long enough to clear the MIN_TRANSCRIPT_CHARS pre-filter in run loops.
LONG_TEXT = "market analysis " * 50


def test_parse_strategies_response_accepts_json_array() -> None:
    payload = '[{"strategy_text": "x", "strategy_type": "allocation"}]'

    strategies = parse_strategies_response(payload)

    assert strategies == [{"strategy_text": "x", "strategy_type": "allocation"}]


def test_parse_strategies_response_strips_code_fences() -> None:
    payload = '```json\n[{"strategy_text": "x", "strategy_type": "allocation"}]\n```'

    strategies = parse_strategies_response(payload)

    assert strategies == [{"strategy_text": "x", "strategy_type": "allocation"}]


def test_parse_strategies_response_rejects_malformed_json() -> None:
    # Contains '[' so it's a real parse failure, not a prose refusal.
    try:
        parse_strategies_response('[{"strategy_text": broken')
    except ValueError as exc:
        assert "Malformed JSON" in str(exc)
    else:
        raise AssertionError("Expected malformed JSON to raise ValueError")


def test_parse_strategies_response_prose_refusal_is_non_financial() -> None:
    with pytest.raises(NonFinancialContentError):
        parse_strategies_response("This video is career advice, no strategies present.")


def test_parse_strategies_response_rejects_non_array() -> None:
    try:
        parse_strategies_response('{"strategy_text": "x"}')
    except ValueError as exc:
        assert "JSON array" in str(exc)
    else:
        raise AssertionError("Expected non-array payload to raise ValueError")


def test_parse_strategies_response_accepts_empty_array() -> None:
    # Strategies are rare (0-3 per piece per the prompt) — an empty array
    # is the normal, expected output for most content, not a failure.
    assert parse_strategies_response("[]") == []


# =====================================================================
# linked_claim_entity -> linked_claim_ids resolution
# =====================================================================


def test_resolve_linked_claim_ids_matches_normalized_entity(monkeypatch) -> None:
    monkeypatch.setattr(
        strategy_extractor,
        "fetch_claims_by_content_piece",
        lambda content_piece_id: [
            {"id": "claim-1", "entity": "indian_banking"},
            {"id": "claim-2", "entity": "BTC"},
        ],
    )

    result = _resolve_linked_claim_ids("piece-1", "indian_banking")

    assert result == ["claim-1"]


def test_resolve_linked_claim_ids_returns_empty_when_no_entity() -> None:
    assert _resolve_linked_claim_ids("piece-1", None) == []


def test_resolve_linked_claim_ids_returns_empty_when_no_match(monkeypatch) -> None:
    monkeypatch.setattr(
        strategy_extractor,
        "fetch_claims_by_content_piece",
        lambda content_piece_id: [{"id": "claim-1", "entity": "BTC"}],
    )

    result = _resolve_linked_claim_ids("piece-1", "indian_banking")

    assert result == []


# =====================================================================
# RUN LOOP TESTS
# =====================================================================


def test_run_strategy_extraction_marks_failed_piece_processed(monkeypatch) -> None:
    # Regression (same as Track A): a failing piece must still get
    # strategies_processed_at set, otherwise it is re-sent to Claude on
    # every run forever. strategies_processing_error preserves the failure.
    updates: dict[str, dict] = {}

    monkeypatch.setattr(
        strategy_extractor,
        "fetch_unprocessed_content_pieces_for_strategies",
        lambda limit: [{"id": "piece-1", "raw_text": LONG_TEXT, "expert_id": "e1"}],
    )
    monkeypatch.setattr(
        strategy_extractor,
        "extract_strategies_for_piece",
        lambda piece: (_ for _ in ()).throw(ValueError("Malformed JSON from Claude")),
    )
    monkeypatch.setattr(
        strategy_extractor,
        "update_by_id",
        lambda table, row_id, fields: updates.setdefault(row_id, fields),
    )

    processed = strategy_extractor.run_strategy_extraction()

    assert processed == 0
    assert "piece-1" in updates
    assert updates["piece-1"]["strategies_processed_at"] is not None
    assert "Malformed JSON" in updates["piece-1"]["strategies_processing_error"]


def test_run_strategy_extraction_marks_success_without_error(monkeypatch) -> None:
    updates: dict[str, dict] = {}

    monkeypatch.setattr(
        strategy_extractor,
        "fetch_unprocessed_content_pieces_for_strategies",
        lambda limit: [{"id": "piece-1", "raw_text": LONG_TEXT, "expert_id": "e1"}],
    )
    monkeypatch.setattr(
        strategy_extractor,
        "extract_strategies_for_piece",
        lambda piece: [{"id": "strategy-1"}],
    )
    monkeypatch.setattr(
        strategy_extractor,
        "update_by_id",
        lambda table, row_id, fields: updates.setdefault(row_id, fields),
    )

    processed = strategy_extractor.run_strategy_extraction()

    assert processed == 1
    assert "strategies_processing_error" not in updates["piece-1"]
    assert updates["piece-1"]["strategies_processed_at"] is not None
