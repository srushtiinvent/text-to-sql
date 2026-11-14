# Frontend

React + Vite + TypeScript. Two pages:

- **Ask**: type a question and watch each pipeline step arrive over Server-Sent Events (`POST /api/ask/stream`): retrieved tables, the candidates, their clusters, the distinguishing example, the judge's reasoning, and the final answer with its confidence.
- **Evaluation**: reads `GET /api/runs` and `GET /api/runs/{name}` for the headline table, ablations, accuracy-vs-coverage chart and per-question traces.

The UI is being built to match the design inspiration. Until then, the API is fully usable with curl.
