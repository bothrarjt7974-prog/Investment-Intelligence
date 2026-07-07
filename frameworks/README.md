# Frameworks

CFA and institutional-practice allocation strategies for the strategy
library (Layer 8), loaded by `src/strategies/framework_loader.py`.

**This directory is intentionally empty of real content.** Per the
2026-07-07 decision log entry: encoding 30-40 real CFA/institutional
frameworks is "2-3 weeks of finance-consultant work" per
`technical_execution_plan_v3.docx` §11 — genuine financial methodology
content, not something an LLM should author wholesale, especially for a
SEBI RIA product bound by "never invent strategies not present in the
library" (CLAUDE.md). This directory holds the *mechanism*; a founder or
finance consultant supplies the real content.

## Adding a framework

Copy `TEMPLATE.yaml` to a new file (e.g. `endowment_model.yaml`) and fill
in every field with a real, citable methodology. `source_name` must be
unique — it's how the loader detects an already-loaded framework and
skips re-inserting it.

Run `python -m src.strategies.framework_loader` to load every `.yaml` file
in this directory (except `TEMPLATE.yaml`, which is never loaded as data)
into the `strategies` table.
