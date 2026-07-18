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

## 2026-07-19, cycle 2
Task 3.2: cross-source entity resolution (the "collapse one founder from many
footprints" piece Elias flagged). Built `ingestion/identity.py`: a pure, reversible
`resolve_identities()` over provider-neutral `IdentitySignal`s using union-find.
Rules: signals from the same source record are one entity; a shared strong identifier
(handle / profile URL / external id / email, URL-normalized) links records across
sources at high confidence; a display name matching across two or more independent
source categories is surfaced as one entity but flagged NEEDS_REVIEW (a name alone is
not proof); same-name-same-source records stay separate; single-source records resolve
at low confidence. Built it myself (not fanned out) since the matching logic is subtle.
8 deterministic tests. Green: 79 tests, ruff, mypy. Commit 98df322.
Scope note: this is in-lane and self-contained. Merging resolved entities into the
canonical Memory store (SubjectRef / Founder / Company) is the SWE persistence layer
and a later paired step, so no shared-contract change was made. Confidences are
heuristic and uncalibrated by design. No NEEDS ELIAS.
