# FounderLookup backend

Python/FastAPI backend for the VC Brain MVP. The project uses a `src/` layout, is managed
with `uv`, and deliberately contains no generic-search, model-provider, or agent-framework
dependency before the corresponding human decision gates.

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
- `/api/v1/queries`, `/api/v1/opportunities`, and `/api/v1/runs` — protected typed query,
  Screening, nested evidence/memo read models, and bounded retry
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

## Pitch-deck OCR safety

Mistral OCR 4 is available only as a page-extraction adapter. It sends one bounded, stateless
`POST /v1/ocr` request containing a base64 PDF data URL; it does not use Files, Batch, a public
deck URL, redirects, or image-base64 output. The request selects only pages zero through the
configured `FOUNDERLOOKUP_MISTRAL_OCR_MAX_PAGES` cap, which also bounds per-request page cost.
Responses are streamed through declared-length and incremental decompressed-byte limits before
JSON parsing. `mistral-ocr-latest` is the configurable request alias, while each result preserves
the concrete model returned by Mistral.

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

Do not add Tavily, Exa, an investment-model SDK, LangGraph, LangChain, LlamaIndex, or an
alternative orchestration framework until its OpenSpec human gate is completed and recorded.
The direct HTTP Mistral OCR adapter is limited to extraction and does not approve investment
analysis. Deterministic fakes and framework-neutral interfaces come first.
