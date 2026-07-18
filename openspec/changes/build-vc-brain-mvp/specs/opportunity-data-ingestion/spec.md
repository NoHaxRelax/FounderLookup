## ADDED Requirements

### Requirement: Canonical Memory separates people, companies, and opportunities
The system SHALL maintain stable, distinct records for Founders, Companies, Outbound Candidates, Applications, Opportunities, Screening Cases, Source Artifacts, Observations, Claims, Evidence, and score snapshots. Each record SHALL retain its origin and relationships without treating a company profile, founder identity, application, and investment opportunity as interchangeable.

#### Scenario: One founder builds multiple companies
- **WHEN** sourced records establish that the same Founder is associated with two Companies over time
- **THEN** the system links both Companies to one Founder while preserving separate Company and Opportunity histories

#### Scenario: Inbound and outbound records refer to the same company
- **WHEN** an Application matches an existing Outbound Candidate with sufficient identity evidence
- **THEN** the system links both origins to the same canonical Founder and Company without discarding either source history

### Requirement: Inbound intake accepts the PRD minimum
The system SHALL accept a company name and a valid pitch deck as the only mandatory inbound Application fields. It SHALL treat additional founder, company, traction, financing, and contact fields as optional unless a later human-approved requirement demonstrates that they are necessary for a confident decision.

#### Scenario: Minimum application is submitted
- **WHEN** a founder submits a company name and a valid pitch deck with no optional fields
- **THEN** the system accepts the Application and records every merely unprovided applicable field as Unknown rather than rejecting the submission; Not Disclosed is reserved for established intentional withholding

#### Scenario: Original deck is retained
- **WHEN** the system accepts a pitch deck
- **THEN** it stores an immutable Source Artifact with the original filename, media type, content hash, receipt time, and page-addressable extracted representation

#### Scenario: Invalid upload is isolated
- **WHEN** an uploaded file fails configured type, size, malware, or parse validation
- **THEN** the system rejects or quarantines that file with a safe, actionable reason and does not begin screening from its untrusted contents

### Requirement: Outbound sourcing discovers candidates before fundraising
The system SHALL support bounded on-demand collection from heterogeneous outbound source categories including developer activity, product launches, hackathons, research or patents, accelerator cohorts, and approved public social signals, plus one configurable recurring or cron-compatible trigger for at least one approved source. The MVP demonstration SHALL include at least one human-selected live adapter—generic or source-specific—and an independent deterministic fixture or import. If a generic provider is selected, a source-specific path SHALL verify at least one authoritative signal so that the demonstration is not dependent on one opaque provider.

#### Scenario: Public signal creates a candidate
- **WHEN** an outbound run finds a source-backed signal about a previously unknown Founder, team, or Company
- **THEN** the system creates or updates an Outbound Candidate with source coverage and freshness without creating or linking an Application

#### Scenario: Activated candidate submits an application
- **WHEN** an activated Outbound Candidate later submits the minimum Application
- **THEN** the system links the Application to the existing candidate evidence and creates the same kind of Screening Case used for a direct inbound Application

#### Scenario: Discovery does not imply consent or investment
- **WHEN** the system creates an Outbound Candidate from public data
- **THEN** it does not mark the candidate as an applicant, record founder consent, send outreach, or create an investment Decision automatically

#### Scenario: Public social evidence is discovered
- **WHEN** an approved public social-traction record is supplied through a conforming discovery or source adapter
- **THEN** the system preserves it as a time-stamped Source Artifact with its data classification, origin, exact locator, and subject relationship

#### Scenario: Recurring scan refreshes Memory
- **WHEN** the configured recurring trigger invokes the same approved source twice
- **THEN** both runs remain observable, unchanged artifacts are not duplicated, and any changed source content creates a new time-stamped version linked to its history

### Requirement: Memory accepts post-discovery enrichment
The system SHALL ingest approved interviews, follow-up answers, launches, milestones, and other diligence material after discovery or Application while preserving the same Source Artifact, Observation, Evidence, knowledge-state, and provenance rules.

#### Scenario: Focused founder interview is recorded
- **WHEN** a Founder supplies a focused interview answer to resolve a material cold-start or diligence gap
- **THEN** the system stores the transcript or structured answer as a time-stamped Source Artifact with speaker, consent/classification, exact locator, and links to the affected Claims

