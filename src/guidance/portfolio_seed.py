"""
Portfolio seeding (Layer 9 input, not delivery).

Mirrors src/strategies/framework_loader.py's pattern: reads YAML files
from portfolios/ (new top-level dir, same reasoning as frameworks/ — this
is durable content a founder/user maintains, not local ephemeral data)
and loads each one as a user + portfolio + holdings. Idempotent on user
email — a file for an already-seeded user is skipped, not re-inserted.

No LLM calls — straight parse-and-insert of human-provided data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from src.db.queries import fetch_user_by_email, insert_many, insert_one

PORTFOLIOS_DIR = Path(__file__).parent.parent.parent / "portfolios"
TEMPLATE_FILENAME = "TEMPLATE.yaml"

REQUIRED_USER_FIELDS = ("name", "email")
REQUIRED_HOLDING_FIELDS = ("entity", "quantity", "acquired_at")
VALID_RISK_PROFILES = ("conservative", "moderate", "aggressive")
VALID_ASSET_CLASSES = ("equity", "mutual_fund", "etf", "bond", "gold", "crypto", "cash")


def validate_portfolio_payload(payload: dict[str, Any], source_name: str = "payload") -> dict[str, Any]:
    """
    Validate a raw portfolio mapping (from a YAML file OR the Streamlit
    form — both paths share this single validator so the UI can never
    insert something the file loader would reject). Raises ValueError on
    any missing/invalid required field.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{source_name}: must be a mapping")

    missing = [field for field in REQUIRED_USER_FIELDS if not payload.get(field)]
    if missing:
        raise ValueError(f"{source_name}: missing required user fields {missing}")

    risk_profile = payload.get("risk_profile", "moderate")
    if risk_profile not in VALID_RISK_PROFILES:
        raise ValueError(
            f"{source_name}: risk_profile must be one of {VALID_RISK_PROFILES}, got {risk_profile!r}"
        )

    holdings = payload.get("holdings") or []
    if not isinstance(holdings, list):
        raise ValueError(f"{source_name}: 'holdings' must be a list")

    return {
        "user": {
            "name": payload["name"],
            "email": payload["email"],
            "risk_profile": risk_profile,
            "tax_notes": payload.get("tax_notes"),
            "restrictions": payload.get("restrictions") or [],
        },
        "portfolio_name": payload.get("portfolio_name", "Primary"),
        "holdings": validate_holdings(holdings, source_name),
    }


def validate_holdings(holdings: list[dict[str, Any]], source_name: str = "payload") -> list[dict[str, Any]]:
    """Validate a list of holdings dicts. Shared by the file loader and
    the UI's add-holding form."""
    parsed_holdings = []
    for i, holding in enumerate(holdings):
        missing = [field for field in REQUIRED_HOLDING_FIELDS if not holding.get(field)]
        if missing:
            raise ValueError(f"{source_name}: holding #{i} missing required fields {missing}")
        asset_class = holding.get("asset_class", "equity")
        if asset_class not in VALID_ASSET_CLASSES:
            raise ValueError(
                f"{source_name}: holding #{i} asset_class must be one of "
                f"{VALID_ASSET_CLASSES}, got {asset_class!r}"
            )
        parsed_holdings.append(
            {
                "entity": holding["entity"],
                "quantity": holding["quantity"],
                "acquired_at": str(holding["acquired_at"]),
                "cost_basis": holding.get("cost_basis"),
                "asset_class": asset_class,
            }
        )
    return parsed_holdings


def parse_portfolio_file(path: Path) -> dict[str, Any]:
    """Parse and validate one portfolio YAML file. Raises ValueError on any
    missing/invalid required field."""
    with path.open(encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    return validate_portfolio_payload(payload, source_name=path.name)


def seed_portfolio(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Insert one validated portfolio (user + portfolio + holdings rows).
    Caller is responsible for the already-exists check (idempotency
    policy differs between the file loader and the UI).
    Returns the inserted user row.
    """
    user_row = insert_one("users", parsed["user"])
    portfolio_row = insert_one(
        "portfolios", {"user_id": user_row["id"], "name": parsed["portfolio_name"]}
    )
    if parsed["holdings"]:
        holding_rows = [{**h, "portfolio_id": portfolio_row["id"]} for h in parsed["holdings"]]
        insert_many("portfolio_holdings", holding_rows)
    return user_row


def add_holdings(portfolio_id: str, holdings: list[dict[str, Any]]) -> int:
    """Validate and append holdings to an existing portfolio (UI path)."""
    validated = validate_holdings(holdings, source_name="new holding")
    rows = [{**h, "portfolio_id": portfolio_id} for h in validated]
    insert_many("portfolio_holdings", rows)
    return len(rows)


def load_all_portfolios() -> dict[str, int]:
    """
    Load every portfolio YAML in portfolios/ (except TEMPLATE.yaml).
    Skips files whose user email is already seeded — idempotent, safe to
    re-run as holdings are updated by editing and re-running (note: this
    only skips at the user level; updating an existing user's holdings
    requires a manual DB update or a future re-seed command, not covered
    by this v1 loader).
    """
    if not PORTFOLIOS_DIR.exists():
        logger.warning(f"Portfolios directory not found: {PORTFOLIOS_DIR}")
        return {"loaded": 0, "skipped_existing": 0, "error": 0, "total": 0}

    files = sorted(p for p in PORTFOLIOS_DIR.glob("*.yaml") if p.name != TEMPLATE_FILENAME)
    logger.info(f"Found {len(files)} portfolio file(s)")

    counts = {"loaded": 0, "skipped_existing": 0, "error": 0, "total": len(files)}

    for path in files:
        try:
            parsed = parse_portfolio_file(path)
        except (ValueError, yaml.YAMLError) as exc:
            logger.exception(f"Failed to parse portfolio file {path.name}: {exc}")
            counts["error"] += 1
            continue

        email = parsed["user"]["email"]
        if fetch_user_by_email(email) is not None:
            logger.info(f"Skipping already-seeded user: {email}")
            counts["skipped_existing"] += 1
            continue

        seed_portfolio(parsed)
        counts["loaded"] += 1

    logger.info(
        f"Portfolio seeding complete: {counts['loaded']} loaded, "
        f"{counts['skipped_existing']} already seeded, {counts['error']} errors "
        f"(of {counts['total']} files)"
    )
    return counts


def main() -> None:
    """Command-line entry point for portfolio seeding."""
    counts = load_all_portfolios()
    print(
        f"Processed {counts['total']} portfolio files: {counts['loaded']} loaded, "
        f"{counts['skipped_existing']} already seeded, {counts['error']} errors"
    )


if __name__ == "__main__":
    main()
