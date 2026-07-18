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

The initial service exposes:

- `GET /health` — process health
- `GET /docs` — interactive OpenAPI documentation
- `GET /openapi.json` — machine-readable API contract

No environment file is required for the health endpoint. `.env` and `.data/` are ignored so
credentials and private local artifacts are not committed.

## Verify the scaffold

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

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

Do not add Tavily, Exa, a model-provider SDK, LangGraph, LangChain, LlamaIndex, or an alternative
orchestration framework until its OpenSpec human gate is completed and recorded. Deterministic
fakes and framework-neutral interfaces come first.
