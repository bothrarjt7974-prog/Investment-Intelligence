"""
Tests for the claim extraction pipeline.

Target-date computation moved to src/extraction/normalization.py (tested in
test_normalization.py); these tests cover response parsing and the
run-loop's failure handling. All DB and LLM access is monkeypatched.
"""

from __future__ import annotations

import pytest

from src.extraction import extractor
from src.extraction.extractor import NonFinancialContentError, parse_claims_response

# Long enough to clear the MIN_TRANSCRIPT_CHARS pre-filter in run loops.
LONG_TEXT = "market analysis " * 50


def test_parse_claims_response_accepts_json_array() -> None:
    payload = '[{"claim_text": "x", "type": "macro"}]'

    claims = parse_claims_response(payload)

    assert claims == [{"claim_text": "x", "type": "macro"}]


def test_parse_claims_response_strips_code_fences() -> None:
    payload = '```json\n[{"claim_text": "x", "type": "macro"}]\n```'

    claims = parse_claims_response(payload)

    assert claims == [{"claim_text": "x", "type": "macro"}]


def test_parse_claims_response_rejects_malformed_json() -> None:
    # Contains '[' so it's a real parse failure, not a prose refusal.
    try:
        parse_claims_response('[{"claim_text": broken')
    except ValueError as exc:
        assert "Malformed JSON" in str(exc)
    else:
        raise AssertionError("Expected malformed JSON to raise ValueError")


def test_parse_claims_response_prose_refusal_is_non_financial() -> None:
    # Claude answering in prose (no JSON array anywhere) means it correctly
    # declined to extract claims from junk content — distinct error type.
    with pytest.raises(NonFinancialContentError):
        parse_claims_response("This content is a movie clip with no financial claims.")


def test_parse_claims_response_rejects_non_array() -> None:
    try:
        parse_claims_response('{"claim_text": "x"}')
    except ValueError as exc:
        assert "JSON array" in str(exc)
    else:
        raise AssertionError("Expected non-array payload to raise ValueError")


def test_run_extraction_marks_failed_piece_processed(monkeypatch) -> None:
    # Regression: a failing piece must still get processed_at set, otherwise
    # it is re-fetched and re-sent to Claude on every run forever (unbounded
    # API cost). processing_error preserves the failure for manual requeue.
    updates: dict[str, dict] = {}

    monkeypatch.setattr(
        extractor,
        "fetch_unprocessed_content_pieces",
        lambda limit: [{"id": "piece-1", "raw_text": LONG_TEXT, "expert_id": "e1"}],
    )
    monkeypatch.setattr(
        extractor,
        "extract_claims_for_piece",
        lambda piece: (_ for _ in ()).throw(ValueError("Malformed JSON from Claude")),
    )
    monkeypatch.setattr(
        extractor,
        "update_by_id",
        lambda table, row_id, fields: updates.setdefault(row_id, fields),
    )

    processed = extractor.run_extraction()

    assert processed == 0
    assert "piece-1" in updates
    assert updates["piece-1"]["processed_at"] is not None
    assert "Malformed JSON" in updates["piece-1"]["processing_error"]


def test_run_extraction_marks_success_without_error(monkeypatch) -> None:
    updates: dict[str, dict] = {}

    monkeypatch.setattr(
        extractor,
        "fetch_unprocessed_content_pieces",
        lambda limit: [{"id": "piece-1", "raw_text": LONG_TEXT, "expert_id": "e1"}],
    )
    monkeypatch.setattr(extractor, "extract_claims_for_piece", lambda piece: [{"id": "c1"}])
    monkeypatch.setattr(
        extractor,
        "update_by_id",
        lambda table, row_id, fields: updates.setdefault(row_id, fields),
    )

    processed = extractor.run_extraction()

    assert processed == 1
    assert updates["piece-1"]["processed_at"] is not None
    assert "processing_error" not in updates["piece-1"]
