# FounderLookup frontend

The React + TypeScript investor workspace and public founder intake for the FounderLookup MVP.
It is built around a strict sequence: **Act → Understand → Audit**. The first layer exposes the
next safe action, the second explains the system Recommendation, and the third opens Claims,
Trust, Evidence, run history, and cited memo detail.

The default route is a compact public landing page with exactly two paths:

- **Investor workspace** — sourcing, Opportunity review, cited memo, and explicit human Decision.
- **Founder application** — public minimum intake and a capability-scoped private status view.

The founder shell never renders investor navigation or requests investor workspace data. A
Recommendation remains advisory; only a confirmed human Decision is persisted, and no UI action
moves funds.

## Run the live-demo fixture

Fixture mode is deterministic and needs no backend, credentials, OCR, or network access.

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. Useful routes are:

- `#/` — public landing page;
- `#/sourcing` — compound sourcing and candidate queue;
- `#/opportunity/<opportunity-id>` — assessment, independent axes, and Evidence;
- `#/memo/<opportunity-id>` — cited memo and explicit Decision;
- `#/apply` — public founder intake;
- `#/apply/status/<capability>` — bounded private founder status.

Fixture-only state controls cover loading, empty, error, and blocked demonstrations. They do not
appear in the public founder shell or the HTTP production runtime.

## Run against the local API

Start FastAPI from the repository root in one terminal:

```bash
cd backend
uv sync --frozen
uv run uvicorn founderlookup.main:app --reload
```

Then configure and start the frontend:

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

Set these values in `.env.local`:

```dotenv
VITE_DATA_SOURCE=http
VITE_API_BASE_URL=/api/v1
VITE_INVESTOR_AUTH_MODE=proxy
FOUNDERLOOKUP_API_PROXY_TARGET=http://127.0.0.1:8000
FOUNDERLOOKUP_INVESTOR_API_KEY=<same value as backend/.env>
```

In local `proxy` mode, the browser calls same-origin `/api/v1`. Vite forwards `/api` to the
loopback FastAPI origin and injects the bearer credential server-side. The credential has no
`VITE_` prefix and is never embedded in browser assets. The development proxy rejects remote
targets so a local credential cannot be forwarded accidentally.

## Investor access model

The MVP intentionally has one investor credential—no accounts, OAuth, or role model. Production
uses `VITE_INVESTOR_AUTH_MODE=session`: the investor enters the key after the page loads, it is
kept in `sessionStorage` with an in-memory fallback, and it is attached only to investor API calls
as `Authorization: Bearer …`. Locking the workspace or closing the tab clears its useful lifetime.
Remote direct API origins must use HTTPS; the Railway image instead keeps calls same-origin and
proxies them over HTTPS.

Founder application and status calls never receive the investor Authorization header. The founder
status capability stays in the URL fragment for client routing and is sent to FastAPI only through
`X-Founder-Status-Capability`.

## Railway deployment

The production `Dockerfile` builds the browser bundle and serves it with Nginx. Its public build
choices are fixed to HTTP data, `/api/v1`, and session investor access. No credential is a Docker
argument or image environment variable.

Create `backend` and `frontend` services in the same Railway project, using the matching directory
as each service's root. Set an explicit `PORT=8000` on the backend so it can be referenced by the
frontend service. Then set this reference variable on the frontend (replace `backend` if the
service has a different name):

```dotenv
BACKEND_URL=http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:${{backend.PORT}}
```

Use `http`, not `https`, for Railway's project-private network. A public backend URL also works,
but is unnecessary for the browser because Nginx is the same-origin gateway. `BACKEND_URL` must
be an origin with no `/api` suffix because Nginx preserves the incoming `/api/v1/...` path.

The investor gateway has deliberately asymmetric configuration:

| Service | Variable | Value |
| --- | --- | --- |
| backend | `FOUNDERLOOKUP_INVESTOR_API_KEY` | One strong, sealed secret |
| frontend | `BACKEND_URL` | The private backend origin above |
| frontend | investor secret | **Do not set one** |

