# Portfolios

Real user portfolios for Layer 9 guidance generation, loaded by
`src/guidance/portfolio_seed.py`.

**This directory holds real financial data — never commit a real
portfolio file to a shared/public repo.** For the founder's own portfolio
(user zero per MVP scope), consider keeping it local-only rather than
version-controlled, or use a private repo/branch.

## Adding a portfolio

Copy `TEMPLATE.yaml` to a new file (e.g. `founder.yaml`) and fill in real
holdings. `email` is the idempotency key — the loader skips a file whose
user email is already seeded.

Run `python -m src.guidance.portfolio_seed` to load every `.yaml` file in
this directory (except `TEMPLATE.yaml`) into `users`/`portfolios`/
`portfolio_holdings`.

Entity names must use the same canonical naming convention as claims/
strategies (`src/extraction/normalization.py`'s `ENTITY_ALIASES` rules —
uppercase tickers, lowercase_with_underscores sectors/indices/macro
factors) so guidance generation can match holdings against theme/strategy
entities.
