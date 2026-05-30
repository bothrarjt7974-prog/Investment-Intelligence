from __future__ import annotations

from datetime import date, datetime

from src.extraction.extractor import compute_target_date, parse_claims_response


def test_parse_claims_response_accepts_json_array() -> None:
    payload = '[{"claim_text": "x", "type": "macro"}]'

    claims = parse_claims_response(payload)

    assert claims == [{"claim_text": "x", "type": "macro"}]


def test_parse_claims_response_rejects_malformed_json() -> None:
    try:
        parse_claims_response("not json")
    except ValueError as exc:
        assert "Malformed JSON" in str(exc)
    else:
        raise AssertionError("Expected malformed JSON to raise ValueError")


def test_compute_target_date_uses_timeframe_offsets() -> None:
    published = datetime(2024, 5, 1, 12, 0, 0)

    assert compute_target_date("short", published) == date(2025, 5, 1)
    assert compute_target_date("medium", published) == date(2026, 5, 1)
    assert compute_target_date("long", published) == date(2027, 5, 1)
