"""
Tests for portfolio seeding (Layer 9). No DB/network — YAML files are
written to a temp directory per test.
"""

from __future__ import annotations

import pytest
import yaml

from src.guidance import portfolio_seed
from src.guidance.portfolio_seed import parse_portfolio_file


def _write_yaml(path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def valid_portfolio(**overrides) -> dict:
    base = {
        "name": "Test User",
        "email": "test@example.com",
        "risk_profile": "moderate",
        "tax_notes": None,
        "restrictions": [],
        "portfolio_name": "Primary",
        "holdings": [
            {
                "entity": "RELIANCE",
                "quantity": 100,
                "acquired_at": "2023-01-15",
                "cost_basis": 2450.0,
                "asset_class": "equity",
            }
        ],
    }
    base.update(overrides)
    return base


class TestParsePortfolioFile:
    def test_parses_valid_portfolio(self, tmp_path):
        path = tmp_path / "user.yaml"
        _write_yaml(path, valid_portfolio())

        parsed = parse_portfolio_file(path)

        assert parsed["user"]["email"] == "test@example.com"
        assert len(parsed["holdings"]) == 1
        assert parsed["holdings"][0]["entity"] == "RELIANCE"

    def test_rejects_missing_user_field(self, tmp_path):
        data = valid_portfolio()
        del data["email"]
        path = tmp_path / "bad.yaml"
        _write_yaml(path, data)

        with pytest.raises(ValueError, match="missing required user fields"):
            parse_portfolio_file(path)

    def test_rejects_invalid_risk_profile(self, tmp_path):
        path = tmp_path / "bad.yaml"
        _write_yaml(path, valid_portfolio(risk_profile="yolo"))

        with pytest.raises(ValueError, match="risk_profile must be one of"):
            parse_portfolio_file(path)

    def test_rejects_holding_missing_required_field(self, tmp_path):
        data = valid_portfolio(holdings=[{"entity": "RELIANCE", "quantity": 100}])
        path = tmp_path / "bad.yaml"
        _write_yaml(path, data)

        with pytest.raises(ValueError, match="missing required fields"):
            parse_portfolio_file(path)

    def test_rejects_invalid_asset_class(self, tmp_path):
        data = valid_portfolio(
            holdings=[
                {
                    "entity": "RELIANCE",
                    "quantity": 100,
                    "acquired_at": "2023-01-15",
                    "asset_class": "nft",
                }
            ]
        )
        path = tmp_path / "bad.yaml"
        _write_yaml(path, data)

        with pytest.raises(ValueError, match="asset_class must be one of"):
            parse_portfolio_file(path)

    def test_no_holdings_is_valid(self, tmp_path):
        path = tmp_path / "empty.yaml"
        _write_yaml(path, valid_portfolio(holdings=[]))

        parsed = parse_portfolio_file(path)

        assert parsed["holdings"] == []


class TestLoadAllPortfolios:
    def test_loads_new_and_skips_existing(self, tmp_path, monkeypatch):
        _write_yaml(tmp_path / "new_user.yaml", valid_portfolio(email="new@example.com"))
        _write_yaml(tmp_path / "old_user.yaml", valid_portfolio(email="old@example.com"))
        (tmp_path / "TEMPLATE.yaml").write_text("not: a: valid: portfolio", encoding="utf-8")

        monkeypatch.setattr(portfolio_seed, "PORTFOLIOS_DIR", tmp_path)
        monkeypatch.setattr(
            portfolio_seed,
            "fetch_user_by_email",
            lambda email: {"id": "existing"} if email == "old@example.com" else None,
        )
        inserted_users = []
        inserted_portfolios = []
        monkeypatch.setattr(
            portfolio_seed,
            "insert_one",
            lambda table, row: (
                inserted_users.append(row) or {"id": "new-user-id"}
                if table == "users"
                else inserted_portfolios.append(row) or {"id": "new-portfolio-id"}
            ),
        )
        inserted_holdings = []
        monkeypatch.setattr(
            portfolio_seed, "insert_many", lambda table, rows: inserted_holdings.extend(rows)
        )

        counts = portfolio_seed.load_all_portfolios()

        assert counts["loaded"] == 1
        assert counts["skipped_existing"] == 1
        assert counts["error"] == 0
        assert inserted_users[0]["email"] == "new@example.com"
        assert inserted_holdings[0]["portfolio_id"] == "new-portfolio-id"

    def test_missing_directory_returns_zero_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(portfolio_seed, "PORTFOLIOS_DIR", tmp_path / "does_not_exist")

        counts = portfolio_seed.load_all_portfolios()

        assert counts == {"loaded": 0, "skipped_existing": 0, "error": 0, "total": 0}
