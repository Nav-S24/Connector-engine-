# Connector Control Room

Operational control-plane dashboard for the self-healing SaaS data connector platform (`connector-platform/`).

## Features

- **Overview**: Platform-wide KPI tiles (`Total Runs`, `Success Rate`, `Rows Synced (24h)`, `Open Drift Events`) and per-connector cards with live `"Run now"` trigger and run polling.
- **Runs**: Filterable run history table with an accessible slide-over detail drawer showing per-stream breakdowns and dead-lettered records.
- **Drift & Repairs**: Schema drift timeline grouped by connector plus AI repair proposals featuring unified/side-by-side YAML diffs, validation-gate checklists, escalation banners, and gated Approve/Reject actions.
- **Audit Log**: Searchable, sortable history of AI generation and repair decisions with expandable inline diffs.
- **Benchmarks**: Multi-batch concurrency throughput curves (Recharts), baseline vs. optimized speedup callout, and AI evaluation metrics.
- **Automatic Demo Mode**: When `VITE_USE_MOCK=true` or the control API is unreachable, the client automatically falls back to realistic stateful sample data and displays a `Demo data` indicator in the header.

## Running Locally

```bash
npm install
npm run dev
```

## Pointing at the Real Backend (`connector serve`)

1. Start the mock SaaS target server and the Python control API from `connector-platform/`:

   ```bash
   cd connector-platform
   source .venv/bin/activate

   # Terminal 1: mock SaaS APIs (port 8000)
   uvicorn mock_server.app:app --host 127.0.0.1 --port 8000

   # Terminal 2: control API (port 8100, CORS enabled for http://localhost:5173)
   connector serve --host 127.0.0.1 --port 8100
   ```

2. Point the dashboard at the control API by setting `VITE_API_URL` (defaults to `http://localhost:8100`) and ensuring `VITE_USE_MOCK=false` in `.env` or your shell:

   ```bash
   VITE_API_URL=http://localhost:8100 VITE_USE_MOCK=false npm run dev -- --port 5173
   ```

   > Note: `connector serve` enables CORS for `http://localhost:5173` by default. Run Vite on port `5173` (or configure your reverse proxy) when connecting directly to the local Python control plane.
