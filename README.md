# Competitor Strategy Shift Detector Agent

Autonomous web intelligence agent that:
- Scrapes competitor listings (live first, fallback snapshots second).
- Detects strategy shifts (sustained undercut and promo-intensity spikes).
- Enriches with Tavily evidence.
- Uses Yutori for action support and optional scout task creation.
- Posts action alerts to Slack.
- Stores memory and confidence updates in Neo4j (Aura-compatible).

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
$env:SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

3. Optional: seed graph:
```powershell
python scripts/seed_graph.py
```

4. Start API:
```powershell
uvicorn app.main:app --reload
```

5. Run demo flow:
```powershell
.\scripts\demo_alert.ps1
```

## API

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
   - `YUTORI_API_KEY`
   - `SLACK_WEBHOOK_URL`

## Notes

- If credentials are missing, the app still runs with reduced functionality.
- Live scrape may fail for protected pages; fallback snapshot mode keeps demo reliability.
