"""
Whisper transcript capture (PHASE TWO — not yet implemented).

This will re-transcribe video audio using OpenAI Whisper for far higher
accuracy than YouTube auto-captions, especially for:
  - old videos (pre-2021) where auto-captions are unreliable
  - Indian-accented English and Hindi-English code-switching
  - precise numbers and company names

It implements the SAME TranscriptCapture interface as the MVP YouTube
capture, so the orchestrator can swap it in by flipping settings.enable_whisper.

=== IMPLEMENTATION PLAN (build in phase two) ===

1. Download audio only via yt-dlp (audio is ~15-30MB vs 100-300MB full video):
       yt-dlp -x --audio-format mp3 -o <tmp_path> <video_url>

2. Transcribe with Whisper. Two modes (settings.whisper_mode):
   - 'api': send file to OpenAI Whisper API (~$0.006/min, ~₹10 per 20-min video)
   - 'local': run whisper locally on a GPU (free but needs compute; best for
     large backfills — rent a GPU server for a few days)

3. Whisper returns text + per-segment timestamps. Map to TranscriptSegment list.

4. Return a TranscriptResult with source=WHISPER, confidence ~0.95.

5. Clean up: DELETE the downloaded audio file immediately after transcription.
   Never retain media files (storage cost + copyright hygiene).

=== COST CONTROL ===
Only invoke this for content that earns it:
  - expert credibility score >= settings.whisper_min_expert_score, OR
  - existing auto-caption confidence is low (old video), OR
  - the claim density / number density is high
"""

from __future__ import annotations

from src.ingestion.capture_base import TranscriptCapture
from src.ingestion.capture_types import TranscriptResult


class WhisperCapture(TranscriptCapture):
    """PHASE TWO — re-transcribe audio with Whisper. Not yet implemented."""

    def capture(self, video_id: str) -> TranscriptResult | None:
        raise NotImplementedError(
            "WhisperCapture is a phase-two component. "
            "See the implementation plan in this file's docstring."
        )
