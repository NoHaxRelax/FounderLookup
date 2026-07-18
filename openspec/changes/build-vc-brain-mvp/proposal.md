## Why

Promising founders are currently discovered through fragmented signals and evaluated through slow, network-dependent diligence. The hackathon needs a sourcing-first VC Brain that turns inbound applications and outbound public signals into an evidence-backed, decision-ready recommendation within 24 hours while remaining honest about missing data, uncertainty, and contradictions.

## What Changes

- Introduce a durable Memory layer that accepts the minimum inbound application (company name and deck), discovers outbound candidates from heterogeneous public signals, preserves source provenance, resolves identities, deduplicates records, and retains history rather than only the latest snapshot.
- Introduce a common opportunity-screening capability in which direct inbound Applications and Applications later submitted by activated Outbound Candidates enter the same screening, diligence, and decision funnel.
- Make the investor thesis configurable across sector, stage, geography, check size, ownership target, and risk appetite, with each criterion explicitly configured as a hard constraint, a scored preference, or no preference.
- Combine explainable deterministic eligibility and ranking rules with framework-neutral model/agent analysis. Produce a persistent per-person Founder Score, three independent per-opportunity axes, per-claim Trust Scores, evidence citations, contradictions, explicit unknowns, and a concise investment memo.
- Expose the workflow through a versioned REST API for application intake, sourcing, thesis configuration, screening, evidence inspection, memos, and human decisions.
- Provide an accessible founder intake and investor workspace that makes rankings, trends, axis disagreement, evidence, uncertainty, and next actions understandable to a non-technical investor.
- Instrument elapsed time and failures from first signal or application through decision readiness to demonstrate the 24-hour target.
- Organize implementation as contract-first parallel workstreams for an SWE and a Data/ML specialist, with shared domain schemas, fake adapters, contract tests, and frequent integration through this single OpenSpec change.
- Keep generic web discovery behind a replaceable adapter and require a recorded human selection before committing P0 to exactly one of Tavily, Exa, another provider, or no generic provider; a source-specific live path remains available, and a two-provider runtime is deferred.
- Keep portfolio monitoring, follow-on investing, fund operations, exit-management UI, autonomous outreach, actual money movement, and any unreviewed commitment to a model provider or agent-orchestration framework out of this MVP.

## Capabilities

### New Capabilities

- `opportunity-data-ingestion`: Inbound and outbound collection, source preservation, identity resolution, normalization, deduplication, enrichment, historical Memory, and data-quality state.
- `opportunity-screening`: Thesis-aware deterministic filtering and framework-neutral intelligence that produces structured, evidence-backed founder and opportunity assessments, diligence gaps, memos, and recommendations.
- `investor-rest-api`: Versioned HTTP contracts that connect intake, sourcing, Memory, screening, evidence, memos, and human decisions without exposing provider-specific internals.
- `investment-workflow-ux`: Accessible founder intake and investor-facing sourcing, screening, diligence, and decision experiences with an evidence-first visual hierarchy.

### Modified Capabilities

None.

## Impact

- Establishes the initial application architecture and shared domain contracts for founders, companies, opportunities, thesis criteria, evidence, claims, assessments, pipeline runs, memos, and decisions.
- Adds a Python backend built and managed with a project-local `pyproject.toml`, `uv`, and FastAPI, plus future frontend code selected independently of this proposal.
- Introduces external-source adapters; Tavily and Exa are candidate web discovery/content providers subject to a human-reviewed bake-off, while source-specific APIs and fixtures remain necessary for authoritative and reproducible signals.
- Introduces model-provider and agent-orchestration seams but deliberately blocks framework-specific implementation until a human coder/reviewer selects an approach.
- Creates privacy, licensing, bias, cost, security, and audit obligations for public founder data, uploaded decks, external APIs, and generated analysis.
