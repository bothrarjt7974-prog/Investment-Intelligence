"""
YouTube auto-caption transcript capture (MVP).

Implements the TranscriptCapture interface using YouTube's auto-captions
via youtube-transcript-api. This is the cheap, fast, MVP-default method.

In phase two, WhisperCapture will implement the SAME interface, and the
orchestrator will choose between them based on settings and content value.
Nothing downstream changes when that happens.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from src.ingestion.capture_base import TranscriptCapture
from src.ingestion.capture_types import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptSource,
)

# Auto-captions before this year are notably less reliable (weaker speech
# recognition). We lower their confidence so downstream extraction treats
# their numbers with more caution. Phase-two Whisper re-transcription targets
# exactly this low-confidence old content.
RELIABLE_AUTOCAPTION_YEAR = 2021
OLD_AUTOCAPTION_CONFIDENCE = 0.50
RECENT_AUTOCAPTION_CONFIDENCE = 0.70


class YouTubeAutoCaptionCapture(TranscriptCapture):
    """MVP transcript capture using YouTube auto-captions."""

    def __init__(self, published_at: datetime | None = None) -> None:
        # published_at lets us assign lower confidence to old videos.
        self.published_at = published_at

    def _confidence_for_date(self) -> float:
        if self.published_at and self.published_at.year < RELIABLE_AUTOCAPTION_YEAR:
            return OLD_AUTOCAPTION_CONFIDENCE
        return RECENT_AUTOCAPTION_CONFIDENCE

    def capture(self, video_id: str) -> TranscriptResult | None:
        """Fetch auto-captions for a video. Returns None if unavailable."""
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            source = TranscriptSource.YOUTUBE_AUTO
            try:
                transcript = transcript_list.find_manually_created_transcript(
                    ["en", "en-US", "en-IN"]
                )
                source = TranscriptSource.YOUTUBE_MANUAL
            except NoTranscriptFound:
                try:
                    transcript = transcript_list.find_transcript(
                        ["en", "en-US", "en-IN"]
                    )
                except NoTranscriptFound:
                    transcript = next(iter(transcript_list), None)
                    if transcript is None:
                        return None

            entries = transcript.fetch()
            segments = [
                TranscriptSegment(
                    text=e["text"],
                    start_seconds=e["start"],
                    duration_seconds=e.get("duration", 0.0),
                )
                for e in entries
            ]
            full_text = " ".join(s.text for s in segments)

            # Manual captions are more reliable than auto regardless of age.
            confidence = (
                0.85
                if source == TranscriptSource.YOUTUBE_MANUAL
                else self._confidence_for_date()
            )

            return TranscriptResult(
                text=full_text,
                source=source,
                confidence=confidence,
                segments=segments,
                language=transcript.language_code,
            )

        except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as e:
            logger.info(f"No transcript for {video_id}: {type(e).__name__}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error capturing {video_id}: {e}")
            return None
