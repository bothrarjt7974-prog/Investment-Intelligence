# Setup Guide

Step-by-step instructions to go from zero to a working development
environment. Follow in order.

## Prerequisites

- Python 3.11 or higher installed
- Git installed
- A code editor (VS Code recommended, with Claude Code extension)
- ~30 minutes for initial setup

## Step 1: Create accounts (15 minutes)

You need accounts on these services. Note your API keys as you create them:

| Service      | URL                              | What you need                                                        |
| ------------ | -------------------------------- | -------------------------------------------------------------------- |
| Anthropic    | https://console.anthropic.com    | API key + add ~$20 credit                                            |
| OpenAI       | https://platform.openai.com      | API key + add ~$10 credit (embeddings only)                          |
| Supabase     | https://supabase.com             | Free tier — create a new project, note URL + anon key + database URL |
| Google Cloud | https://console.cloud.google.com | Enable "YouTube Data API v3", create API key                         |
| GitHub       | https://github.com               | Free — for the repo and Actions                                      |

Optional but recommended:

- **Railway** or **Render** — for hosting later (skip for now)
- **Alpha Vantage** — free tier for US market data
- **FRED** — free API for US macro data

## Step 2: Clone and configure

```bash
# Clone the repo (after pushing to your GitHub)
git clone <your-repo-url>
cd investment-intelligence

# Create a virtual environment
python -m venv .venv

# Activate it
source .venv/bin/activate         # macOS/Linux
# OR
.venv\Scripts\activate            # Windows

# Install dependencies (takes 2-3 minutes)
pip install --upgrade pip
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Now edit .env with your real API keys (use any text editor)
```

## Step 3: Initialize the database

```bash
# Run migrations to create all tables
python -m scripts.run_migrations
```

Expected output: "All migrations applied successfully"

If you get a connection error, double-check `DATABASE_URL` in `.env`.

## Step 4: Seed the experts table

```bash
# Insert the 10 starting experts
python -m scripts.seed_experts
```

Expected output: 10 "Inserted: <name>" lines.

## Step 5: Verify setup

```bash
# Run the sanity-check tests
pytest tests/test_setup.py -v
```

All tests except the integration test should pass. The integration test
makes a real Claude API call — run with `pytest -m integration` to verify
the API key works.

## Step 6: First real ingestion

```bash
# Pull recent videos from one expert manually for testing
python -c "
from src.db.queries import fetch_where
from src.ingestion.youtube import ingest_channel

experts = fetch_where('experts', {'source_type': 'youtube'}, limit=1)
expert = experts[0]
print(f'Ingesting from: {expert[\"name\"]}')
ingest_channel(expert['id'], expert['handle'], max_videos=5)
"
```

Expected: 5 videos processed, transcripts stored in `content_pieces` table.

## Step 7: Open in VS Code with Claude Code

```bash
code .
```

When you open Claude Code in VS Code, it will automatically read `CLAUDE.md`
and have full project context loaded.

Your first Claude Code prompt for tomorrow:

> "Read the architecture document and CLAUDE.md. Then build
> `src/extraction/extractor.py` — it should query unprocessed content_pieces,
> call Claude with the claim_extraction_v2 prompt, parse the JSON response,
> and insert rows into the claims table. Include error handling for
> malformed JSON responses and add a CLI entry point."

## Step 8: Launch the UI

```bash
streamlit run app.py
```

Visit http://localhost:8501. You should see the 10 experts in the sidebar
and the data status tab showing content pieces ingested.

## Troubleshooting

**"ModuleNotFoundError"** — activate your virtual environment first

**"Failed to load settings"** — check `.env` exists and has all required keys

**"Connection refused" on Supabase** — verify `DATABASE_URL` includes the
correct password and uses the connection pooler URL, not the direct one

**YouTube API quota errors** — the default daily quota is 10,000 units.
List operations cost ~1 unit each, so this is enough for daily ingestion of
10 channels. If you hit limits, reduce `max_videos` in `seed_experts.py`.

## What to build next

After setup is working:

1. **Week 1 remainder** — refine the YouTube ingestion, run on all 10 experts
2. **Week 2** — build `src/extraction/extractor.py` (the claim extraction loop)
3. **Week 3** — build `src/quality/screener.py` (the quality filter loop)
4. **Week 4** — wire up the daily cron job and start accumulating data

Refer to the execution document for the full build plan.
