# Nightly build log

Newest last. Each entry records: cycle, task, what shipped, tests, commit(s), notes.
`NEEDS ELIAS` marks a parked decision for morning review.

## 2026-07-19, kickoff
Plan agreed with Elias: push to `main` (Rares's agent pulls only), full night, full
Data/ML lane, per `docs/nightly-plan.md`. Already shipped: GitHub and OpenAlex OSINT
source adapters + shared `_support`, root README. Starting the loop.

## 2026-07-19, cycle 1
Task 3.9: expand OSINT palette. Fanned out a Workflow (3 parallel agents) to draft
Hacker News (PUBLIC_SOCIAL), PatentsView (PATENT), and Semantic Scholar (RESEARCH)
source adapters + deterministic tests, mirroring the OpenAlex template on the shared
_support helpers. Fixed one mypy nit (invariant dict in a test helper). Green: 71
tests, ruff, mypy. Palette now: GitHub, OpenAlex, HN, PatentsView, Semantic Scholar.
Note: response shapes follow each API's public docs; live-API field validation is
deferred to when adapters run against real endpoints. No NEEDS ELIAS.
