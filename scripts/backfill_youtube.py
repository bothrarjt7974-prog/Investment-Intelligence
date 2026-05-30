"""
Backfill all YouTube videos for one expert.

Usage:
    python -m scripts.backfill_youtube @AkshatZayn
"""

from __future__ import annotations

import argparse
import random
import time
from typing import Any

from loguru import logger

from src.db.queries import fetch_where
from src.ingestion.youtube import (
    get_youtube_client,
    ingest_video,
    resolve_channel_id,
    video_already_ingested,
)


def parse_args() -> argparse.Namespace:
    """Parse the expert handle from the command line."""
    parser = argparse.ArgumentParser(
        description="Backfill the full YouTube history for one expert handle."
    )
    parser.add_argument(
        "handle",
        help="YouTube handle of the expert, for example @AkshatZayn",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=8.0,
        help="Minimum random delay between video requests (default: 8.0 seconds)",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=15.0,
        help="Maximum random delay between video requests (default: 15.0 seconds)",
    )
    parser.add_argument(
        "--batch-pause",
        type=float,
        default=90.0,
        help="Extra pause after every batch of 50 videos (default: 90 seconds)",
    )
    return parser.parse_args()


def list_all_videos(channel_id: str) -> list[dict[str, Any]]:
    """Fetch every video in the channel uploads playlist, using pagination."""
    yt = get_youtube_client()

    channel_response = yt.channels().list(part="contentDetails", id=channel_id).execute()
    if not channel_response.get("items"):
        logger.error(f"Channel not found: {channel_id}")
        return []

    uploads_playlist = (
        channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    )

    videos: list[dict[str, Any]] = []
    next_page_token = None

    while True:
        playlist_response = (
            yt.playlistItems()
            .list(
                part="contentDetails,snippet",
                playlistId=uploads_playlist,
                maxResults=50,
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

    return videos


def find_expert_id(handle: str) -> str | None:
    """Look up the stored expert row using its YouTube handle."""
    experts = fetch_where("experts", {"handle": handle, "source_type": "youtube"}, limit=1)
    return experts[0]["id"] if experts else None


def is_blocking_error(exc: Exception) -> bool:
    """Return True when YouTube appears to be temporarily blocking the request."""
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "blocked",
            "rate limit",
            "too many requests",
            "429",
            "temporarily blocked",
            "request blocked",
            "ip blocked",
        )
    )


def main() -> None:
    """Run the backfill for one expert handle and print a final summary."""
    args = parse_args()
    handle = args.handle.strip()

    if not handle:
        logger.error("Please provide an expert handle, for example @AkshatZayn")
        raise SystemExit(1)

    expert_id = find_expert_id(handle)
    if not expert_id:
        logger.error(f"No expert found in the database for handle: {handle}")
        raise SystemExit(1)

    channel_id = resolve_channel_id(handle)
    if not channel_id:
        logger.error(f"Could not resolve YouTube channel: {handle}")
        raise SystemExit(1)

    videos = list_all_videos(channel_id)
    total = len(videos)
    logger.info(f"Starting backfill for {handle}: {total} videos found")

    ingested = 0
    skipped = 0
    failed = 0
    ip_logic_active = False

    for index, video in enumerate(videos, start=1):
        print(f"[{index}/{total}] Processing: {video['title'][:80]}")
        video_id = video["video_id"]

        if video_already_ingested(video_id):
            skipped += 1
            logger.debug(f"Skipped existing video {video_id}")
            continue

        if not ip_logic_active:
            ip_logic_active = True
            logger.info("First new video found; enabling conservative YouTube pacing")

        try:
            result = ingest_video(expert_id, video)
            if result:
                ingested += 1
            else:
                failed += 1
        except Exception as exc:
            if is_blocking_error(exc):
                logger.warning(
                    f"YouTube appears to be blocking {video_id}; waiting 5 minutes and retrying once"
                )
                time.sleep(300)
                try:
                    result = ingest_video(expert_id, video)
                    if result:
                        ingested += 1
                    else:
                        failed += 1
                except Exception as retry_exc:
                    logger.exception(f"Retry failed for video {video_id}: {retry_exc}")
                    failed += 1
            else:
                logger.exception(f"Failed to ingest video {video_id}: {exc}")
                failed += 1

        if ip_logic_active and index < total:
            time.sleep(random.uniform(args.delay_min, args.delay_max))

        if ip_logic_active and index % 50 == 0 and index < total:
            logger.info(f"Completed batch ending at {index}/{total}; taking a longer pause")
            time.sleep(args.batch_pause)

    print("\nBackfill summary")
    print(f"  ingested: {ingested}")
    print(f"  skipped:  {skipped}")
    print(f"  failed:   {failed}")
    print(f"  total:    {total}")


if __name__ == "__main__":
    main()
