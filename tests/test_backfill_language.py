"""Tests for the deterministic Devanagari language-detection heuristic."""

from __future__ import annotations

from scripts.backfill_language import HINDI_MIN_DEVANAGARI_SHARE, devanagari_share


class TestDevanagariShare:
    def test_pure_english_is_zero(self):
        assert devanagari_share("The Nifty 50 sits in a neutral valuation zone.") == 0.0

    def test_pure_hindi_is_high(self):
        text = "द आईपीओ विल इंक्लूड अ फ्रेश इशू ऑफ़ अप टू शेयर्स"
        assert devanagari_share(text) > 0.9

    def test_hinglish_mix_crosses_threshold(self):
        # Realistic Hinglish transcript: Hindi narration with English finance terms.
        text = "मार्केट में correction आया है और Nifty का PE ratio अभी भी high है"
        assert devanagari_share(text) >= HINDI_MIN_DEVANAGARI_SHARE

    def test_english_with_quoted_hindi_stays_under_threshold(self):
        text = (
            "The expert said 'setting chalta hai' (सेटिंग) while explaining why FSI "
            "zoning norms in Indian cities are systematically violated through "
            "corruption, making legally mandated restrictions unenforceable."
        )
        assert devanagari_share(text) < HINDI_MIN_DEVANAGARI_SHARE

    def test_empty_text_is_zero(self):
        assert devanagari_share("") == 0.0
        assert devanagari_share("   \n\t") == 0.0