### Requirement: Sourcing strategy is measurable
The MVP SHALL evaluate a versioned sourcing hypothesis against a human-labeled corpus of at least five candidate examples spanning at least three early-signal categories. Each surfaced candidate SHALL expose the pre-fundraising trigger, original-source locator, freshness, thesis Match, Mismatch, or Unknown outcomes, coverage, identity-resolution result, and an evidence-backed reason to contact. The evaluation SHALL record top-result relevance labels so a human can compare provider and query-plan choices.

#### Scenario: Candidate combines independent signals
- **WHEN** a candidate is supported by credible signals from two independent early-signal categories
- **THEN** the sourcing result preserves both sources and explains how their corroboration affected priority without collapsing them into one opaque provider score

#### Scenario: Duplicate appears across channels
- **WHEN** the same Founder or Company appears in multiple sourcing channels
- **THEN** the evaluation shows whether the records linked, stayed separate, or entered identity review and cites the identity evidence used

#### Scenario: Provider bake-off is reviewed
- **WHEN** Tavily, Exa, or another candidate provider is evaluated against the labeled sourcing corpus
- **THEN** a human reviewer can compare relevance, source diversity, freshness, provenance quality, latency, failure behavior, and cost using the same provider-neutral result contract, with unavailable live candidates visibly marked `not_live_tested`

### Requirement: External collection uses replaceable adapters
The system SHALL place each true external source behind a provider-neutral ingestion interface with production and test adapters. Canonical domain records and tests MUST NOT depend on Tavily-, Exa-, or other provider-specific response types.

#### Scenario: Generic provider discovers an original source
- **WHEN** the configured web discovery provider returns a candidate page
- **THEN** the system records the underlying source URL as a discovery lead and records provider operation metadata separately from source evidence

#### Scenario: Generic provider acquires source content
- **WHEN** the configured content provider successfully retrieves an allowed public page
- **THEN** the system creates a Source Artifact for that original page with retrieval metadata, a content hash, and any available precise content locator

#### Scenario: Provider is replaced in a contract test
- **WHEN** a fake outbound adapter supplies the same provider-neutral collection result as a live adapter
- **THEN** ingestion produces domain-equivalent records without a change to the ingestion caller

#### Scenario: Human-selected provider changes
- **WHEN** a human-approved configuration changes from one conforming discovery provider to another
- **THEN** downstream canonical records, provenance rules, and ingestion callers retain the same domain contract while new collection events identify the new provider

### Requirement: Every observation preserves provenance
Every normalized Observation SHALL reference the Source Artifact from which it was extracted and SHALL record the source or document locator, retrieval time, source event or publication time when known, extraction method and version, and verification state. Generated summaries and search-result relevance scores MUST NOT be treated as primary evidence.

#### Scenario: Deck claim is cited
- **WHEN** the system extracts a revenue claim from a pitch deck
- **THEN** the Observation and resulting Evidence identify the deck version and exact page or slide location

#### Scenario: Web fact is cited
- **WHEN** the system extracts a founder milestone from a public page
- **THEN** the Observation identifies the original URL, captured source version, retrieval time, and precise excerpt or locator

#### Scenario: Publication time is unavailable
- **WHEN** a source has no reliable publication or event timestamp
- **THEN** the system records that timestamp as Unknown while still recording when the source was retrieved

### Requirement: Knowledge states are explicit and queryable
Each applicable normalized field SHALL carry one of Known, Unknown, Not Disclosed, Not Applicable, or Conflicted rather than using ambiguous nulls or silently coercing missing values to false, zero, or an empty string. Not Applicable SHALL require a reason. Conflicted SHALL retain the incompatible sourced values.

#### Scenario: Geography is not found
- **WHEN** no reliable source establishes a Company's geography
- **THEN** the system records geography as Unknown and does not interpret it as outside the investor thesis

#### Scenario: Investor does not care about geography
- **WHEN** a thesis configures geography as No Preference
- **THEN** the underlying known or unknown geography remains unchanged because No Preference is a thesis policy rather than a data state

#### Scenario: Confidential field is withheld
- **WHEN** a founder explicitly does not provide a cap table
- **THEN** the system records the cap table as Not Disclosed and does not fabricate or silently omit it

#### Scenario: Sources disagree
- **WHEN** credible sources provide incompatible values for the same field and time period
- **THEN** the system records the value as Conflicted and preserves each source-backed alternative for review

### Requirement: Ingestion is idempotent and version preserving
The system SHALL use stable external identifiers and content hashes to make repeated ingestion idempotent. Changed source content SHALL create a new version linked to the previous version rather than overwriting historical data.

