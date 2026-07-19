# ADR 0001: Inbound investment-intelligence model and orchestration

- Status: Accepted
- Date: 2026-07-19
- Deciders: Elias, Rares (satisfies the human model gate, tasks 5.2 / 5.3)

## Context

The inbound reasoning lane needs a language model to produce the reasoned reads
that the five analysis interfaces (market, idea novelty, founder dossier,
adversarial validation, memo synthesis) consume, plus an orchestration layer to
run them. Task 5.2 requires an explicit human decision on both before any provider
is installed or configured.

Note that our confidence estimator is deliberately logprob-free (it uses
self-consistency dispersion and snap-versus-reasoned divergence, not token
logprobs), so it does not require any specific provider. Anthropic Claude was
therefore a viable option and is already available to the development agent. The
team nonetheless chose OpenAI because an OpenAI API account with credit was on
hand for the hackathon.

## Decision

- Model: OpenAI, default `gpt-4o-mini`, as the inbound investment-intelligence
  model. The key lives server-side in the gitignored `backend/.env` as
  `OPENAI_API_KEY` and is never committed.
- Orchestration: LangGraph, for the inbound reasoning loop only, behind the
  framework-neutral analysis interfaces (task 3.7). Checkpoints are transient run
  state, never canonical Memory.
- Both sit behind the neutral interfaces, so the provider or orchestrator can be
  swapped without touching the domain contracts or the rest of the pipeline.
- Deterministic fakes remain the default for development, tests, and the demo
  fallback. Live model calls happen only on demo/final runs, to conserve credit
  and keep the pipeline reproducible.

## Consequences

- The live inbound path depends on an external paid API, so it is not
  self-contained. The full pipeline still runs end to end on deterministic fakes
  with no model call, which is the self-contained, reproducible demo path.
- Development iterates on the free fakes; credit is spent only on the handful of
  live demo runs. If credit is exhausted, the demo falls back to fakes and still
  runs, so the provider is not a single point of failure.
- Cost is kept low by defaulting to `gpt-4o-mini`, small self-consistency sample
  counts, and bounded `max_tokens`.

## Alternatives considered

- Anthropic Claude: viable and already available to the dev agent, no token
  logprobs (not needed for our confidence method). Not chosen only because the
  OpenAI credit was already in hand.
- No live model (fakes only): kept as the default and the demo fallback, but on
  its own it does not exercise the model-backed reasoning we want to show.
