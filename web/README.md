# Frontend

React + Vite (plain JSX). Two pages and two themes (day / night, remembered per browser).

- **Ask**
  - *Recorded examples*: replays traces from `public/examples.json` step by step. No API or model quota needed, so the public demo always works.
  - *Live*: sends the question to `POST /api/ask/stream` and shows each pipeline step as its event arrives.
- **Evaluation**: reads `GET /api/runs` and `GET /api/runs/{name}`. If the API has no runs, it shows the snapshot in `public/eval-sample.json`.

## Run it

```bash
# terminal 1, project root
set -a; source .env; set +a
uvicorn api.main:app --reload --port 8000

# terminal 2
cd web
npm install
npm run dev          # http://localhost:5173 (proxies /api to :8000)
```

## Production

```bash
cd web && npm run build      # writes web/dist
```

FastAPI serves `web/dist` itself when it exists, so one service hosts both. The Dockerfile builds the frontend in its first stage.

## Use your own recorded examples

The shipped examples are sample traces made with scripted model replies. Replace them with real ones:

```bash
python scripts/export_examples.py runs/demo --ids 1 9 11     # from an eval run
python scripts/export_examples.py trace.json trace2.json      # from saved /api/ask responses
```

## Files

| File | What it holds |
|---|---|
| `src/App.jsx` | header, page tabs, theme switch |
| `src/Ask.jsx` | recorded / live modes, replay timer, SSE handling |
| `src/Trace.jsx` | one window per pipeline step |
| `src/Evaluation.jsx` | scoreboard, breakdown, judge stats |
| `src/Decor.jsx` | pixel-art decorations and icons |
| `src/api.js` | fetch helpers and the SSE reader |
| `src/styles.css` | both themes (CSS variables) and animations |