#### Scenario: Identical artifact is ingested twice
- **WHEN** the same source artifact and content hash are received more than once
- **THEN** the system reuses the existing artifact and records the repeated collection event without duplicating its normalized facts

#### Scenario: Source content changes
- **WHEN** a previously collected URL or document is acquired with a different content hash
- **THEN** the system creates a new time-stamped artifact version and retains the previous version and its derived observations

### Requirement: Entity resolution is evidence backed and reversible
The system SHALL deduplicate source records through versioned identity-resolution rules and SHALL preserve source aliases and match evidence. It MUST route ambiguous matches to human review rather than silently merging identities, and any approved merge SHALL remain auditable and reversible.

#### Scenario: High-confidence duplicate is resolved
- **WHEN** two records share sufficiently strong stable identifiers under the active resolution policy
- **THEN** the system links them to one canonical entity and records the rule version and match evidence

#### Scenario: Names collide without enough evidence
- **WHEN** two founders share a name but lack sufficient corroborating identity attributes
- **THEN** the system leaves them separate and creates an identity-review item

### Requirement: Memory preserves history and trends
The system SHALL append time-stamped observations, milestones, relationship changes, and score snapshots without resetting a Founder's history when a new Company, Application, or Opportunity appears. Retention or deletion required by an approved data policy SHALL be recorded as an auditable event rather than an invisible overwrite.

#### Scenario: Founder returns with a new application
- **WHEN** an identified Founder submits an Application for a later Company
- **THEN** the new Screening Case can reference the Founder's prior evidence and score history while keeping the new Opportunity assessment separate

#### Scenario: Milestone changes a tracked value
- **WHEN** new evidence changes a previously known traction value
- **THEN** the system stores a dated observation and makes both the earlier and later values available for trend calculation

### Requirement: Collection coverage does not become founder quality
The system SHALL report source coverage, freshness, extraction certainty, and unresolved gaps separately from founder or opportunity quality. Missing GitHub, funding, network, social, or other public history MUST NOT be converted into a negative fact or a zero-valued quality feature.

#### Scenario: Cold-start founder has only an application deck
- **WHEN** a first-time Founder has no discoverable public track record but submits the minimum Application
- **THEN** the system records low public-source coverage and continues the common workflow without creating negative observations for absent sources

#### Scenario: Search returns no results
- **WHEN** the configured discovery provider returns no result for a possible traction, competitor, or founder signal
- **THEN** the relevant field remains Unknown and the no-result query is retained only as collection telemetry

### Requirement: Ingestion runs are bounded and observable
Every ingestion run SHALL expose a stable run identifier, status, source operations, counts, elapsed time, retries, partial failures, and provider usage or cost when available. Runs SHALL enforce configured query, page, depth, time, and cost budgets, and one source failure SHALL NOT erase successfully ingested records from other sources. An authorized retry SHALL preserve accepted artifacts and resume from the last safe stage or create a linked retry run against the same identifiable input snapshot.

#### Scenario: One provider fails during enrichment
- **WHEN** an external provider times out after bounded retries while other sources succeed
- **THEN** the run completes with Partially Succeeded status, persists successful artifacts, and records the failed operation without making a negative domain inference

#### Scenario: Run reaches its budget
- **WHEN** an outbound run reaches a configured request, crawl-depth, time, or cost limit
- **THEN** the system stops further external collection, records the limiting budget, and leaves uncollected fields Unknown

#### Scenario: Investor retries a partial run
- **WHEN** an authorized investor retries a partially succeeded or failed ingestion run
- **THEN** the system retains previously accepted Source Artifacts, records the retry relationship and attempt, and does not duplicate successful ingestion

### Requirement: Sensitive data and credentials remain controlled
The system SHALL keep external-provider credentials server-side, restrict access to non-public uploaded Source Artifacts, and apply an explicit allow/deny and retention policy before sending content to a third party. Public availability alone MUST NOT remove the need to record source terms, data classification, and collection purpose.

#### Scenario: Pitch deck enrichment is requested
- **WHEN** an ingestion step would send private deck content to an external provider not approved for that data class
- **THEN** the system blocks the transfer and records a policy failure without leaking the content

#### Scenario: API response references an external provider
- **WHEN** a client retrieves ingestion status or evidence
- **THEN** no provider secret or private internal credential is included in the response
