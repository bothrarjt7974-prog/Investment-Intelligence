"""
Visual frame capture (PHASE TWO — not yet implemented).

Extracts on-screen data (charts, tables, numbers, source watermarks) from
video frames using vision AI. This is the "proof layer" — the visual evidence
behind spoken claims that transcripts miss.

Implements the SAME VisualCapture interface as the MVP NullVisualCapture,
so it drops in by flipping settings.enable_visual_extraction.

=== IMPLEMENTATION PLAN (build in phase two) ===

1. Download video at modest quality (480p/720p is enough to read charts):
       yt-dlp -f 'best[height<=720]' -o <tmp_path> <video_url>

2. Detect key frames (where visuals change) rather than sampling everything:
   - Use PySceneDetect or ffmpeg scene filter
   - A 20-min video yields ~30-60 distinct visual moments

3. Pre-filter frames cheaply (skip talking-head frames with no data):
   - Lightweight check for text/chart content vs just a face
   - Reduces ~50 candidate frames to ~15 data-rich frames

4. Send data-rich frames to vision AI (settings.vision_model):
   - Prompt: extract chart titles, axis labels, numeric values, table
     contents, text overlays, and any source watermark (Screener.in etc.)
   - Returns structured JSON per frame

5. Build VisualEvidenceItem per frame with timestamp_seconds.

6. Return VisualData(items=[...]).

7. Clean up: DELETE the downloaded video immediately after extraction.

=== TIMESTAMP PAIRING (done downstream, not here) ===
Each VisualEvidenceItem has a timestamp. The transcript segments also have
timestamps. Downstream, a claim spoken at minute 7 gets linked to visual
evidence captured at minute 7 — giving the claim its visual proof.

=== COST CONTROL ===
This is the most expensive capture (~₹15-35/video). Only invoke for:
  - expert score >= settings.visual_min_expert_score, AND
  - transcript number density >= settings.visual_min_number_density
i.e. only flagship, chart-heavy analysis from top experts.
"""

from __future__ import annotations

from src.ingestion.capture_base import VisualCapture
from src.ingestion.capture_types import VisualData


class VisualFrameCapture(VisualCapture):
    """PHASE TWO — extract visual data from video frames. Not yet implemented."""

    def capture(self, video_id: str) -> VisualData:
        raise NotImplementedError(
            "VisualFrameCapture is a phase-two component. "
            "See the implementation plan in this file's docstring."
        )
