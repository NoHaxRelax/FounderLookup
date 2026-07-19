# FounderLookup backend

Python/FastAPI backend for the VC Brain MVP. The project uses a `src/` layout and is managed
with `uv`. Tavily is the human-selected P0 generic public-web provider and is integrated through
direct bounded HTTP behind provider-neutral ports. GPT-5.6 Luna is optional behind structured
analysis/extraction seams, and LangGraph is confined to the bounded outbound retrieval state
machine; neither provider nor framework enters the domain contracts.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/)
- Git

Python 3.12 is pinned in `.python-version`; `uv` can install it automatically when needed.

## Set up and run

```bash
uv lock --check
uv sync --frozen
cp .env.example .env
uv run uvicorn founderlookup.main:app --reload
```

Settings load from the process environment and the local ignored `.env` file. Protected routes
fail closed when `FOUNDERLOOKUP_INVESTOR_API_KEY` is absent; secrets use redacted settings types
and are never part of OpenAPI response models.

The service exposes:

- `GET /health` — process health
- `GET /docs` — interactive OpenAPI documentation
- `GET /openapi.json` — machine-readable API contract
- `POST /api/v1/applications` and `GET /api/v1/founder-status` — public, rate-limited
  founder intake and capability-scoped status
- `/api/v1/theses`, `/api/v1/sourcing-runs`, and `/api/v1/outbound-candidates` — protected
  thesis and sourcing commands
- `/api/v1/query-plans`, `/api/v1/queries`, `/api/v1/opportunities`, and `/api/v1/runs` —
  protected compound-query planning, typed execution, Screening, nested evidence/memo read models,
  and bounded retry
- `POST /api/v1/opportunities/{id}/decisions` — append-only human Decisions
- `GET /api/v1/artifacts/{id}` — protected, content-verified private artifact retrieval

Investor routes require `Authorization: Bearer <FOUNDERLOOKUP_INVESTOR_API_KEY>`. Founder status
uses the separately issued `X-Founder-Status-Capability`; only its keyed digest is retained, and
an investor can revoke it. CORS accepts only the explicit origins in
`FOUNDERLOOKUP_CORS_ORIGINS`. Errors use `application/problem+json` with an `X-Request-ID` and do
not echo credentials, capabilities, local paths, uploaded content, or internal exceptions.

`founderlookup.main:app` composes the safe local runtime: append-only SQLite intake state, private
content-verified artifact storage, the deterministic query boundary, and background deck
extraction. The dependency-injectable `create_app()` factory still returns a fail-closed `503`
when called without an intake service, so tests and alternative hosts cannot accidentally fall
back to the lightweight in-memory intake path.

The P0 restart boundary is intentionally narrower than the storage interfaces: intake revisions,
private artifact bytes, and rule-override events are durable. The fake-backed service projections
for runs, founder-status capabilities, theses, Opportunities, assessments, and memos remain
process-local demo state and are not rehydrated after a restart. Starlette background extraction
is likewise an in-process runner rather than a durable worker queue. An idempotent Application
replay can resume extraction, but production restart recovery and projection rehydration require a
later persistence/worker change.

No environment file is required for the health endpoint. `.env` and `.data/` are ignored so
credentials and private local artifacts are not committed.

## Docker and Railway deployment

The production image runs as a non-root user, binds `${PORT:-8000}`, stores SQLite/private
artifacts under `/app/data`, and checks `GET /health`. Build and smoke-test it from `backend/`:

```bash
docker build -t founderlookup-backend .
docker run --rm -p 8000:8000 --env-file .env founderlookup-backend
curl --fail http://127.0.0.1:8000/health
```

For Railway, create a service with `backend/` as its root; `railway.toml` selects the Dockerfile
and `/health`. Attach a persistent volume at `/app/data` and configure secrets in Railway rather
than committing `.env`. At minimum set a strong `FOUNDERLOOKUP_INVESTOR_API_KEY`, explicit CORS
origins, `FOUNDERLOOKUP_ENV=production`, and the public frontend origin. Provider keys remain
optional and do not enable calls by themselves. If fictional seeded data is intentionally used in
that production demo, both demo-seed flags are required. The documented process-local projection
and background-worker limitations still apply after deployment.

