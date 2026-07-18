## Context

The repository is greenfield except for the extracted hackathon brief and OpenSpec configuration. The brief makes Sourcing the most important MVP pillar, requires the Founder Score to persist across opportunities, requires three independent opportunity axes, and attaches Trust Score to individual claims. It accepts company name plus deck as the minimum inbound Application and expects outbound discovery to lead to outreach and a real Application before both paths converge on full Screening.

The user selected Python, a project-owned `pyproject.toml`, `uv`, and FastAPI for the backend. Mistral OCR 4 is selected narrowly for pitch-deck extraction, using the configurable `mistral-ocr-latest` alias while persisting the concrete model returned by each run. No frontend technology, investment-intelligence model provider, agent-orchestration framework, or generic web discovery provider has been selected. Tavily and Exa are available candidates for outbound web discovery/content acquisition and require a human-reviewed bake-off.

The stakeholders are:

- a founder submitting a minimum Application;
- a human investor configuring a thesis, reviewing evidence, and recording the Decision;
- a coder/reviewer who must approve any agent-orchestration framework;
- an SWE and a Data/ML specialist implementing the same change in parallel;
- hackathon judges assessing data architecture, intelligence and trust, investment utility, and UX.

The domain language is recorded in the repository `CONTEXT.md`. In particular, canonical Memory is durable business evidence and history; it is not an agent chat history or framework checkpoint store.

## Goals / Non-Goals

**Goals:**

- Demonstrate a sourcing-first path using heterogeneous inbound and outbound data.
- Give inbound and activated outbound Applications one common Screening Case and Assessment Envelope.
- Preserve provenance, source history, explicit knowledge states, and claim-level Evidence.
- Run cheap, explainable deterministic rules before expensive model analysis where possible.
- Produce a persistent Founder Score, three non-averaged axes, claim-level Trust Scores, contradictions, gaps, a concise memo, and a human-actionable Recommendation.
- Expose the workflow through a typed FastAPI contract and an accessible evidence-first experience.
- Measure progress toward decision readiness within 24 hours.
- Keep third-party collection, model providers, and future orchestration replaceable at real seams.
- Let the SWE and Data/ML workstreams proceed in parallel after a shared contract freeze and integrate through deterministic fixtures and contract tests.

**Non-Goals:**

- Actual capital transfer, autonomous investment, autonomous outreach sending, or production campaign automation.
- Portfolio monitoring, follow-on investing, fund operations, or exit-management UI.
- Exhaustive web crawling or production-scale source coverage.
- Multi-tenant identity, RBAC, collaborative deal rooms, or production fund administration.
- Online learning of investment weights from funded outcomes.
- Raw or hidden model chain-of-thought capture.
- Selecting or adding a generic web provider or agent framework without the corresponding explicit human gate.

## Decisions

### 1. Build one modular Python backend

The MVP will be a modular monolith. The four capabilities are specification and module seams, not independently deployed services.

```text
backend/
├── pyproject.toml             # authored for this project; managed with uv
├── uv.lock
├── README.md
├── src/founderlookup/
│   ├── domain/                # ubiquitous types and invariants
│   ├── ingestion/             # Memory ingestion and external-source adapters
│   ├── screening/             # thesis rules and framework-neutral intelligence
│   ├── api/                   # FastAPI transport adapter and HTTP schemas
│   └── infrastructure/        # persistence, files, telemetry, configuration
└── tests/
    ├── unit/
    ├── contract/
    ├── integration/
    └── fixtures/              # decks and seeded contradictions
```

The first implementation action will create this scaffold, a custom `pyproject.toml`, the `uv` environment and lockfile, a backend README, importable packages, tests, and configuration examples. Empty `.gitkeep` files will be used only for intentionally empty non-package directories; Python packages will contain real package files.

`screening` is preferred over a generic `ml` directory because it owns deterministic rules, model-assisted analysis, validation, memo synthesis, and human-review semantics—not just machine-learning code.

FastAPI is an HTTP adapter over application/domain interfaces. Business rules do not live in route functions. This gives callers a small interface while keeping parsing, validation, filtering, provenance, and orchestration implementation local to their owning module.

For the hackathon MVP, structured records will use SQLite and private Source Artifact bytes will use a non-public local artifact directory. Both stay internal to the Memory implementation, and tests use temporary equivalents. This provides durable, zero-operations local runs without committing the domain interface to a production database or object store.

