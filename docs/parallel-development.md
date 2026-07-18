# Parallel development protocol

This MVP remains one OpenSpec change. Until the next jointly reviewed protocol change, use
exactly two branches:

- `main` is the shared integration branch and the Data/ML working lane.
- `work/swe-platform` is the SWE working lane.

The user/SWE is the integrator. Only the integrator edits task checkboxes, and only after the
corresponding implementation and tests are present on `main`. Feature commits reference task
and scenario IDs but do not check them off.

## Initial ownership

| Surface | Primary owner |
| --- | --- |
| `backend/src/founderlookup/api/`, `backend/src/founderlookup/infrastructure/`, project setup, persistence, security, and UX integration | SWE |
| `backend/src/founderlookup/ingestion/`, `backend/src/founderlookup/screening/`, sourcing/evaluation experiments, fixtures, and rubrics | Data/ML |
| `CONTEXT.md`, OpenSpec artifacts, `domain/`, `tests/contract/`, shared `tests/fixtures/`, OpenAPI/domain schemas, and dependency or lock-file changes | Paired review |

Ownership identifies the driver, not an exclusive editor. Do not change a paired-review surface
without both developers reviewing it. Do not import another lane's implementation internals;
integrate through shared interfaces and deterministic fakes.

## Contract and behavior changes

For any observable behavior, lifecycle, schema, scoring-policy, or interface change:

1. Update the active OpenSpec proposal/design/spec/task artifact first.
2. Obtain paired review and update the relevant version identifier when a shared contract changes.
3. Land the OpenSpec and contract-test change on `main` before dependent implementation.
4. Rebase `work/swe-platform` onto that shared commit and run the combined contract suite.

Implementation-only refactors that preserve specified behavior do not need a spec revision, but
they still need the owning lane's tests.

## Cadence

- Pull or fetch before starting a work block; keep commits small and reference task/scenario IDs.
- The Data/ML owner rebases local `main` from `origin/main` before pushing Data/ML work.
- The SWE owner fetches and rebases `work/swe-platform` onto `origin/main` after every shared-contract
  merge and at least once per working day.
- Hand off or integrate at least daily. Before integration, both lanes run their focused tests and
  the shared contract suite.
- The integrator merges SWE work into `main`, runs the combined checks, pushes `main`, and only then
  records completed task checkboxes in a follow-up commit on `main`.
- Stop and pair before resolving a semantic conflict in a shared surface; do not choose a contract
  meaning during a rebase.

## Handoff commands

Start or refresh the Data/ML lane:

```bash
git switch main
git pull --rebase origin main
cd backend
uv sync --frozen
uv run pytest tests/contract tests/unit
```

Start the SWE lane once from the agreed split commit:

```bash
git fetch origin
git switch -c work/swe-platform origin/main
```

Refresh and hand off SWE work:

```bash
git fetch origin
git switch work/swe-platform
git rebase origin/main
cd backend
uv sync --frozen
uv run pytest
uv run ruff check .
uv run mypy
cd ..
git push --force-with-lease origin work/swe-platform
git status --short
git log -1 --oneline
```

Integrate SWE work and record completion:

```bash
git switch main
git pull --rebase origin main
git merge --no-ff work/swe-platform
cd backend
uv sync --frozen
uv run pytest
uv run ruff check .
uv run mypy
cd ..
git push origin main
# Now update only the task checkboxes proven complete on main, then commit and push them.
```

Every handoff message includes the branch, commit hash, OpenSpec task/scenario IDs, checks run,
known failures, and any shared-contract decision still awaiting paired review.