`FOUNDERLOOKUP_ENV` accepts `development`, `test`, or `production`. Fictional demo seeding is
off by default; production requires both the seed flag and its separate production-demo
acknowledgement. `FOUNDERLOOKUP_LOG_LEVEL` accepts the standard `DEBUG`, `INFO`,
`WARNING`, `ERROR`, or `CRITICAL` levels and configures the `founderlookup` package logger without
logging credentials, provider bodies, or acquired content.

## Compound natural-language query planning

`POST /api/v1/query-plans` turns one investor-authored compound request into one typed,
inspectable `OpportunityQueryPlan`. The executable runtime injects the deterministic P0 planner;
the lower-level `create_app()` factory fails closed with `503` when no planner is supplied. The
endpoint is investor protected and accepts explicit result/retrieval budgets plus an optional
controlled vocabulary for geography, sector, or accelerator phrases.

```bash
curl -sS http://127.0.0.1:8000/api/v1/query-plans \
  -H "Authorization: Bearer $FOUNDERLOOKUP_INVESTOR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "raw_query": "technical founder, Berlin, AI infra, enterprise traction, no prior VC backing, top-tier accelerator",
    "max_results": 50,
    "retrieval_max_results": 20,
    "retrieval_max_pages": 3,
    "retrieval_timeout_seconds": 30
  }'
```

The baseline recognizes the six supported PRD attributes in that request, preserves the requested
absence of prior backing as the boolean operand `false`, and leaves subjective `top-tier` visibly
unresolved unless the investor supplies a controlled mapping. Retrieval stays provider-neutral and
bounded. Executable-looking SQL, shell text, prompt-injection phrases, and unsupported prose remain
inert unresolved spans and never become SQL or provider expressions. Planning is one investor
interaction; executing deterministic canonical-data filters or starting a public sourcing run is a
subsequent explicit action, so no interpreted criterion is silently executed or altered.

## Multi-adapter outbound sourcing

The executable runtime can fan one bounded sourcing command across Tavily plus authoritative
public GitHub, Hacker News, OpenAlex, Semantic Scholar, and PatentsView adapters. Every adapter is
independently opt-in and disabled by default. Tavily remains the only generic public-web provider;
put its server-side key in the ignored `.env` before enabling it:

```dotenv
TAVILY_API_KEY=replace-with-a-real-local-key
FOUNDERLOOKUP_TAVILY_ENABLED=true
# Optional source-specific adapters; enable only the sources approved for this deployment.
FOUNDERLOOKUP_GITHUB_ENABLED=true
GITHUB_TOKEN=
FOUNDERLOOKUP_HACKERNEWS_ENABLED=true
FOUNDERLOOKUP_OPENALEX_ENABLED=true
FOUNDERLOOKUP_SEMANTIC_SCHOLAR_ENABLED=true
FOUNDERLOOKUP_PATENTSVIEW_ENABLED=true
```

A key by itself never enables a request. If the flag is enabled without a key, configuration fails
closed. GitHub's token is optional and server-only; omitting it uses the bounded unauthenticated
public API. The other source-specific adapters are keyless. If every adapter is disabled, the
runtime returns a safe `503` instead of claiming a fixture run was live. Configure the global
result, page, content-byte, timeout, and cache ceilings plus Tavily's provider-specific response
and domain ceilings in `.env.example`.

After creating a thesis, a protected sourcing command is suitable for an on-demand or external
cron trigger:

```bash
curl -sS http://127.0.0.1:8000/api/v1/sourcing-runs \
  -H "Authorization: Bearer $FOUNDERLOOKUP_INVESTOR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "technical founders building enterprise AI infrastructure",
    "source_categories": ["developer_activity", "public_social", "research", "patent"],
    "max_results": 10,
    "max_pages": 5,
    "max_bytes": 500000,
    "timeout_seconds": 20
  }'
```

The HTTP response is `202` with a queued run and `Location`; poll that protected run URL for the
terminal result. The coordinator runs enabled adapters concurrently, prefers an authoritative
source-specific record when it duplicates a generic URL, and preserves successful artifacts when
another source times out or fails. Tavily snippets and provider relevance remain retrieval
telemetry, never Evidence. Only approved public HTTP(S) original URLs can be acquired; localhost,
private-network, credential-bearing, nonstandard-port, denied, and non-public targets are rejected.
Acquired bytes become protected immutable Source Artifacts with exact origin provenance.

