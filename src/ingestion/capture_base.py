"""
Capture method interfaces.

Defines the abstract contract that any transcript or visual capture
method must follow. This is what makes capture methods swappable.

MVP implements YouTubeAutoCaptionCapture.
Phase 2 will add WhisperCapture and VisualFrameCapture — each implementing
the same interface, so the orchestrator can pick a method without the
rest of the code changing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.ingestion.capture_types import TranscriptResult, VisualData


class TranscriptCapture(ABC):
    """
    Abstract base for anything that turns a video into a transcript.

    Implementations:
      - YouTubeAutoCaptionCapture (MVP) — in youtube.py
      - WhisperCapture (phase 2) — will live in whisper_capture.py
    """

    @abstractmethod
    def capture(self, video_id: str) -> TranscriptResult | None:
        """
        Produce a transcript for the given video.

        Returns a TranscriptResult, or None if no transcript could be obtained.
        Must NOT raise on the common 'no transcript available' case — return None.
        """
        ...


class VisualCapture(ABC):
    """
    Abstract base for anything that extracts visual data from a video.

    Implementations:
      - NullVisualCapture (MVP) — returns empty, does nothing
      - VisualFrameCapture (phase 2) — will live in visual_capture.py
    """

    @abstractmethod
    def capture(self, video_id: str) -> VisualData:
        """
        Produce visual evidence for the given video.
        MVP implementation returns empty VisualData.
        """
        ...


class NullVisualCapture(VisualCapture):
    """
    MVP visual capture — does nothing, returns empty.

    This lets the orchestration code already call visual capture
    everywhere it eventually should, while doing nothing until the
    real implementation is dropped in for phase two.
    """

    def capture(self, video_id: str) -> VisualData:
        return VisualData()  # empty
