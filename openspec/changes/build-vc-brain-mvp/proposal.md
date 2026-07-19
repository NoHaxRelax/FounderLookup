## Why

Promising founders are currently discovered through fragmented signals and evaluated through slow, network-dependent diligence. The hackathon needs a sourcing-first VC Brain that turns inbound applications and outbound public signals into an evidence-backed, decision-ready recommendation within 24 hours while remaining honest about missing data, uncertainty, and contradictions.

## What Changes

- Introduce a durable Memory layer that accepts the minimum inbound application (company name and deck), discovers outbound candidates from heterogeneous public signals, preserves source provenance, resolves identities, deduplicates records, and retains history rather than only the latest snapshot.
- Extract accepted pitch decks through a provider-neutral page-extraction seam with deterministic tests and a human-selected Mistral OCR 4 adapter; keep original decks private, persist the concrete OCR model/version and page locators, and default real private-deck transfer off. Normal deployments require confirmed provider/account controls; this hackathon deployment may instead use a separate explicit human-approved private-processing risk-acceptance flag without falsely claiming ZDR, region, or training settings.
- Introduce a common opportunity-screening capability in which direct inbound Applications and Applications later submitted by activated Outbound Candidates enter the same screening, diligence, and decision funnel.
- Make the investor thesis configurable across sector, stage, geography, check size, ownership target, and risk appetite, with each criterion explicitly configured as a hard constraint, a scored preference, or no preference.
- Combine explainable deterministic eligibility and ranking rules with framework-neutral model/agent analysis. Produce a persistent per-person Founder Score, three independent per-opportunity axes, per-claim Trust Scores, evidence citations, contradictions, explicit unknowns, and a concise investment memo.
- Expose the workflow through a versioned REST API for application intake, sourcing, thesis configuration, screening, evidence inspection, memos, and human decisions.
- Provide a focused public founder intake/status journey that is not part of investor navigation, plus a separate investor workspace that presents the next decision or action first and reveals criteria, evidence, Trust factors, and run diagnostics on demand.
- Instrument elapsed time and failures from first signal or application through decision readiness to demonstrate the 24-hour target.
- Organize implementation as contract-first parallel workstreams for an SWE and a Data/ML specialist, with shared domain schemas, fake adapters, contract tests, and frequent integration through this single OpenSpec change.
- Keep generic web discovery behind a replaceable adapter and implement the human-selected Tavily Search/Extract path with explicit budgets and original-URL provenance; source-specific verification remains available, and a second generic provider is deferred.
- Pass bounded acquired PUBLIC Tavily/source content through the human-selected OpenAI `gpt-5.6-luna` Responses API with strict Structured Outputs, then accept only fields, exact locators/excerpts, and URLs that pass deterministic provenance and public-URL policy validation. Keep the sourcing extractor optional and public-only, record its returned model and usage, retain deterministic fallback, and keep `OPENAI_API_KEY` server-side.
- Broaden outbound sourcing to a wide open-source-intelligence (OSINT) source palette spanning developer activity, scholarly output, patents, launches, technical-community reputation, long-form writing, cohorts, and approved professional and social profiles. Public hackathon and startup-showcase collection preserves the event, project, participant-display-name, repository/demo, explicitly linked public pitch-deck relationships, and source-published public contact routes without hunting for private contact data or silently asserting identity. Each route retains its stable id, kind, label/value, validated optional link target, public classification, source artifact/name/locator, and collection time. The system builds one cross-source-corroborated profile per Founder within public, lawful, and robots- and terms-respecting limits.
- Orchestrate outbound identify → retrieve/structure → assess-gaps → converge as a bounded LangGraph loop with deterministic round/request/time/cost limits, replayable query and Evidence deltas, explicit stop reasons, and no autonomous outreach or investment authority. Provider adapters and acceptance validators remain framework-neutral.
- Grade the Founder Axis and Founder Score with an evidence-weighted trait taxonomy that favors costly-to-fake, peer-validated signals and excludes non-predictive folklore such as charisma, pedigree, youth, and raw team size from founder quality.
- Attach calibrated confidence bands to subjective sub-assessments through repeated reasoned sampling that stays provider-neutral and needs no token log-probabilities, addressing the confidence-scoring research question.
- Express a builder-signal read distinct from a fundability read and surface strong-builder, low-fundability Founders as underrated opportunities.
- Anchor calibration on observed building outcomes rather than funding history, add counterfactual identity-swap bias checks and subgroup calibration, and provide a hold-out predictive-validity evaluation.
- Keep portfolio monitoring, follow-on investing, fund operations, exit-management UI, autonomous outreach, and actual money movement out of this MVP. The human reviewer explicitly selected LangGraph only as a thin outbound retrieval state machine; rebased inbound analysis remains behind framework-neutral specialist ports and does not share graph checkpoints with canonical Memory. For the hackathon demo, the reviewer also explicitly approved OpenAI `gpt-5.6-luna` for bounded structured extraction and model-backed analysis. Founder-private model use requires its own explicit runtime data-class and risk-acceptance gates; the outbound sourcing extractor remains public-only. Neither approval makes generated output authoritative or removes Evidence validation.
- Keep fictional demo seeding off by default. A production-mode demo may enable it only with a second explicit production acknowledgement, and must continue to label every seeded record as fictional.

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
- Introduces external-source adapters; Tavily is the single human-selected P0 generic discovery/content provider, while Exa is deferred and source-specific APIs plus fixtures remain necessary for authoritative and reproducible signals.
- Selects Mistral OCR 4 for page-addressable deck extraction behind a provider-neutral seam while keeping real private-deck transfer opt-in and policy controlled.
- Selects OpenAI `gpt-5.6-luna` behind public sourcing extraction and the framework-neutral investment-intelligence seam for strict schema-constrained output, with `store=false` where supported, explicit private-data and demo-risk enablement outside public sourcing, deterministic validation/fallback, and no autonomous Decision authority; selects LangGraph only for the bounded outbound retrieval state machine.
- Creates privacy, licensing, bias, cost, security, and audit obligations for public founder data, uploaded decks, external APIs, and generated analysis.