Outbound convergence is a thin LangGraph `StateGraph`: `plan` → `retrieve_structure` →
`assess_gaps`, followed by either one bounded follow-up or `finalize`. The configured
`FOUNDERLOOKUP_SOURCING_MAX_FOLLOW_UP_ROUNDS` and
`FOUNDERLOOKUP_SOURCING_MAX_DISCOVERY_CALLS` supplement the existing result, page, byte, cache,
and per-provider timeout ceilings. Each run persists its queries, round counts, accepted Evidence
delta, remaining gaps, partial-failure state, and terminal stop reason. Graph state never activates
a candidate, sends outreach, verifies identity, records a Decision, or becomes canonical Memory.

Structured public records project only explicit allowlisted fields into Observations, Evidence,
and unverified Claims. Founder identity stays Unknown unless a later reviewed identity workflow
links it; handles and showcase participant display names never trigger a silent merge. Public
hackathon/showcase pages may link an explicitly labeled public pitch deck, which is acquired as a
separate artifact and related back to the same unresolved candidate. They may also publish a
website, contact page, public email, public profile, or other public follow-up route. Every accepted
route retains its stable id, kind, exact value, validated optional link target, `public`
classification, source artifact/name/locator, and collection time; no route is inferred or enriched
from private data. The runtime never activates a candidate, sends outreach, records a Decision, or
treats source silence as a negative signal.

Optional GPT-5.6 Luna extraction runs only after a PUBLIC page has been acquired. Enable it with a
server-side key and `FOUNDERLOOKUP_OPENAI_STRUCTURED_ENABLED=true`; a key alone does nothing. The
direct Responses request uses `store=false`, strict Pydantic-derived Structured Outputs, and
configured input/output/response-byte/time bounds. Deterministic projection accepts only exact
input lines/excerpts and URLs that pass public-source policy, records returned model/usage and safe
failure metadata, and falls back to deterministic parsing. This sourcing path rejects non-public
artifacts regardless of `FOUNDERLOOKUP_OPENAI_ALLOW_PRIVATE`.

Each artifact receives a collection-policy sidecar. Source terms and robots facts remain visibly
Unknown until this deployment records reviewed facts; enabling an adapter is not a claim that those
facts were reviewed. Collection must remain public, lawful, purpose-limited, and compliant with
the source's current policies.

Repeated commands retain separate run/telemetry history while reusing unchanged artifacts during
`FOUNDERLOOKUP_SOURCING_CACHE_TTL_SECONDS`. There is intentionally no hidden in-process scheduler:
an external cron or job runner can call the same protected endpoint, making recurrence explicit and
deployable while preserving idempotency. Durable worker recovery and rehydrating process-local
candidate projections after restart remain follow-up production work.

## Optional fictional UI demo state

The executable runtime starts empty by default. To evaluate the HTTP-connected UI without first
entering setup data, set this explicit local flag in `.env` and restart the backend:

```dotenv
FOUNDERLOOKUP_DEMO_SEED_ENABLED=true
```

Development keeps this as a single explicit opt-in. Production mode additionally requires:

```dotenv
FOUNDERLOOKUP_DEMO_SEED_PRODUCTION_ACKNOWLEDGED=true
```

That second flag acknowledges that clearly labeled fictional, process-local records are being
shown from a production deployment. It does not make them live sourcing results or Evidence.

The bootstrap creates one fictional default thesis and one clearly labeled fictional outbound
candidate with two fictional public-source handles. It registers only explicit fictional signals
with the deterministic screening bridge, then completes the candidate's preliminary assessment
through the public service API. The identifiers are demo provenance handles, not stored source
content or assertions about a real person. The bootstrap is idempotent within one service instance
and makes no network, model, OCR, or private-artifact call. It does not create an inbound
Application, store deck bytes, activate the candidate, draft or send outreach, run full Screening,
or record a human Decision. Those actions remain user-driven. This demo projection is process-local
and is recreated after a backend restart; set the flag back to `false` for an empty runtime.

