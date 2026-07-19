# FounderLookup: the VC Brain

An AI operating system for early-stage venture. It **sources** founders (inbound applications and outbound open-source intelligence), **screens** each opportunity on three independent axes with per-claim trust and calibrated confidence, and hands a human investor an **evidence-backed, decision-ready recommendation**. It never moves money: a human makes the call.

> Hack-Nation x MIT, Challenge 02 "The VC Brain" (Maschmeyer Group).

## What's built

The scoring engine is real and green: **330+ tests, every scoring rule adversarially verified**, and the whole outbound pipeline runs end to end on deterministic fakes with **no model and no network**.

- **Sourcing.** Five free, public-only OSINT adapters (GitHub, OpenAlex, Hacker News, PatentsView, Semantic Scholar) behind provider-neutral ports, plus a natural-language query planner that turns a thesis into a validated Opportunity Query Plan.
- **Identity resolution.** Collapses one founder from many scattered footprints; ambiguous merges go to a human, never a guess.
- **The judge.** Claim-trust and founder-score rubrics, the three independent axes, and the builder-vs-fundability read that surfaces the exceptional builder a traditional screen filters out.
- **Confidence, computed not asserted.** Self-consistency dispersion, snap-vs-reasoned divergence, explicit abstention when evidence is thin, a counterfactual identity-swap bias check, and per-subgroup calibration.
- **Decision.** A conviction threshold and a candidate-keyed preliminary Assessment Envelope, plus an evaluation harness that scores rank agreement, confidence-band coverage, calibration, and lift over a baseline, on fixtures.
- **Inbound reasoning.** Five framework-neutral analysis interfaces (market, idea novelty, founder dossier, adversarial validation, memo synthesis), wired to **GPT-5.6 Luna via LangGraph** behind the same neutral seam the deterministic fakes use; a live smoke run is the final step.

## The flow

![How FounderLookup thinks: source the founder, resolve identity, build the evidence graph, screen on three independent axes, compute honest confidence, write a cited brief, and a human decides](docs/flow.png)

> The designed pipeline. See **What's built** for today's scope: the outbound path runs end to end on fakes; inbound intake and canonical Memory persistence are aimed.

## Principles we don't compromise on

- **The model extracts, the rubrics score.** The language model turns messy evidence into structured signals; deterministic, versioned rubrics do all the scoring. So the intelligence is auditable and reproducible, never a black box.
- **Three axes, never averaged.** Founder / Market / Idea-vs-Market stay independent, each with a trend.
- **Trust is per-claim.** Every assertion traces to evidence with a confidence level; contradictions surface before the investor sees them.
- **Confidence is honest.** The system reports how sure it is and abstains instead of guessing; missing history lowers coverage and confidence, never founder quality.
- **Founder Score is built to persist.** A per-person, evidence-backed, versioned score designed to follow a founder across companies (persistence via canonical Memory is aimed); one input to the Founder axis, never a replacement.
- **OSINT, done responsibly.** Many public sources, one cross-source-corroborated profile; public-only, terms-respecting, no deanonymization; a human reviews before any outreach.
- **Recommendation, not autonomous capital.** The system decides what to recommend; a human deploys the check.

## Architecture

Modular Python monolith, contract-first, developed spec-driven via OpenSpec (`openspec/changes/build-vc-brain-mvp`).

```
backend/src/founderlookup/
  domain/          # frozen, strict Pydantic contracts (evidence, scoring, discovery, ...)
  ingestion/       # provider-neutral source adapters, identity resolution, query planner
  screening/       # rubrics, three axes, confidence, conviction, evaluation, analysis seam
  api/             # FastAPI transport
  infrastructure/  # persistence, files, telemetry
```

Stack: Python + FastAPI + `uv` + SQLite. Inbound reasoning is wired to **LangGraph + GPT-5.6 Luna** behind framework-neutral interfaces, with deterministic fakes as the default and the demo fallback (a live smoke run is the final step), so the pipeline is reproducible and the model provider is swappable. Mistral OCR is the selected tool for deck extraction; that intake path is aimed. Sourcing anchors free source-specific APIs behind a provider-neutral seam built for later expansion. See `docs/adr/` for the recorded model and orchestration decisions.

## Getting started

```bash
cd backend
uv run pytest          # run the suite (all green)
uv run ruff check .    # lint
uv run mypy src        # type-check
cp .env.example .env   # then fill in local secrets (OPENAI_API_KEY, MISTRAL_API_KEY)
```

## Where things live

- `openspec/changes/build-vc-brain-mvp/` : proposal, design, tasks, and the four capability specs.
- `docs/adr/` : architecture decision records (model provider, orchestration).
- `CONTEXT.md` : the ubiquitous domain language.
- `research/founder-traits.md` : the evidence base behind the founder-scoring rubric.
