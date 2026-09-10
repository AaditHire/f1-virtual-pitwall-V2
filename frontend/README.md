# Virtual Pit Wall frontend

Next.js dashboard for the Phase 7 FastAPI service.

```bash
cp .env.example .env.local
npm install
npm run dev
```

`API_BASE_URL` is used by server-rendered pages and the `/backend/*` same-origin rewrite. It defaults to `http://127.0.0.1:8000`.

The FastAPI backend must be running before the frontend. Available routes are `/` (home), `/weekend`, `/pitwall`, `/standings`, and `/news`; Replay is intentionally marked for a later phase.

Use `npm run build` and `npm run start` for the production server.

Quality checks: `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build`.
