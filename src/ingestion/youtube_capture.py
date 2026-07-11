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
from youtube_transcript_api._errors import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

from src.ingestion.capture_base import TranscriptCapture
from src.ingestion.capture_types import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptSource,
)
from src.ingestion.youtube import make_transcript_api

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
            api = make_transcript_api()

            try:
                entries = api.fetch(video_id, languages=("en", "en-US", "en-IN"))
                # Read the REAL track language instead of hardcoding "en":
                # en-IN tracks on Indian channels are frequently Hinglish or
                # outright Hindi, and hardcoding left all 1,001 pieces
                # labeled 'en' — Hindi content couldn't be stratified for
                # quality measurement (2026-07-10 audit). Fall back to "en"
                # only when the library object doesn't expose the code.
                language = getattr(entries, "language_code", None) or "en"
            except NoTranscriptFound:
                # No English track. `api.fetch(video_id)` with no `languages`
                # arg looks like a fallback but silently defaults to
                # `languages=('en',)` internally — identical to the attempt
                # above, not a real fallback. Many Indian finance channels
                # only have Hindi/Hinglish auto-captions; list what's
                # actually available and take the first track rather than
                # dropping the video. Claude reads multilingual transcripts
                # fine downstream.
                transcript_list = api.list(video_id)
                transcript = next(iter(transcript_list))
                entries = transcript.fetch()
                language = transcript.language_code

            # The library reports whether captions are creator-uploaded or
            # speech-recognition auto-captions. Most English tracks on finance
            # channels ARE auto-generated — labeling any successful English
            # fetch as "manual" (as this code once did) inflated confidence
            # to 0.85 on transcripts that deserve 0.50-0.70, which corrupts
            # phase-two Whisper tier selection. Default to auto when the
            # attribute is missing: under-claiming confidence is safe,
            # over-claiming is not.
            is_generated = getattr(entries, "is_generated", True)
            source = (
                TranscriptSource.YOUTUBE_AUTO
                if is_generated
                else TranscriptSource.YOUTUBE_MANUAL
            )

            segments = []
            for e in entries:
                text = getattr(e, "text", None)
                if text is None and hasattr(e, "get"):
                    text = e.get("text", "")

                start_seconds = getattr(e, "start", None)
                if start_seconds is None and hasattr(e, "get"):
                    start_seconds = e.get("start", 0.0)

                duration_seconds = getattr(e, "duration", None)
                if duration_seconds is None and hasattr(e, "get"):
                    duration_seconds = e.get("duration", 0.0)

                segments.append(
                    TranscriptSegment(
                        text=text or "",
                        start_seconds=start_seconds or 0.0,
                        duration_seconds=duration_seconds or 0.0,
                    )
                )
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
                language=language,
            )

        except StopIteration:
            logger.info(f"No transcript for {video_id}: no tracks available")
            return None
        except (IpBlocked, RequestBlocked, TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as e:
            logger.info(f"No transcript for {video_id}: {type(e).__name__}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error capturing {video_id}: {e}")
            return None