The investor types the same backend key into the gateway. It remains in that tab's
`sessionStorage` and is sent as a bearer token through the same-origin proxy; no `VITE_` secret or
frontend Railway secret is needed. The browser bundle already fixes `VITE_DATA_SOURCE=http`,
`VITE_API_BASE_URL=/api/v1`, and `VITE_INVESTOR_AUTH_MODE=session` at build time.

Generate a public domain only for the frontend service. Railway supplies its runtime `PORT`; the
frontend image defaults to `8080` outside Railway. The service provides `GET /healthz`, SPA
fallback routing, long-lived content-hashed asset caching, an 11 MiB upload ceiling,
security headers, and same-origin `/api/` proxying. The browser-facing Railway domain must use
HTTPS before an investor key is entered.

For a seeded demo backend, the minimum Railway variables are:

```dotenv
PORT=8000
FOUNDERLOOKUP_ENV=production
FOUNDERLOOKUP_INVESTOR_API_KEY=<strong sealed secret>
FOUNDERLOOKUP_FOUNDER_STATUS_PEPPER=<different strong sealed secret>
FOUNDERLOOKUP_CORS_ORIGINS=https://<frontend-public-domain>
FOUNDERLOOKUP_DEMO_SEED_ENABLED=true
FOUNDERLOOKUP_DEMO_SEED_PRODUCTION_ACKNOWLEDGED=true
```

Attach a backend volume at `/app/data`. Provider keys and enable flags are optional; without them,
the seeded UI remains deterministic and no external provider is called.

Local image smoke test:

```bash
docker build -t founderlookup-frontend .
docker run --rm -p 8080:8080 \
  -e BACKEND_URL=https://backend-production-1cf3.up.railway.app \
  founderlookup-frontend
curl --fail http://127.0.0.1:8080/healthz
```

## Implemented API actions

| UI operation | FastAPI contract |
| --- | --- |
| Initial workspace | `GET /theses/active`, `GET /outbound-candidates?limit=50`, `GET /opportunities?limit=50`, then first Opportunity detail |
| Save thesis revision | `POST /theses` |
| Interpret compound query | `POST /query-plans` |
| Execute validated query | `POST /queries` |
| Start source discovery | `POST /sourcing-runs` and bounded run polling |
| Preliminary candidate assessment | `POST /outbound-candidates/{id}/preliminary-assessment` and bounded run polling |
| Activate outbound candidate | `POST /outbound-candidates/{id}/activate` |
| Record human-controlled outreach | `POST /outbound-candidates/{id}/outreach` |
| Opportunity detail | `GET /opportunities/{id}?expand=claims,evidence` and related run reads |
| Full Screening | `POST /opportunities/{id}/screen` and bounded run polling |
| Retry failed stage | `POST /runs/{id}/retry` and bounded run polling |
| Record human Decision | `POST /opportunities/{id}/decisions` |
| Founder application | public multipart `POST /applications` with `Idempotency-Key` |
| Founder status | public `GET /founder-status` with `X-Founder-Status-Capability` |

The UI model is camelCase; `src/api/contractAdapter.ts` is the explicit snake_case wire boundary.
Unknown data remains Unknown rather than being inferred from a missing value.

Outbound candidate and Opportunity detail responses may also supply `public_contact_routes`
(`contact_routes` is accepted as a compatibility alias) plus `sourcing_audit`/`agent_loop`.
Only routes explicitly classified `public` are rendered. Every rendered route retains its source
artifact, source name, locator, and collection timestamp; unsafe or non-HTTPS links remain
unclickable. The UI never guesses an email address, performs a private lookup, or sends outreach.
Both `contact_url` and `contact_page` wire kinds map to the same public contact-page presentation.
When present, bounded sourcing rounds and the stop reason are shown in the Audit layer.

## Verify before a demo

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

Also check keyboard-only navigation, 320 CSS pixels, 400% zoom, reduced motion, and forced colors
in a real browser. The Soft UI treatment is decorative: visible focus, semantic labels, text-plus-
icon states, and forced-color boundaries remain when shadows disappear.
