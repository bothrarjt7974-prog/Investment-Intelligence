"""
Tests for strategy embeddings (Layer 8). OpenAI calls are monkeypatched
(never hit the real API) — Chroma runs for real via an ephemeral in-memory
client (free, local, no network) since mocking a vector DB's query
semantics would test nothing meaningful.
"""

from __future__ import annotations

import uuid

import chromadb

from src.strategies import embeddings


def _ephemeral_collection():
    # A fresh EphemeralClient() still shares underlying state for a
    # collection *name* across instances within the same process (verified
    # empirically) — a unique name per test is what actually guarantees
    # isolation, not just calling EphemeralClient() again.
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(f"test-{uuid.uuid4()}", metadata={"hnsw:space": "cosine"})


def make_strategy(**overrides):
    base = {
        "id": "s1",
        "strategy_text": "Allocate 40% of equity to PSU banks weighted by market cap",
        "entity_class": "indian_banking",
        "scope": "in_scope",
        "quality_verdict": "pass",
        "source_type": "expert_extracted",
    }
    base.update(overrides)
    return base


class TestEmbedStrategy:
    def test_round_trips_into_chroma_with_metadata(self, monkeypatch):
        collection = _ephemeral_collection()
        monkeypatch.setattr(embeddings, "get_chroma_collection", lambda: collection)
        monkeypatch.setattr(embeddings, "get_embedding", lambda text: [0.1, 0.2, 0.3])
        updates: dict[str, dict] = {}
        monkeypatch.setattr(
            embeddings, "update_by_id", lambda table, row_id, fields: updates.setdefault(row_id, fields)
        )

        embeddings.embed_strategy(make_strategy())

        result = collection.get(ids=["s1"], include=["metadatas", "documents"])
        assert result["documents"][0] == "Allocate 40% of equity to PSU banks weighted by market cap"
        assert result["metadatas"][0] == {
            "entity_class": "indian_banking",
            "scope": "in_scope",
            "quality_verdict": "pass",
            "source_type": "expert_extracted",
        }
        assert updates["s1"]["embedded_at"] is not None

    def test_none_fields_coerced_to_empty_string(self, monkeypatch):
        # Chroma metadata values must be str/int/float/bool — None would crash upsert.
        collection = _ephemeral_collection()
        monkeypatch.setattr(embeddings, "get_chroma_collection", lambda: collection)
        monkeypatch.setattr(embeddings, "get_embedding", lambda text: [0.1, 0.2, 0.3])
        monkeypatch.setattr(embeddings, "update_by_id", lambda table, row_id, fields: None)

        embeddings.embed_strategy(make_strategy(id="s2", entity_class=None, quality_verdict=None))

        result = collection.get(ids=["s2"], include=["metadatas"])
        assert result["metadatas"][0]["entity_class"] == ""
        assert result["metadatas"][0]["quality_verdict"] == ""


class TestSyncStrategyEmbeddings:
    def test_counts_embedded_and_errors(self, monkeypatch):
        strategies = [make_strategy(id="s1"), make_strategy(id="s2")]
        monkeypatch.setattr(embeddings, "fetch_strategies_pending_embedding", lambda limit: strategies)

        embedded_ids = []

        def fake_embed(strategy):
            if strategy["id"] == "s2":
                raise ValueError("boom")
            embedded_ids.append(strategy["id"])

        monkeypatch.setattr(embeddings, "embed_strategy", fake_embed)

        counts = embeddings.sync_strategy_embeddings()

        assert counts == {"embedded": 1, "error": 1, "total": 2}
        assert embedded_ids == ["s1"]