**Alternatives considered:**

- Four services: rejected for the MVP because deployment, distributed transactions, schemas, and observability would consume hackathon time without improving the judging criteria.
- A flat routes/scripts layout: rejected because source-provider and model details would leak into every caller and make deterministic testing fragile.
- A generic `ml/` package: rejected because it obscures deterministic screening and the investment domain.
- PostgreSQL plus external object storage: deferred because their operational cost does not improve the local hackathon demonstration; immutable identifiers and domain contracts preserve a later migration path.

### 2. Use one canonical domain flow with an explicit outbound boundary

```text
INBOUND
company + deck ─────────────────────────────┐
                                            │
OUTBOUND                                    ▼
public signals → Outbound Candidate → activation → Application
       │                                            │
       └→ preliminary activation assessment         │
                                                    ▼
                    canonical Memory → Screening Case
                                             │
                                   deterministic rules
                                             │
                                 structured intelligence
                                             │
                                      validation/gaps
                                             │
                                  memo + Recommendation
                                             │
                                      human Decision
```

An Outbound Candidate receives a candidate-keyed preliminary, thesis-aware Assessment Envelope when its source-backed signals cross a versioned conviction threshold or an investor explicitly requests analysis. The envelope uses the same criterion, axis, Claim, Evidence, and coverage structures as full Screening, with Unknown fields and preliminary mode made explicit. The candidate becomes eligible for the full Screening used by inbound only after activation is followed by a founder-submitted Application. Existing outbound Evidence is linked into that Screening Case; it is not copied into a separate scoring universe.

This resolves an apparent tension in the brief: outbound prospects are “scored the same way” for discovery, yet activated Applications “converge” at Screening. The common part is the domain schema, thesis semantics, and final full-screening pipeline; assessment mode and evidence coverage remain explicit.

Canonical lifecycle vocabularies are versioned and distinct:

- Outbound Candidate: Discovered → Preliminary Assessment → Ready for Activation → Activated → Contacted → Applied or Closed.
- Application: Received → Ingesting → Ready for Screening → Linked to Screening Case, with Withdrawn and Failed terminal alternatives.
- Screening Case: First Pass → Screening → Diligence → Readiness Review → Decision Ready → Decided or Closed. Readiness Review may enter Blocked; focused follow-up returns Blocked cases to Diligence or Readiness Review, while an authorized accepted-risk event may advance them to Decision Ready without erasing the blocker.
- Screening Case readiness: Not Evaluated, Blocked, Ready, or Ready With Accepted Risk.
- Pipeline Run: Queued, Running, Succeeded, Partially Succeeded, or Failed.

Skipping a state requires an explicit transition rule and audit event; activation never implies contact, Application, or Decision.

**Alternatives considered:**

- Full diligence of every public prospect before contact: rejected as costly, biased by sparse data, and misleading without founder-submitted material.
- Separate inbound and outbound scoring systems: rejected because scores would be incomparable and drift would undermine the promised common funnel.

### 3. Make missingness and thesis applicability orthogonal

Decision-relevant fields use a typed `KnowledgeValue<T>` with these states:

| State | Meaning |
| --- | --- |
| `known` | A sourced value is present; verification and Trust remain separate. |
| `unknown` | Applicable, but not currently established. |
| `not_disclosed` | Explicitly withheld, declined, or confirmed not supplied; bare omission stays Unknown. |
| `not_applicable` | No meaningful value exists for this subject; requires a reason. |
| `conflicted` | Credible sources support incompatible alternatives. |

The Investment Thesis separately configures each criterion as `hard_constraint`, `scored_preference`, or `no_preference`, plus an Unknown policy. For example, “geography does not matter” is `no_preference`; it never changes an unknown geography into `not_applicable`.

Defaults are conservative:

- Unknown under a hard constraint produces an `indeterminate` rule result; policy then maps it to a Needs Information or Manual Review action, not Fail.
- Unknown under a preference contributes nothing and lowers coverage; it is not a penalty.
- No Preference produces Not Evaluated.
- Missing public history affects evidence coverage and confidence, not founder quality.

**Alternatives considered:**

- Nullable fields: rejected because clients cannot distinguish unknown, undisclosed, inapplicable, and conflicted values.
- Treating missing as false or zero: rejected because it structurally disadvantages cold-start founders and contradicts the brief.

