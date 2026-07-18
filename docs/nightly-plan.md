# Overnight autonomous build plan (Data/ML lane)

Elias's agent runs this unattended overnight. Rares runs a parallel agent that
**pulls only** (it does not push), so this agent **pushes to `main`** and Rares's
agent integrates by pulling through the night. Keep every commit small and strictly
in the Data/ML lane so his merges stay trivial.

## Each cycle
1. `git checkout main && git pull` (always pull first).
2. Pick the next unblocked task in the Data/ML lane (see Scope). Skip anything that
   needs an SWE-owned file, the model/provider gate, or a human decision.
3. Build it. If it splits into independent pieces (for example several source
   adapters), fan out a Workflow to build them in parallel; integrate serially.
4. Green-gate: `uv run pytest`, `uv run ruff check .`, and `uv run mypy src tests`
   must all pass. If not green after 3 attempts, `git restore` the change and park
   the task with a note.
5. Commit small (one logical change), push to `main`.
6. Append an entry to `docs/nightly-log.md`.
7. Schedule the next cycle.

## Scope (in-bounds)
- More OSINT source adapters (Hacker News, arXiv, ProductHunt, PatentsView, ...),
  mirroring `ingestion/sources/openalex.py` on the shared `_support` helpers.
- Cross-source entity resolution / dedup (one founder from many footprints).
- Deterministic screening rubrics + fakes (three axes, Founder Score, Claim Trust,
  cold-start, builder-vs-fundability) against fixtures, no live model.
- Evaluation harness (predictive-validity and calibration scaffolding on fixtures).
- Fixtures and labeled corpus for the above.

## Never do overnight
- Touch SWE-owned files (`api/`, `infrastructure/`, UX, scaffold, `pyproject.toml`).
- Wire a live model provider or add a framework/search dependency (gated).
- Live-scrape terms-restricted sources; adapters are tested against fakes only.
- Irreversible git (force-push, rewrite shared history) or merge Rares's branches.
- Push non-green code, or send anything external.

## Ambiguity or plan-holes: council, do not block
1. Spin a council (parallel agents): enumerate options, recommend, adversarially check.
2. Reversible and in-lane: take the recommendation, record it in the log, proceed.
3. Not safely reversible (shared-contract change, product decision, anything touching
   Rares's lane or a human gate): park the task with a `NEEDS ELIAS` note plus the
   council reasoning in the log, and move to the next unblocked task.

## Hard stops (halt and wait for Elias)
- No unblocked in-lane task remains.
- Repeated failure to reach green.
- The only remaining work needs an SWE file, the model gate, or a human decision.
