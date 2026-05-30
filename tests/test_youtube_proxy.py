from __future__ import annotations

from types import SimpleNamespace

from src.ingestion.youtube import make_transcript_api


def test_make_transcript_api_uses_generic_proxy_retries(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        def __init__(self) -> None:
            self.headers = {}
            self.proxies = {}
            self.mounted = {}

        def mount(self, prefix: str, adapter: object) -> None:
            self.mounted[prefix] = adapter

    class FakeYouTubeTranscriptApi:
        def __init__(self, http_client=None, proxy_config=None) -> None:
            captured["http_client"] = http_client
            captured["proxy_config"] = proxy_config

    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_url", "http://proxy.example")
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_username", "")
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_password", "")
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_retries", 4)
    monkeypatch.setattr("src.ingestion.youtube.requests.Session", FakeSession)
    monkeypatch.setattr("src.ingestion.youtube.YouTubeTranscriptApi", FakeYouTubeTranscriptApi)

    make_transcript_api()

    assert isinstance(captured["http_client"], FakeSession)
    assert captured["http_client"].headers["User-Agent"].startswith("Mozilla/")
    assert captured["http_client"].proxies == {
        "http": "http://proxy.example",
        "https": "http://proxy.example",
    }
    assert "http://" in captured["http_client"].mounted
    assert "https://" in captured["http_client"].mounted


def test_make_transcript_api_applies_request_delay(monkeypatch) -> None:
    sleep_calls: list[float] = []

    class FakeSession:
        def __init__(self) -> None:
            self.headers = {}
            self.proxies = {}
            self.mounted = {}

        def mount(self, prefix: str, adapter: object) -> None:
            self.mounted[prefix] = adapter

    class FakeYouTubeTranscriptApi:
        def __init__(self, http_client=None, proxy_config=None) -> None:
            self.http_client = http_client
            self.proxy_config = proxy_config

    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_url", "")
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_username", "")
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_password", "")
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_proxy_retries", 3)
    monkeypatch.setattr("src.ingestion.youtube.settings.youtube_request_delay_seconds", 0.75)
    monkeypatch.setattr("src.ingestion.youtube.requests.Session", FakeSession)
    monkeypatch.setattr("src.ingestion.youtube.YouTubeTranscriptApi", FakeYouTubeTranscriptApi)
    monkeypatch.setattr("src.ingestion.youtube.time.sleep", lambda seconds: sleep_calls.append(seconds))

    make_transcript_api()

    assert sleep_calls == [0.75]
