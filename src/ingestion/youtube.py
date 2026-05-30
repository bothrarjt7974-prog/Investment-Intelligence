"""
YouTube content ingestion.

Pulls video metadata via YouTube Data API v3 and transcripts via
youtube-transcript-api. Stores everything in the content_pieces table.

Usage:
    from src.ingestion.youtube import ingest_channel

    ingest_channel(expert_id="...", channel_handle="@warikoo", max_videos=30)
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests
from googleapiclient.discovery import build
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)
from youtube_transcript_api.proxies import WebshareProxyConfig

from src.config.settings import settings
from src.db.queries import insert_one, supabase


def get_youtube_client() -> Any:
    """Return an authenticated YouTube Data API client."""
    return build("youtube", "v3", developerKey=settings.youtube_api_key)


def make_transcript_api() -> YouTubeTranscriptApi:
    """Build a transcript client that uses browser-like headers and retry handling."""
    retries = max(1, settings.youtube_proxy_retries)
    delay_seconds = max(0.0, float(settings.youtube_request_delay_seconds))

    if delay_seconds > 0:
        logger.debug(
            f"Sleeping {delay_seconds:.2f}s before YouTube transcript request to reduce 429s"
        )
        time.sleep(delay_seconds)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com/",
        }
    )

    retry_config = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "HEAD"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry_config)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    if settings.youtube_proxy_username and settings.youtube_proxy_password:
        logger.debug("Using Webshare proxy configuration for YouTube transcripts")
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=settings.youtube_proxy_username,
                proxy_password=settings.youtube_proxy_password,
                retries_when_blocked=retries,
            )
        )

    if settings.youtube_proxy_url:
        logger.debug("Using generic proxy URL for YouTube transcripts")
        session.proxies.update(
            {
                "http": settings.youtube_proxy_url,
                "https": settings.youtube_proxy_url,
            }
        )

        return YouTubeTranscriptApi(http_client=session)

    return YouTubeTranscriptApi(http_client=session)


def resolve_channel_id(handle: str) -> str | None:
    """
    Resolve a YouTube channel handle (e.g. '@warikoo') to a channel ID.
    Returns None if not found.
    """
    yt = get_youtube_client()
    cleaned = handle.lstrip("@")

    # Try the channels.list endpoint with forHandle parameter
    response = yt.channels().list(part="id", forHandle=cleaned).execute()
    items = response.get("items", [])
    if items:
        return items[0]["id"]

    logger.warning(f"Could not resolve channel handle: {handle}")
    return None


def list_recent_videos(channel_id: str, max_results: int = 30) -> list[dict[str, Any]]:
    """
    Return metadata for the most recent videos on a channel.
    Each dict contains: video_id, title, published_at.
    """
    yt = get_youtube_client()

    # Get the uploads playlist ID for the channel
    channel_response = (
        yt.channels().list(part="contentDetails", id=channel_id).execute()
    )
    if not channel_response.get("items"):
        logger.error(f"Channel not found: {channel_id}")
        return []

    uploads_playlist = (
        channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    )

    # Fetch recent videos from the uploads playlist
    videos: list[dict[str, Any]] = []
    next_page_token = None

    while len(videos) < max_results:
        playlist_response = (
            yt.playlistItems()
            .list(
                part="contentDetails,snippet",
                playlistId=uploads_playlist,
                maxResults=min(50, max_results - len(videos)),
                pageToken=next_page_token,
            )
            .execute()
        )

        for item in playlist_response.get("items", []):
            videos.append(
                {
                    "video_id": item["contentDetails"]["videoId"],
                    "title": item["snippet"]["title"],
                    "published_at": item["contentDetails"].get(
                        "videoPublishedAt", item["snippet"]["publishedAt"]
                    ),
                }
            )

        next_page_token = playlist_response.get("nextPageToken")
        if not next_page_token:
            break

    return videos[:max_results]


def fetch_transcript(video_id: str) -> str | None:
    """
    Fetch the transcript for a YouTube video as a single concatenated string.
    Returns None if no transcript is available.
    """
    try:
        api = make_transcript_api()

        try:
            entries = api.fetch(video_id, languages=("en", "en-US", "en-IN"))
        except NoTranscriptFound:
            entries = api.fetch(video_id)

        text = " ".join(getattr(entry, "text", "") for entry in entries)
        return text

    except (IpBlocked, RequestBlocked, TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as e:
        logger.info(f"No transcript for video {video_id}: {type(e).__name__}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error fetching transcript for {video_id}: {e}")
        return None


def video_already_ingested(video_id: str) -> bool:
    """Check if a video is already in content_pieces."""
    url = f"https://youtube.com/watch?v={video_id}"
    response = (
        supabase.table("content_pieces")
        .select("id")
        .eq("source_url", url)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def ingest_video(expert_id: str, video: dict[str, Any]) -> dict[str, Any] | None:
    """
    Ingest a single video: capture transcript, store in content_pieces.
    Returns the inserted row, or None if skipped.

    Uses the swappable TranscriptCapture abstraction. The MVP uses
    YouTubeAutoCaptionCapture. In phase two, the orchestrator can choose
    WhisperCapture for high-value content by flipping settings.enable_whisper —
    this function does not need to change because both return TranscriptResult.
    """
    from datetime import datetime

    from src.ingestion.youtube_capture import YouTubeAutoCaptionCapture

    video_id = video["video_id"]
    url = f"https://youtube.com/watch?v={video_id}"

    if video_already_ingested(video_id):
        logger.debug(f"Skipping already-ingested video: {video_id}")
        return None

    # Parse publish date so capture can assign confidence based on video age.
    published_at = video["published_at"]
    pub_dt = None
    if isinstance(published_at, str):
        try:
            pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            pub_dt = None

    # MVP capture method. Phase two: select method based on settings + expert score.
    capturer = YouTubeAutoCaptionCapture(published_at=pub_dt)
    result = capturer.capture(video_id)

    if result is None:
        logger.info(f"No transcript available for: {video['title'][:60]}")
        return None

    row = {
        "expert_id": expert_id,
        "source_url": url,
        "title": video["title"],
        "published_at": published_at,
        "raw_text": result.text,
        "word_count": result.word_count,
        # Enrichment fields — populated with capture metadata.
        "transcript_source": result.source.value,
        "transcript_confidence": result.confidence,
        "enrichment_tier": "tier1_transcript",
        "has_visual_extraction": False,
    }

    inserted = insert_one("content_pieces", row)
    logger.info(
        f"Ingested: {video['title'][:60]} "
        f"({row['word_count']} words, {result.source.value}, "
        f"conf={result.confidence})"
    )
    return inserted


def ingest_channel(
    expert_id: str, channel_handle: str, max_videos: int = 30
) -> list[dict[str, Any]]:
    """
    Ingest recent videos from a YouTube channel.

    Args:
        expert_id: UUID of the expert row in the experts table
        channel_handle: YouTube handle like '@warikoo' or channel ID
        max_videos: how many recent videos to consider

    Returns:
        List of newly ingested content_pieces rows.
    """
    logger.info(f"Starting ingestion: {channel_handle} (up to {max_videos} videos)")

    if channel_handle.startswith("@"):
        channel_id = resolve_channel_id(channel_handle)
        if not channel_id:
            logger.error(f"Could not resolve channel: {channel_handle}")
            return []
    else:
        channel_id = channel_handle

    videos = list_recent_videos(channel_id, max_videos)
    logger.info(f"Found {len(videos)} recent videos for {channel_handle}")

    ingested: list[dict[str, Any]] = []
    for video in videos:
        result = ingest_video(expert_id, video)
        if result:
            ingested.append(result)

    logger.info(
        f"Finished {channel_handle}: {len(ingested)} new, "
        f"{len(videos) - len(ingested)} skipped"
    )
    return ingested
