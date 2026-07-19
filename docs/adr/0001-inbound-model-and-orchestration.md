# ADR 0001: GPT-5.6 Luna and bounded orchestration boundaries

- Status: Accepted, revised after inbound/outbound integration
- Date: 2026-07-19
- Deciders: Elias, Rares, and the human reviewer (resolves OpenSpec tasks 5.2 / 5.3)

## Context

FounderLookup has two different model-shaped responsibilities:

1. inbound investment analysis through five framework-neutral specialist ports (market,
   idea novelty, founder dossier, adversarial validation, and memo synthesis); and
2. semantic extraction from already acquired public sourcing pages before deterministic
   provenance projection.

The rebased inbound lane provides the neutral ports, deterministic fakes, and an optional
OpenAI reasoner wrapper. Outbound sourcing additionally needs a small convergence loop that
can inspect explicit Evidence gaps and issue a bounded follow-up query. Canonical Memory must
never become a model conversation or framework checkpoint store.

The confidence estimator remains provider-neutral and logprob-free: it uses reasoned-sample
dispersion and snap-versus-reasoned divergence. No provider or orchestration framework may
change deterministic rubrics, create a human Decision, activate a candidate, or send outreach.

## Decision

- Use OpenAI `gpt-5.6-luna` as the optional structured-analysis model. `OPENAI_API_KEY`
  remains server-side and a key alone never enables a sourcing call.
- Keep inbound specialist analyses behind their existing framework-neutral ports. The
  OpenAI reasoner is an adapter to those ports, not a new domain contract. Deterministic
  fakes remain the reproducible default and fallback. Full live inbound composition and
  acceptance remain separate implementation tasks.
- Use a separate direct-HTTP Responses API adapter for bounded PUBLIC sourcing extraction.
  It sends `store=false`, a strict Pydantic-derived JSON Schema, and capped input/output;
  deterministic code then validates every excerpt, line, URL, contact route, and Unknown
  against the immutable acquired artifact. Non-public artifacts are rejected even if a
  generic private-use flag is enabled.
- Use LangGraph only as the thin outbound retrieval state machine:
  `plan` → `retrieve_structure` → `assess_gaps` → conditional follow-up or `finalize`.
  It records queries, Evidence deltas, budgets, failures, and a terminal stop reason.
  Provider adapters, validators, scoring, and inbound analysis remain callable without it.
- Treat graph state as disposable run control. Canonical Source Artifacts, Observations,
  Claims, Evidence, assessments, and audit records remain in application-owned Memory.
- Keep founder-private processing behind separate explicit demo gates. Mistral OCR requires
  OCR enablement, private transfer, an explicit confirmed OCR purpose, and either normal
  provider-control confirmations or the hackathon private-risk acknowledgement. Any future
  founder-private OpenAI analysis requires both private enablement and its risk
  acknowledgement. These acknowledgements do not claim training opt-out, Zero Data
  Retention, or a processing region.

## Consequences

- The outbound graph can be replaced without changing provider or domain contracts, and a
  model failure preserves already accepted artifacts before deterministic fallback.
- Public contact routes are evidence records, not enrichment guesses. Each retains a stable
  id, route kind, exact value, validated optional link, public classification, source
  artifact/name/locator, and collection time; participant identity remains unverified.
- Live model/provider behavior is an opt-in acceptance step. A skipped live test is not
  evidence that cost, latency, refusal behavior, or scientific calibration has passed.
- Deterministic tests and demo fixtures remain network-free. If provider credit or access is
  unavailable, the system can still demonstrate the bounded workflow without pretending the
  run was live.
- LangGraph has no authority to activate candidates, contact people, record investment
  Decisions, or move funds.

## Alternatives considered

- `gpt-4o-mini`: the original draft default; superseded by the explicit GPT-5.6 Luna
  selection and therefore no longer the documented runtime default.
- LangGraph for inbound and outbound analysis internals: rejected. Only outbound retrieval
  needs the bounded conditional state machine; specialist analysis and scoring stay neutral.
- Plain Python for the outbound convergence loop: viable, but superseded by the human choice
  of a thin LangGraph boundary with replayable stop-state auditing.
- No live model: retained as the deterministic fallback, but it does not satisfy the
  separately tracked real-provider acceptance checks.
