from __future__ import annotations

import importlib


def test_backfill_skips_existing_videos_without_sleeping(monkeypatch) -> None:
    module = importlib.import_module("scripts.backfill_youtube")

    videos = [
        {"video_id": "a", "title": "old-1", "published_at": "2024-01-01T00:00:00Z"},
        {"video_id": "b", "title": "old-2", "published_at": "2024-01-02T00:00:00Z"},
        {"video_id": "c", "title": "new-1", "published_at": "2024-01-03T00:00:00Z"},
    ]

    sleep_calls: list[float] = []

    monkeypatch.setattr(module, "list_all_videos", lambda channel_id: videos)
    monkeypatch.setattr(module, "find_expert_id", lambda handle: "expert-1")
    monkeypatch.setattr(module, "resolve_channel_id", lambda handle: "channel-1")

    seen: list[str] = []

    def fake_already_ingested(video_id: str) -> bool:
        seen.append(video_id)
        return video_id in {"a", "b"}

    def fake_ingest_video(expert_id: str, video: dict[str, object]) -> dict[str, object] | None:
        return {"video_id": video["video_id"]}

    monkeypatch.setattr(module, "video_already_ingested", fake_already_ingested)
    monkeypatch.setattr(module, "ingest_video", fake_ingest_video)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr("sys.argv", ["scripts.backfill_youtube.py", "@demo"])

    module.main()

    assert seen == ["a", "b", "c"]
    assert sleep_calls == []
