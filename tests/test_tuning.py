"""Tests for the central tuning registry (src/config/tuning.py)."""

from __future__ import annotations

import json

import pytest

from src.config import tuning
from src.config.tuning import PARAMETERS, effective_values, param, save_overrides


@pytest.fixture
def tmp_overrides(tmp_path, monkeypatch):
    """Point the registry at a temp overrides file so tests never touch the real one."""
    path = tmp_path / "tuning_overrides.json"
    monkeypatch.setattr(tuning, "OVERRIDES_PATH", path)
    return path


def test_defaults_match_shipped_values(tmp_overrides):
    """With no overrides, param() returns each registry default."""
    assert param("extraction.min_transcript_chars") == 500
    assert param("quality.out_of_scope_horizon_months") == 7  # raised from 6 in the 2026-07-09 audit
    assert param("themes.medium_min_signal") == 2.0
    assert param("sizing.conservative_max_position_pct") == 5.0
    assert param("guidance.default_review_months") == 12


def test_override_roundtrip(tmp_overrides):
    save_overrides({"validation.directional_move_threshold_pct": 3.5})
    assert param("validation.directional_move_threshold_pct") == 3.5
    # Only the non-default value is persisted
    stored = json.loads(tmp_overrides.read_text())
    assert stored == {"validation.directional_move_threshold_pct": 3.5}


def test_override_clamped_to_safe_range(tmp_overrides):
    tmp_overrides.write_text(json.dumps({"extraction.min_transcript_chars": 999999}))
    spec = PARAMETERS["extraction.min_transcript_chars"]
    assert param("extraction.min_transcript_chars") == spec.max_value


def test_int_params_stay_int(tmp_overrides):
    tmp_overrides.write_text(json.dumps({"backtest.min_trigger_windows": 4.7}))
    value = param("backtest.min_trigger_windows")
    assert isinstance(value, int)
    assert value == 5


def test_corrupt_overrides_file_falls_back_to_defaults(tmp_overrides):
    tmp_overrides.write_text("{not json")
    assert param("guidance.already_aligned_threshold_pct") == 15.0


def test_unknown_keys_dropped_on_save(tmp_overrides):
    save_overrides({"nonsense.key": 42, "matcher.semantic_weight": 0.6})
    stored = json.loads(tmp_overrides.read_text())
    assert "nonsense.key" not in stored
    assert stored["matcher.semantic_weight"] == 0.6


def test_effective_values_covers_every_parameter(tmp_overrides):
    values = effective_values()
    assert set(values.keys()) == set(PARAMETERS.keys())


def test_registry_metadata_is_sane():
    """Every parameter's default must sit inside its own declared range."""
    for key, spec in PARAMETERS.items():
        assert spec.min_value <= spec.default <= spec.max_value, key
        assert spec.plain and spec.raise_effect and spec.lower_effect, key
