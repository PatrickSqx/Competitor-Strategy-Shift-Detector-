# Suspicious Differential Pricing Detector

Single-page FastAPI web app that:
- accepts an electronics search query,
- discovers public product pages across Best Buy, Micro Center, and Amazon,
- extracts listed prices and promo text,
- matches the same product across platforms,
- flags suspicious differential pricing based on public list prices,
- stores comparison history in Neo4j when available.

## Quick Start (Local)

1. Create venv and install dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Set environment variables:
```powershell
$env:NEO4J_URI="neo4j+s://<your-aura-uri>"
$env:NEO4J_USER="<username>"
$env:NEO4J_PASSWORD="<password>"
$env:TAVILY_API_KEY="<tavily-key>"
$env:YUTORI_API_KEY="<yutori-key>"
$env:GEMINI_API_KEY="<gemini-key>"
$env:GEMINI_MODEL="gemini-2.5-pro"
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

Alternative: create a local `.env` file in the repo root and the app will load it automatically on startup.
You can copy `.env.example` to `.env` and fill in:
- `NEO4J_URI`
- `NEO4J_USER` or `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `TAVILY_API_KEY`
- `YUTORI_API_KEY`
- `GEMINI_API_KEY` or `LLM_API_KEY`
- `GEMINI_MODEL` or `LLM_MODEL`
- `SLACK_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL`

The app currently supports Gemini-style env aliases:
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_BASE_URL`

These map into the generic internal LLM config automatically. When enabled, Gemini is used to:
- assist same-product fuzzy matching when exact model matching fails,
- filter weak evidence paths,
- rewrite the final pricing explanation.

3. Optional: seed graph:
```powershell
python scripts/seed_graph.py
```

4. Start the web app:
```powershell
uvicorn app.main:app --reload
```

5. Open the page:
```powershell
http://127.0.0.1:8000
```

6. Optional: run the legacy strategy-signal API demo:
```powershell
.\scripts\demo_alert.ps1
```

## API

- `GET /`
- `POST /api/compare`
- `GET /api/history?query=sony%20wh-1000xm5`
- `GET /healthz`
- `POST /run-once` with body:
```json
{ "scenario": "current" }
```
or
```json
{ "scenario": "shock" }
```
- `POST /webhooks/scheduler`
- `GET /signals/latest?limit=20`

## Render Deployment

1. Push this repo to GitHub.
2. In Render, create Blueprint and point to repo root.
3. Render reads `render.yaml` and creates the service.
4. Fill env vars in Render dashboard:
   - `NEO4J_URI`
   - `NEO4J_USER`
   - `NEO4J_PASSWORD`
   - `TAVILY_API_KEY`
   - `GEMINI_API_KEY`
   - `GEMINI_MODEL`
   - `DISCORD_WEBHOOK_URL` or `SLACK_WEBHOOK_URL`

## Notes

- If credentials are missing, the app still runs with reduced functionality.
- Live scrape may fail for protected pages; the compare API only uses pages where a public listed price could be extracted.
- Discord webhooks are supported via `DISCORD_WEBHOOK_URL` if you do not want Slack.
- The UI labels results as `suspicious differential pricing`; it does not claim unlawful discrimination.
- Taxes, shipping, and checkout-only adjustments are excluded unless explicitly shown on the product page.
