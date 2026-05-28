"""
Capture result shapes.

These dataclasses define the consistent OUTPUT shape that any content
capture method must produce. This is the key abstraction that lets us
swap capture methods without touching downstream code.

MVP: YouTube auto-caption capture produces a TranscriptResult.
Phase 2: Whisper capture produces the SAME TranscriptResult shape.
Phase 2: Visual extraction produces VisualData.

Because downstream code (storage, extraction) only depends on these
shapes — not on HOW they were produced — adding Whisper or visual
extraction later requires zero changes downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TranscriptSource(str, Enum):
    """How a transcript was obtained."""

    YOUTUBE_AUTO = "youtube_auto"      # MVP: auto-captions
    YOUTUBE_MANUAL = "youtube_manual"  # creator-uploaded captions
    WHISPER = "whisper"                # phase 2: re-transcribed


class EnrichmentTier(str, Enum):
    """How richly a content piece was processed."""

    TIER1_TRANSCRIPT = "tier1_transcript"  # MVP: transcript only
    TIER2_WHISPER = "tier2_whisper"        # phase 2: Whisper re-transcription
    TIER3_FULL = "tier3_full"              # phase 2: Whisper + visual extraction


@dataclass
class TranscriptSegment:
    """One timed segment of a transcript."""

    text: str
    start_seconds: float
    duration_seconds: float


@dataclass
class TranscriptResult:
    """
    The standard output of ANY transcript capture method.

    Both the MVP YouTube capture and the future Whisper capture
    return this exact shape. Downstream storage code consumes this
    without knowing which method produced it.
    """

    text: str                                    # full concatenated transcript
    source: TranscriptSource
    confidence: float                            # 0.0–1.0
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = "en"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class VisualEvidenceItem:
    """One piece of visual evidence extracted from a video frame (phase 2)."""

    timestamp_seconds: int
    visual_type: str                             # 'chart'|'table'|'text_overlay'|'slide'
    extracted_data: dict                         # structured content read from frame
    source_watermark: str | None = None          # e.g. 'Screener.in'
    confidence: float | None = None
    vision_model: str | None = None


@dataclass
class VisualData:
    """
    The standard output of the future visual extraction method.

    Empty in MVP. When phase-two visual extraction is built, it produces
    a list of these and downstream code stores them in visual_evidence.
    """

    items: list[VisualEvidenceItem] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return len(self.items) > 0
