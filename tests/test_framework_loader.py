"""
Tests for CFA/institutional framework ingestion (Layer 8). No DB/network —
YAML files are written to a temp directory per test.
"""

from __future__ import annotations

import pytest
import yaml

from src.strategies import framework_loader
from src.strategies.framework_loader import parse_framework_file


def _write_yaml(path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def valid_framework(**overrides) -> dict:
    base = {
        "source_name": "Example Endowment Model",
        "source_type": "cfa_framework",
        "strategy_type": "allocation",
        "strategy_text": "Diversify across equities, fixed income, and alternatives.",
        "entity_class": "broad_equity",
        "allocation_rule": "60% equities, 30% fixed income, 10% alternatives",
        "reason_given": "Long-horizon diversification reduces drawdown risk.",
    }
    base.update(overrides)
    return base


class TestParseFrameworkFile:
    def test_parses_valid_framework(self, tmp_path):
        path = tmp_path / "endowment.yaml"
        _write_yaml(path, valid_framework())

        row = parse_framework_file(path)

        assert row["source_name"] == "Example Endowment Model"
        assert row["quality_verdict"] == "pass"
        assert row["scope"] == "in_scope"

    def test_rejects_missing_required_field(self, tmp_path):
        data = valid_framework()
        del data["allocation_rule"]
        path = tmp_path / "bad.yaml"
        _write_yaml(path, data)

        with pytest.raises(ValueError, match="missing required fields"):
            parse_framework_file(path)

    def test_rejects_invalid_source_type(self, tmp_path):
        path = tmp_path / "bad.yaml"
        _write_yaml(path, valid_framework(source_type="made_up"))

        with pytest.raises(ValueError, match="source_type must be one of"):
            parse_framework_file(path)

    def test_rejects_non_mapping_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")

        with pytest.raises(ValueError, match="must be a YAML mapping"):
            parse_framework_file(path)

    def test_optional_fields_default_to_none(self, tmp_path):
        path = tmp_path / "minimal.yaml"
        _write_yaml(path, valid_framework())

        row = parse_framework_file(path)

        assert row["position_sizing"] is None
        assert row["holding_horizon_months"] is None


class TestLoadAllFrameworks:
    def test_loads_new_frameworks_and_skips_existing(self, tmp_path, monkeypatch):
        _write_yaml(tmp_path / "new_one.yaml", valid_framework(source_name="New Framework"))
        _write_yaml(tmp_path / "old_one.yaml", valid_framework(source_name="Already Loaded"))
        # TEMPLATE.yaml must never be loaded as data even if present.
        (tmp_path / "TEMPLATE.yaml").write_text("not: valid: framework: data", encoding="utf-8")

        monkeypatch.setattr(framework_loader, "FRAMEWORKS_DIR", tmp_path)
        monkeypatch.setattr(
            framework_loader, "fetch_existing_framework_source_names", lambda: {"Already Loaded"}
        )
        inserted = []
        monkeypatch.setattr(framework_loader, "insert_many", lambda table, rows: inserted.extend(rows))

        counts = framework_loader.load_all_frameworks()

        assert counts["loaded"] == 1
        assert counts["skipped_existing"] == 1
        assert counts["error"] == 0
        assert inserted[0]["source_name"] == "New Framework"

    def test_missing_directory_returns_zero_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(framework_loader, "FRAMEWORKS_DIR", tmp_path / "does_not_exist")

        counts = framework_loader.load_all_frameworks()

        assert counts == {"loaded": 0, "skipped_existing": 0, "error": 0, "total": 0}

    def test_parse_error_counted_not_raised(self, tmp_path, monkeypatch):
        _write_yaml(tmp_path / "bad.yaml", {"source_name": "Incomplete"})
        monkeypatch.setattr(framework_loader, "FRAMEWORKS_DIR", tmp_path)
        monkeypatch.setattr(framework_loader, "fetch_existing_framework_source_names", lambda: set())
        monkeypatch.setattr(framework_loader, "insert_many", lambda table, rows: None)

        counts = framework_loader.load_all_frameworks()

        assert counts["error"] == 1
        assert counts["loaded"] == 0