### 4. Preserve an evidence graph rather than a mutable profile blob

The durable Memory model is:

```text
Source Artifact ──extracts──▶ Observation
       │                         │
       └──── precise locator ────┴──▶ Evidence
                                            │
                                   supports/refutes
                                            ▼
                                          Claim
                                            │
                           Trust factors / contradictions
                                            ▼
                          Axis / memo / Recommendation
```

Source Artifacts are immutable versions. Observations record what a particular source said at a particular time. Claims are versioned analytical assertions and never overwrite Observations. Evidence is a precise locator such as deck page, captured URL excerpt, repository/commit, paper section, or interview segment. Entity merges preserve aliases, match evidence, and an audit/reversal path.

Historical observations and Founder Score snapshots are append-only. An approved retention or deletion action can remove protected content, but the action remains auditable and dependent derived records become unavailable rather than silently changing truth.

#### Mistral OCR is a narrow ingestion adapter

The SWE owner (`DiaRar`) owns the real Mistral OCR adapter. Intake first validates and privately stores the original PDF, then calls a provider-neutral page-extraction interface. Deterministic tests use a fake extractor; the production adapter uses Mistral's stateless `/v1/ocr` endpoint with a configurable model default of `mistral-ocr-latest`. Each accepted result records the concrete returned model, page index, Markdown text, optional page confidence, usage metadata, input hash, and extraction time so Evidence can cite the immutable original deck and exact page. OCR output is an Observation input, not primary Evidence by itself.

The adapter sends bounded PDF bytes directly in the stateless OCR request and does not use a public deck URL, Mistral Files API, Batch API, or another stateful upload path. `MISTRAL_API_KEY` remains server-side. Real founder-private decks are disabled by default and may be sent only when an explicit runtime policy confirms the approved account's training opt-out, retention or Zero Data Retention posture, permitted region, and collection purpose. Until that confirmation, live calls are limited to fictional or otherwise approved test documents; intake still preserves the original deck and leaves extracted fields Unknown if OCR is unavailable or blocked.

This selection does not approve Mistral or any other provider for market, founder, idea, validation, memo, query-planning, or agentic investment analysis. Those uses remain behind the later human model/orchestration gate.