## Pitch-deck OCR safety

Mistral OCR 4 is available only as a page-extraction adapter. It sends one bounded, stateless
`POST /v1/ocr` request containing a base64 PDF data URL; it does not use Files, Batch, a public
deck URL, redirects, or image-base64 output. The request selects only pages zero through the
configured `FOUNDERLOOKUP_MISTRAL_OCR_MAX_PAGES` cap, which also bounds per-request page cost.
Responses are streamed through declared-length and incremental decompressed-byte limits before
JSON parsing. `mistral-ocr-latest` is the configurable request alias. When Mistral echoes that
alias instead of a version, the adapter performs one document-free, bounded model-card lookup and
accepts exactly one OCR 4 version alias, so every stored result still preserves a concrete model.

Copying `MISTRAL_API_KEY` into `.env` makes the secret available only to the server-side
composition root; it is not permission to transfer a deck. OCR is disabled by default. Public
material additionally requires `FOUNDERLOOKUP_MISTRAL_OCR_ENABLED=true`; the
simple classification allowlist accepts only `public`. Founder-private and investor-internal
material remain blocked unless all of the following are explicitly configured and confirmed:

- private transfer is allowed;
- training opt-out is confirmed;
- an approved retention or Zero Data Retention posture is recorded;
- the permitted processing region is recorded and confirmed;
- the OCR-only processing purpose is recorded and confirmed.

For the hackathon demo only, the normal training/retention/region confirmations may be replaced by
the explicit `FOUNDERLOOKUP_MISTRAL_OCR_HACKATHON_PRIVATE_RISK_ACCEPTED=true` acknowledgement.
That path still requires OCR enabled, private transfer allowed, and a non-blank OCR purpose with
purpose confirmation. It records acceptance of unknown provider/account controls; it must not be
presented as training opt-out, Zero Data Retention, or region confirmation. A Mistral key or the
risk flag by itself never authorizes a private transfer.

Restricted material is never sent to external OCR, even when the non-public controls are fully
confirmed.

When OCR is missing, disabled, or denied by policy, Application intake still returns `202`, keeps
the original PDF private, preserves extracted fields as explicit Unknown values, and records only
safe blocked-attempt metadata. Provider response bodies and deck content are not retained in the
attempt ledger.

See `.env.example` for the fail-closed flags. Unit tests use `httpx.MockTransport`; they do not
load `.env`, use the real credential, or access the network.

## Verify the scaffold

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

The cross-lane baseline is the deterministic contract suite:

```bash
uv run pytest tests/contract
```

It validates the frozen v0 domain shapes, provider-neutral fake adapters, and the fictional
golden sourcing corpus without network access, model calls, or provider credentials. See the
[parallel development protocol](../docs/parallel-development.md) before changing a shared
contract or integrating either workstream.

## Module boundaries and initial ownership

| Path | Responsibility | Initial owner |
| --- | --- | --- |
| `src/founderlookup/domain/` | Shared domain language, schemas, and invariants | Paired review |
| `src/founderlookup/ingestion/` | Inbound/outbound normalization and provider-neutral ports | Data/ML primary |
| `src/founderlookup/screening/` | Deterministic rules and framework-neutral intelligence | Data/ML primary |
| `src/founderlookup/api/` | FastAPI transport only | SWE primary |
| `src/founderlookup/infrastructure/` | Persistence, files, configuration, telemetry | SWE primary |
| `tests/contract/` and `tests/fixtures/` | Cross-lane contracts and golden examples | Paired review |

Business rules do not belong in route functions. Search/model adapters must implement shared
contracts and must not leak provider payload types into `domain/`.

## Dependency gates

Tavily is the only approved P0 generic web provider; do not add Exa or a second generic provider
without a later OpenSpec decision. The resolved gate permits GPT-5.6 Luna behind the existing
framework-neutral analysis/extraction interfaces and LangGraph only for the thin bounded outbound
retrieval state machine. Adding another model provider, expanding LangGraph into analysis or
autonomous actions, or adding LangChain/LlamaIndex requires a later recorded decision. The direct
HTTP Mistral OCR adapter remains limited to extraction and does not approve investment analysis.