Official implementation and policy references: [Mistral OCR endpoint](https://docs.mistral.ai/api/endpoint/ocr), [Mistral OCR processor](https://docs.mistral.ai/studio-api/document-processing/basic_ocr), [privacy and data controls](https://docs.mistral.ai/admin/monitor-comply/privacy-data-controls), and [Zero Data Retention eligibility](https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr).

**Alternatives considered:**

- Store only the latest normalized profile: rejected because trends, contradictions, and reproducibility would be lost.
- Treat generated prose as evidence: rejected because models can summarize Evidence but cannot create primary Evidence.

### 5. Select a generic discovery provider through a human gate

Generic web discovery is a true external dependency. The ingestion module owns two small provider-neutral interfaces: discover candidate URLs from a bounded Opportunity Query Plan, and acquire permitted content from selected original URLs. A deterministic fake is implemented first. Canonical domain records never import Tavily, Exa, or other provider response types.

Before adding a provider SDK to the runtime project, the Data/ML workstream preflights credentials, terms, and permitted test data, then evaluates Tavily and Exa on the same human-labeled sourcing corpus and representative compound queries wherever access exists. An unavailable candidate is desk-reviewed and marked `not_live_tested`; lack of access does not silently eliminate it or deadlock the human gate. The comparison records:

- top-result relevance and source diversity;
- performance across people/company, developer, launch, hackathon, research/patent, accelerator, and social-signal queries;
- freshness/date/domain controls and ability to retrieve precise original content;
- stable request, cost, latency, rate-limit, cache, and failure metadata;
- SDK/API ergonomics, structured output, terms, and reproducibility;
- behavior for no results, partial extraction, duplicates, and unsupported pages.

Tavily currently exposes Search, Extract, Crawl, and Map capabilities. Exa currently exposes Search with content/highlight options, domain/date filters, categories including people, company, and research papers, and structured outputs. These capabilities are inputs to a benchmark, not a selection. A human reviewer records exactly one generic provider—Tavily, Exa, or another—or no generic provider as the P0 choice. Only then is the selected generic adapter and SDK added. Running two generic providers is deferred to a separate follow-up change.

Tavily is the current working front-runner, but this is not the human approval required by the gate and no Tavily dependency is added during the shared-contract freeze. OSINT-style multi-source discovery and correlation is a possible stretch direction only; it remains outside P0 until its sources, permitted techniques, privacy/terms constraints, evidence rules, and review boundary are explicitly proposed and approved.

Choosing no generic provider does not invalidate the P0 slice: the human-approved source-specific adapter becomes the bounded live discovery path as well as the authoritative verifier. If no candidate live adapter is accessible at all, the gate is blocked and the change must be revised explicitly rather than pretending a fake run is live.

Regardless of provider:

- original source URLs and acquired content—not provider snippets—anchor Evidence;
- provider relevance is retrieval metadata, never Founder Score or Trust Score;
- no-result responses leave domain facts Unknown;
- source-specific APIs such as GitHub remain preferable for authoritative activity facts;
- runs enforce query/result/depth/domain/time/cost budgets and keep credentials server-side;
- private deck content is never sent without an explicit approved data policy.

Official evaluation references: [Tavily API overview](https://docs.tavily.com/documentation/api-reference/introduction), [Tavily Python SDK](https://docs.tavily.com/sdk/python/reference), [Exa Search API](https://exa.ai/docs/reference/search), and [Exa Python SDK](https://exa.ai/docs/sdks/python-sdk-specification).

**Alternatives considered:**

- Preselect Tavily because access exists: rejected because access does not demonstrate better sourcing quality for this corpus.
- Preselect Exa for semantic/people search features: rejected for the same reason; a representative bake-off is required.
- Make either provider the canonical database: rejected because both are retrieval providers, not the system of record.
- Use a generic provider alone for GitHub-style metrics: rejected because discovery results are less authoritative than a source-specific API or captured source record.
- Abstract every internal dependency behind a port: rejected as shallow indirection. Ports are reserved for true external variation and the eventual orchestration choice; local persistence stays an internal seam until a second real adapter is justified.

### 6. Separate deterministic screening from structured intelligence

The evaluation sequence is:

1. Snapshot canonical inputs and thesis revision.
2. Run versioned viability and thesis rules.
3. Short-circuit expensive analysis on a reliable hard failure when policy allows.
4. Run logical market, idea/quality, founder/team, and validation analyses.
5. Validate every structured output and Evidence reference.
6. Produce the common Assessment Envelope.
7. Synthesize the cited memo and Recommendation.
8. Await a human Decision.

Every deterministic result exposes inputs, knowledge states, rule version, and reason. Human overrides append an event; they do not erase the original result.

Logical intelligence responsibilities are:

- Market analysis: market direction, sizing assumptions, competitors, SWOT.
- Idea novelty and quality: problem/product coherence, novelty Evidence, defensibility, and viability as-is versus pivot potential.
- Founder dossier: sourced skills, milestones, consistency, founder-market fit, and claim clarity.
- Validator/adversarial analysis: corroboration, contradictions, stale sources, unsupported Claims, and diligence gaps.
- Memo synthesis: consumes only accepted structured outputs.

“How founders present themselves” is limited to clarity, consistency, responsiveness, and Evidence quality. Appearance, accent, name, charisma, production polish, protected traits, and socioeconomic proxies are excluded.

These are analysis responsibilities, not a commitment that each must be a separate autonomous agent. Plain Python functions, model calls, or framework nodes may implement them after the human framework gate.

### 7. Standardize the Assessment Envelope

Both preliminary and full evaluation return a versioned envelope shaped conceptually as follows:

```text
AssessmentEnvelope
├── identity: candidate or screening-case subject, opportunity when present, origin, mode, as-of time
├── versions: schema, thesis, rules, score, model, prompt, policy
├── coverage: sources, freshness, missing and conflicted fields
├── deterministic_results[]: pass/fail/indeterminate/not-evaluated
├── founder_score_snapshots[]
├── axes
│   ├── founder: conclusion, trend, confidence, claims, gaps
│   ├── market: bullish/neutral/bear/unknown, trend, claims, gaps
│   └── idea_vs_market: viable/pivotable/weak/unknown, trend, claims, gaps
├── claims[]: trust details and evidence references
├── contradictions[] and diligence_actions[]
├── memo_revision
├── recommendation
└── run timing and human-decision state
```

The three axes are never averaged. Queue priority may use a transparent decision matrix or tier, but every contribution remains visible. Founder Score is a 0–100 heuristic person-level estimate with factors, coverage, qualitative uncertainty, provisional state, and history; it is one input to the Founder Axis. A numerical interval is shown only after documented calibration. Trust Score is a versioned 0–100 Claim-level heuristic of provenance, independence, recency, extraction certainty, corroboration, and contradiction.

The exact scale rubrics require calibration with fixtures and human review; numeric precision must not obscure confidence or coverage.

A versioned Decision Readiness policy evaluates whether the deck is parsed or its failure reviewed, thesis outcomes exist, all axes are present, Founder Score is present, all five memo sections exist, every material Claim is cited or Unsupported, contradictions and gaps are enumerated, and one Recommendation/next action is clear. Policy-blocking Unknown or Conflicted values yield Needs Information or Manual Review unless a human explicitly accepts the documented risk.

### 8. Keep canonical Memory separate from agent run state

Intelligence reads an immutable Memory snapshot. Model messages, scratchpads, and checkpoints are temporary orchestration state. Agents propose schema-validated Claims and assessments; they do not mutate canonical Source Artifacts or Observations directly.

Persist auditable execution data: input snapshot, source references, rule outcomes, tool calls, model/prompt versions, schema validation, accepted outputs, retries, and concise rationales. Do not request, store, or expose private model chain-of-thought. This interprets the brief's “agentic traceability” as an evidence and execution audit trail.

**Alternatives considered:**

- Treat a framework checkpoint store as business Memory: rejected because retries and conversational state are not authoritative founder facts.
- Log raw chain-of-thought: rejected because citations, structured decisions, and validation summaries provide useful auditability without exposing hidden reasoning.

### 9. Enforce a human investment-intelligence model/orchestration-selection gate

Except for the separately approved, ingestion-only Mistral OCR adapter above, no runtime implementation task may select, add, import, or configure an investment-intelligence model provider or LangGraph, LangChain, LlamaIndex, or another agent-orchestration framework until a human coder/reviewer explicitly approves the named choices and records the decision. Plain Python orchestration is included as an option, not merely a fallback; using an analysis model through plain Python still requires the model-provider decision.

Before that gate, the Data/ML owner may run isolated evaluation probes through the framework-neutral harness after a credential, terms, and data-use preflight. These probes do not add SDKs or configuration to the runtime project. They use synthetic or fictional decks and profiles unless a human has explicitly approved a candidate provider's private-data policy. Inaccessible candidates are marked `not_live_tested` and may be desk-reviewed; the gate evidence must make that limitation visible.

Before approval, implementation may complete:

- project scaffolding;
- the domain model and Pydantic contracts;
- the provider-neutral ingestion interfaces, deterministic fake, any already human-approved live adapter, and the source-specific adapter;
- persistence and provenance;
- deterministic thesis rules and natural-language filter schema;
- framework-neutral analysis interfaces, fakes, and evaluation cases;
- REST resources that do not require live model execution.

The reviewer checkpoint compares candidate model providers on structured-output reliability, latency, cost, privacy, and evaluation results, and compares at least plain Python, LangGraph, LangChain, and LlamaIndex on state/checkpointing, human-in-the-loop support, traceability, testing, lock-in, dependency weight, and hackathon delivery risk. Work pauses at the first model- or framework-specific task until approval is recorded in this design or a dedicated ADR.

### 10. Expose a resource-oriented FastAPI adapter

The API uses `/api/v1`, generated OpenAPI, opaque stable identifiers, UTC timestamps, bounded deterministic collections, `application/problem+json` errors, idempotency keys for intake, and `202 Accepted` run resources for long work. Route handlers validate and translate HTTP, then call domain interfaces; they do not implement screening rules.

The P0 transport is deliberately narrower than the domain model: Application intake/status, thesis, sourcing run and candidate activation/outreach, query/screen commands, run status/retry, one nested Opportunity detail read model, and append-only human Decision. Founder, Company, Screening Case, Claim, Evidence, memo, and assessment retain distinct internal identifiers and schemas, but P0 does not require standalone CRUD routes or generic relationship traversal. This lets the SWE build exactly the four demo surfaces while keeping later API expansion compatible.

The MVP access model is deliberately narrow: anonymous, rate-limited minimum Application intake; an unguessable, revocable capability for the bounded founder-status projection; and one configured investor credential for protected resources. This avoids premature accounts, RBAC, and multi-tenancy while keeping private decks and investor analysis non-public.

Natural-language sourcing uses a planner/executor split:

```text
one investor request
        │
        ▼
model-assisted or deterministic Query Planner     [Data/ML]
        │
        ▼
typed Opportunity Query Plan + unresolved spans   [shared contract]
        │
        ▼
schema/allowlist validation + investor preview    [SWE]
        │
        ├──▶ deterministic canonical-data filters [SWE]
        └──▶ bounded provider-neutral retrieval   [selected adapter]
                         │
                         ▼
                 normalize Evidence
                         │
                         ▼
          deterministic thesis/Unknown evaluation
                         │
                         ▼
              optional labeled semantic rerank
```

“One pass” means one investor interaction, not necessarily one internal call or an autonomous agent loop. The MVP starts with a single schema-constrained planning call or deterministic parser. A bounded iterative agent is justified only if evaluation shows that gap-guided query refinement materially improves sourcing. The planner never executes SQL, directly decides eligibility, or treats search silence as proof of a negative; the deterministic executor owns those semantics.

### 11. Use an evidence-first, progressively neumorphic UX

The information architecture covers only:

1. founder Application intake;
   - including a private founder-status capability and focused follow-up;
2. thesis editor;
3. inbound/outbound sourcing queue and candidate activation;
   - including an editable, human-approved outreach draft and recorded contact state;
4. Opportunity detail with distinct scores and trends;
5. Claim/Evidence and contradiction inspection;
6. memo and adversarial view;
7. human Decision and processing timeline.

The linked color-theory video supports three useful principles: choose saturation and brightness for emotional hierarchy, mix environmental colored grays from the palette, and reserve vivid color for a small number of focal points. The UX will therefore use rice-paper/celadon/jade/ink-like semantic surface tokens and limited cinnabar, azure, or deep-jade accents. These are starting directions, not historically authoritative fixed swatches.

Neumorphic shadow depth is a progressive decorative layer for major surface grouping. Interactive controls retain semantic labels, visible boundaries, non-color states, and explicit focus outlines. Forced-colors mode, print, or unsupported shadows must leave the interface fully understandable. Dense tables and evidence views prioritize legibility over softness.

The frontend technology remains open. Whatever is selected must meet the UX spec's WCAG 2.2 AA, keyboard, responsive reflow, forced-colors, and reduced-motion requirements.

**Alternatives considered:**

- Full neumorphism on all controls: rejected because low-contrast boundaries and shadow-only states conflict with analytical density and accessibility.
- Generic neutral gray plus bright blue: rejected as visually sterile and unrelated to the requested design direction.
- Decorative Chinese motifs: not required; the design takes palette and hierarchy principles rather than turning the interface into cultural pastiche.

### 12. Test at module interfaces and against decision fixtures

The module interface is the primary test surface. Planned verification includes:

- ingestion contract tests shared by the fake and human-selected provider adapters;
- idempotency, artifact-version, identity-resolution, and KnowledgeValue tests;
- deterministic thesis-rule tables covering Known, Unknown, Not Disclosed, Not Applicable, and Conflicted inputs;
- seeded contradictory profiles and cold-start founder fixtures;
- Assessment Envelope schema and evidence-reference validation;
- OpenAPI and API end-to-end tests from Application or discovery through Decision;
- model-output rejection tests through framework-neutral fakes;
- memo citation and gap-preservation tests;
- automated and manual keyboard, contrast, reflow, forced-colors, and reduced-motion UX checks.

Tests assert observable outcomes through interfaces rather than internal agent topology or provider payloads. This keeps them valid if the selected discovery provider, persistence, model, or orchestration implementation changes.

### 13. Use one OpenSpec change with two contract-first workstreams

This MVP remains one OpenSpec change because both developers are implementing one observable Sourcing → Screening → Diligence → Decision slice and share the same domain language, Evidence model, Assessment Envelope, and API contract. Two changes would duplicate those contracts and create ambiguous archive/merge order. Parallelism happens through branches or git worktrees and small PRs, not duplicated specifications.

```text
                  1. SWE backend scaffold
                           │
                           ▼
               2. paired v0 contract freeze
        KnowledgeValue / Evidence / QueryPlan / Envelope
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       SWE/platform lane          Data/ML lane
       persistence/files          sourcing corpus
       FastAPI + fake runs        provider bake-off
       deterministic executor     score/trust rubrics
       founder/investor UX        planners/analyses/evals
              │                         │
              └────────────┬────────────┘
                           ▼
                  contract-test merges
                           ▼
              provider gate / framework gate
                           ▼
                    live vertical slice
```

**Shared, paired-review contracts:**

- `CONTEXT.md` and OpenSpec artifacts;
- `domain/` types and lifecycle invariants;
- provider-neutral discovery/acquisition results;
- Source Artifact / Observation / Claim / Evidence;
- Opportunity Query Plan and allowlisted filter vocabulary;
- Founder Score/Trust rubrics, axes, Assessment Envelope, and Decision Readiness;
- OpenAPI schemas and deterministic cross-lane fixtures.

**SWE primary ownership:** project scaffold, SQLite/artifact implementation, HTTP/security/run semantics, deterministic query execution, fake-backed integration, founder/investor UX, and deployment documentation.

**Data/ML primary ownership:** sourcing hypotheses and labeled corpus, Tavily-versus-Exa benchmark, source/entity extraction experiments, deterministic factor rubrics, query planning, logical analysis interfaces/adapters, prompt/model evaluation, and contradiction/Trust evaluation.

**Synchronization protocol:**

1. The SWE lands the scaffold first; both developers immediately pair on the smallest v0 contract PR.
2. After the planning artifacts, scaffold, v0 contracts, fixtures, and contract tests are committed to `main` through the team's normal user-approved Git workflow, create `work/swe-platform` from that exact shared commit. For the initial two-developer sprint, `main` is both the Data/ML lane and shared integration branch; strict file ownership and paired review protect shared contracts. A separate Data/ML feature branch may replace this temporary arrangement in a later workflow change.
3. Each PR cites OpenSpec requirement/scenario names and task IDs, includes interface-level tests, and stays small enough to merge at least daily.
4. Shared-contract behavior changes update this OpenSpec change first, receive both reviewers' approval, increment the schema/policy version, and land before dependent lane changes.
5. SWE develops API/UX against deterministic fakes; Data/ML makes live adapters satisfy the same contract suite. Neither lane imports the other's implementation internals.
6. Rebase `work/swe-platform` after each shared-contract change on `main` and run the combined contract suite before merging SWE work back to `main`.
7. The SWE owner is the initial integrator and checks off `tasks.md` only after the corresponding work is present and verified on `main`; lane commits reference task and scenario IDs but do not compete to edit checkbox state.

**Joint integration checkpoints:**

- I1: fake end-to-end Application and Outbound Candidate to human Decision;
- I2: approved live adapter path plus deterministic/import coverage for any remaining demo categories;
- I3: direct inbound and activated-then-applied outbound converge on full Screening;
- I4: cold-start, contradiction, partial-provider-failure, founder-status, and outreach-draft demo.

Only create a second OpenSpec change later for a behaviorally separable follow-up, such as production multi-tenancy or adding a second provider after the MVP—not for developer assignment.

## Risks / Trade-offs

- **Scope explosion across sources and agents** → Build against fakes from the contract freeze, demonstrate at most one human-selected generic provider plus one source-specific path—or only the source-specific path when no generic provider is selected—bound all runs, and move hardening into a later OpenSpec change.
- **Public-data absence becomes a quality penalty** → Keep coverage separate, use neutral provisional Founder Score factors, widen uncertainty, and test cold-start cases explicitly.
- **Entity-resolution mistakes merge different founders** → Require match evidence, manual review for ambiguity, preserved aliases, and reversible merge events.
- **False precision in Founder or Trust Scores** → Display factors, version, coverage, qualitative uncertainty, and snapshots; show numerical intervals only after calibration.
- **Hallucinated provenance** → Only ingested Source Artifacts can anchor Evidence; reject unsupported generated Claims.
- **Generic-provider cost, latency, or failure** → Select against the labeled corpus, enforce budgets, cache by normalized request/source version, use bounded retries, record partial failure, and keep fake/source-specific adapters.
- **Framework lock-in delays the hackathon** → Complete domain, ingestion, deterministic rules, and fakes first; require the explicit human gate before any framework dependency.
- **“Memory” ambiguity contaminates source truth** → Keep canonical Memory authoritative and temporary orchestration state disposable.
- **Founder-presentation analysis encodes bias** → Limit it to claim clarity and Evidence consistency; prohibit appearance, polish, protected traits, and proxies.
- **Neumorphism harms accessibility or density** → Use it only for progressive grouping; require borders, focus, contrast, semantic HTML, non-color states, and forced-colors behavior.
- **Private decks or provider credentials leak** → Validate uploads, protect file routes, classify data before external transfer, keep secrets server-side, and redact telemetry.
- **A recommendation is mistaken for autonomous investment** → Keep Recommendation and human Decision distinct in domain, API, and UX; exclude fund transfer entirely.
- **SQLite or a single process limits scale** → Acceptable for a hackathon MVP; keep domain interfaces and immutable records portable while avoiding premature distributed infrastructure.
- **Parallel branches drift at shared schemas** → Pair-review and version shared contracts first, merge small PRs daily, and require both lanes to run the same contract fixtures.

## Migration Plan

This is a greenfield change, so migration means staged construction rather than live data conversion:

1. Scaffold the backend and verify an empty FastAPI service through `uv`.
2. Review and commit the OpenSpec artifacts, schema shapes/version identifiers, deterministic fixtures, and shared contract tests through the team's normal user-approved Git workflow.
3. Create `work/swe-platform` from the shared `main` commit; the SWE works there while the Data/ML owner uses the strictly owned paths on `main`, and both lanes build against deterministic fakes before meeting again at I1.
4. Preflight and benchmark Tavily and Exa through the provider-neutral interface where access exists; mark unavailable candidates `not_live_tested`, stop for the human provider decision, and add at most one approved generic runtime adapter.
5. In parallel, add local persistence/deck intake/API/UX and sourcing corpus/thesis rules/query planning/Assessment fakes; integrate Mistral OCR through the provider-neutral extractor using fictional inputs until private-deck controls are explicitly enabled.
6. Integrate the selected provider and source-specific verification at checkpoint I2.
7. Stop at the human framework-selection gate.
8. After approval, add model-backed logical analyses, validation, memo synthesis, and tracing.
9. Complete checkpoints I3/I4 and run contract, seeded-contradiction, cold-start, accessibility, founder-status, and timing demonstrations.

Each stage remains runnable with deterministic fixtures. External adapters and model-backed analysis can be disabled to fall back to the last deterministic state. Schema and score revisions are append-only, so rollback selects an earlier version rather than rewriting history.

## Open Questions

1. **Mandatory human decision:** Which investment-intelligence model provider and orchestration option—plain Python, LangGraph, LangChain, LlamaIndex, or another reviewed choice—will be approved after framework-neutral foundations and comparison evidence exist? The ingestion-only Mistral OCR selection does not answer this question.
2. Which frontend technology best fits the team and hackathon deadline while satisfying the UX specification?
3. **Mandatory human decision:** Does the representative sourcing bake-off select exactly one generic provider—Tavily, Exa, or another—or no generic provider for P0? A two-provider runtime belongs in a later change.
4. Which direct/source-specific outbound connector should serve as or complement the live discovery path? GitHub is the leading candidate because it can verify developer activity at the source.
5. What numeric rubric and calibration set should define Founder Score and Claim Trust Score factors without presenting false precision?
6. Which source domains, provider budget, refresh cadence, and retention policy are approved for the demonstration?
7. What minimal follow-up information, if any, is genuinely required after company name plus deck to reach decision readiness confidently?

## Deferred Follow-up Backlog

The following work is deliberately outside `build-vc-brain-mvp`. It must be proposed as one or more later OpenSpec changes rather than being silently continued by an apply agent after the P0 demo:

- production schema migrations, richer retention/deletion workflows, malware-service integration, and comprehensive private-file audit history;
- cursor-stable collection snapshots, standalone CRUD/relationship routes for every domain record, Decision correction/supersession, exhaustive error/rate-limit cases, and broader security testing;
- production scheduling and operational monitoring, broader recurring-source coverage, deeper crawl/map features, expanded source categories, a second generic provider, or policy-reviewed OSINT-style multi-source correlation;
- score and Trust calibration on a larger labeled corpus and numerical intervals only when statistically defensible;
- exhaustive cross-browser and assistive-technology certification beyond the P0 keyboard, contrast, reflow, forced-colors, reduced-motion, and semantic-state acceptance checks;
- production multi-tenancy, portfolio workflows, or learned investment weights.
