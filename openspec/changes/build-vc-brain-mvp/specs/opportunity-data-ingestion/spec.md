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

#### Scenario: Mistral OCR extracts an approved deck
- **WHEN** policy permits the selected Mistral OCR 4 adapter to process an accepted deck
- **THEN** the system uses the provider-neutral extractor interface, records the input hash, concrete returned OCR model, extraction time, usage, and ordered page indexes with Markdown and available confidence, and keeps every page linked to the immutable private Source Artifact rather than treating generated text as primary Evidence

#### Scenario: External OCR is unavailable or disallowed
- **WHEN** Mistral OCR fails, the account's required data controls are not confirmed, or policy disallows external processing for the deck's classification
- **THEN** the original deck remains privately stored, the extraction stage records a safe blocked or failed result, and decision-relevant extracted fields remain Unknown without fabricating content

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

#### Scenario: Public hackathon showcase exposes a project and deck
- **WHEN** an approved public event, cohort, project-gallery, finalist, winner, or demo page explicitly publishes a project or team, participant display names or public profiles, and a pitch-deck, repository, or demo link
- **THEN** the system preserves the event-to-project-to-participant and linked-artifact relationships with exact source locators, treats participant identity as unverified until corroborated, and may acquire the public linked artifact only within the configured URL, media, request, page, byte, time, terms, and robots policies

#### Scenario: Public showcase omits a deck or participant
- **WHEN** an approved hackathon or startup-showcase source does not explicitly publish a deck or participant identity
- **THEN** the missing relationship remains Unknown, no contact detail is sought, and the absence does not lower founder or project quality

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

### Requirement: Outbound discovery uses a bounded agentic retrieval loop
The outbound sourcing path SHALL use the selected thin LangGraph orchestration to move from an investor-approved query plan through provider-neutral retrieval, schema-constrained extraction, explicit Evidence-gap assessment, and convergence. The graph SHALL enforce configured round, request, page, elapsed-time, and cost budgets; retain every query, accepted artifact, gap, partial failure, and stop reason; and stop when Evidence is sufficient, a round adds no new accepted Evidence, a budget is exhausted, or a provider fails without a useful retry path. Provider adapters, canonical validation, and domain scoring SHALL remain callable and testable without LangGraph. The loop MUST NOT autonomously send outreach, infer private contact details, verify a person from a display name, create a human Decision, or transfer funds.

#### Scenario: Evidence gap produces one bounded follow-up round
- **WHEN** the first retrieval round finds a project and public demo but an explicitly required repository or public contact route remains Unknown
- **THEN** the graph may issue a validated follow-up query within budget, records its reason and result, and then either converges or records the remaining gap

#### Scenario: Retrieval stops adding Evidence
- **WHEN** a graph round yields no new deterministically accepted Evidence
- **THEN** the graph stops with `no_new_evidence`, preserves prior artifacts, and leaves unresolved fields Unknown rather than searching indefinitely

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

The selected Mistral OCR adapter SHALL use a bounded stateless OCR request rather than a public deck URL or stateful file/batch upload. Founder-private transfer SHALL require explicit runtime data-class enablement rather than following from the presence of a key. The normal Mistral path SHALL require confirmed training-use, retention, region, and purpose controls. For this hackathon MVP the human reviewer has instead approved a separate demo-only private-processing risk-acceptance override; that override SHALL still require OCR to be enabled, private transfer to be allowed, and an explicit OCR purpose to be present and confirmed. It MUST preserve training, retention/Zero Data Retention, and region controls as unknown rather than falsely recording them as confirmed. The outbound OpenAI sourcing extractor SHALL remain PUBLIC-only regardless of generic OpenAI private-use settings; a separate investment-analysis adapter MAY accept founder-private content only when both its private-data and hackathon-risk gates are explicit. Original bytes remain private, requests use bounded/stateless or `store=false` modes where supported, and generated output remains derived data subject to Evidence validation.

#### Scenario: Pitch deck enrichment is requested
- **WHEN** an ingestion step would send private deck content to an external provider not approved for that data class
- **THEN** the system blocks the transfer and records a policy failure without leaking the content

#### Scenario: Hackathon private OCR risk is accepted
- **WHEN** the demo enables Mistral OCR, private transfer, an explicit confirmed OCR purpose, and the separate hackathon private-risk acknowledgement
- **THEN** founder-private OCR may proceed within its byte/page/time bounds while training opt-out, retention/Zero Data Retention, and region remain unconfirmed unless independently configured

#### Scenario: API response references an external provider
- **WHEN** a client retrieves ingestion status or evidence
- **THEN** no provider secret or private internal credential is included in the response

### Requirement: Outbound sourcing draws on a broad OSINT source palette
The system SHALL support open-source-intelligence (OSINT) collection across many independent public source categories rather than only developer activity and professional profiles. Supported categories SHALL include developer activity, scholarly and research output, patents, company and regulatory and court-filing registries, product launches, technical-community reputation, long-form authored writing, accelerator and hackathon cohorts and startup showcases, and approved professional and social profiles. Hackathon/showcase normalization SHALL preserve the event or cohort, project/team, explicitly public participant display names/profile links, explicitly linked public-pitch-deck, repository, and demo relationships, and source-published public contact routes useful for human follow-up. Each contact route SHALL retain a stable route id, kind (`website`, `contact_page`, `public_email`, `public_profile`, or `other`), label, exact value, optional validated link target, `public` classification, source artifact id, source display name, exact source locator, and collection time. The system MUST NOT guess, enrich, or hunt for private contact details or convert a display-name assertion into a verified Founder identity. Each category SHALL be collected through the provider-neutral ingestion interface, SHALL preserve original-source provenance, and one category's absence MUST NOT become a negative fact. OSINT collection SHALL serve both outbound discovery of new Outbound Candidates and enrichment or verification of inbound Applications.

#### Scenario: Multiple OSINT categories enrich one candidate
- **WHEN** outbound collection finds source-backed signals for one Outbound Candidate across three or more independent categories
- **THEN** the system records each as a separately provenanced Source Artifact and builds one candidate profile without collapsing them into a single opaque score

#### Scenario: A source category is unavailable
- **WHEN** an OSINT category cannot be collected because of no access, no result, or a disallowed source
- **THEN** the affected fields remain Unknown, the gap lowers coverage only, and no negative Observation is created

#### Scenario: Authoritative source is preferred over a generic snippet
- **WHEN** a source-specific API can supply an authoritative activity or research fact
- **THEN** the system records it as primary Evidence in preference to a generic web-discovery snippet

#### Scenario: Linked public pitch deck is acquired
- **WHEN** an approved, acquired hackathon or startup-showcase page contains an explicitly labeled public pitch-deck URL and collection budget remains
- **THEN** the system policy-checks and acquires that exact original URL as a separate immutable Source Artifact linked to the project; a search snippet, inaccessible URL, or unlabeled document is not promoted to pitch-deck Evidence

#### Scenario: Source publishes a public follow-up route
- **WHEN** an approved acquired source explicitly publishes a project website, contact page, public email address, or public profile for the project or participant display name
- **THEN** the system may expose that route to the investor with the complete public provenance contract and unverified-identity label, while absent routes remain Unknown and trigger no private-data search

### Requirement: Acquired content may use schema-constrained semantic extraction
The system MAY pass a bounded acquired PUBLIC page through OpenAI `gpt-5.6-luna` using strict Structured Outputs. The request SHALL keep credentials server-side, set `store=false`, cap input/output/time/response bytes, and record the requested and returned model, schema/adapter version, usage, and safe failure metadata. The output SHALL distinguish missing fields from known values and SHALL include exact input-backed excerpts or locators for every emitted field and URL. Deterministic validation MUST reject non-public Source Artifacts, unsupported excerpts, unsafe or absent URLs, identity verification not present in the source, and malformed, refused, incomplete, or over-budget output. A model failure MUST preserve accepted artifacts and fall back to deterministic parsing or explicit Unknown values.

#### Scenario: Structured extraction finds showcase relationships
- **WHEN** a bounded Tavily-acquired showcase page explicitly contains an event, project, participant display names, repository, demo, and public deck link
- **THEN** strict structured output proposes those fields with input-backed locators, deterministic validation accepts only exact supported relationships, and participant identities remain unverified

#### Scenario: Structured output invents a field or URL
- **WHEN** the model returns content that cannot be found in the immutable acquired input or a URL that fails public-source policy
- **THEN** the system rejects that value or the whole output safely, records validation telemetry, and does not persist it as Evidence

#### Scenario: OSINT enriches an inbound application
- **WHEN** a founder submits an inbound Application with a company name and deck
- **THEN** the same OSINT collection may corroborate or contradict the Application's Claims against public Evidence rather than being reserved for outbound discovery

#### Scenario: Registry confirms a prior venture
- **WHEN** a company or court-filing registry records a Founder's earlier company, role, or incorporation
- **THEN** the system stores it as provenanced Evidence available to the founder track-record Claims without inferring outcome quality from the registry alone

### Requirement: Cross-source corroboration resolves one person from many footprints
The system SHALL attempt to link OSINT signals about the same Founder or Company across independent sources into one canonical identity using evidence-backed, reversible identity resolution, and SHALL treat agreement between independent sources as corroboration that raises Claim Trust and sourcing priority. A Claim supported by only one source SHALL be marked single-source, and corroboration MUST NOT be inferred from a generic provider's own relevance ranking.

#### Scenario: Same founder is found across independent sources
- **WHEN** independent sources describe the same Founder with sufficient matching identity Evidence
- **THEN** the system links them to one canonical Founder, preserves each alias and source, and records the corroboration on the affected Claims

#### Scenario: Independent sources agree on a fact
- **WHEN** two independent Source Artifacts support the same Claim
- **THEN** the Claim's Trust Score records multi-source corroboration and references both Evidence locators

#### Scenario: Cross-source identity is ambiguous
- **WHEN** footprints share a name but lack sufficient corroborating identity attributes
- **THEN** the system keeps them separate and creates an identity-review item rather than merging different people

#### Scenario: OSINT signals deduplicate two candidate records
- **WHEN** two Outbound Candidate or Application records are linked by shared cross-source OSINT identity signals such as a matching handle, verified profile, or registry identifier
- **THEN** the system treats those signals as match Evidence, resolves the records to one canonical Founder or Company under the reversible identity-resolution policy, and preserves both source histories

### Requirement: OSINT collection stays within public, lawful, and ethical limits
The system SHALL collect only publicly available information, SHALL respect each source's terms of service and robots directives, and MUST NOT access authentication-walled or private data or attempt to re-identify individuals from data they did not make public. Data classification and an allow or deny policy SHALL be applied before any content is sent to a third party, credentials SHALL remain server-side, and the collection purpose and source terms SHALL be recorded for every OSINT Source Artifact.

#### Scenario: Source forbids automated collection
- **WHEN** a candidate source's terms or robots directives disallow automated collection
- **THEN** the system skips it, records the skip reason, and does not collect from it

#### Scenario: Private or auth-walled data is encountered
- **WHEN** reaching a fact would require private access, credentials, or bypassing an access control
- **THEN** the system does not collect it and records the boundary rather than the value

#### Scenario: OSINT artifact records its terms
- **WHEN** an OSINT Source Artifact is stored
- **THEN** it carries its origin, data classification, collection purpose, and source terms alongside its retrieval metadata

#### Scenario: Intrusive collection technique is out of scope
- **WHEN** a proposed collection method relies on account-existence enumeration, deanonymizing a pseudonymous account, ingesting breached or leaked datasets, or extracting data from a device
- **THEN** the system does not use it, because such methods are prohibited regardless of whether their inputs appear public

### Requirement: Personal-data processing has a lawful basis and purpose limitation
The system SHALL process personal data of Founders only for the stated purpose of investor sourcing and evaluation, SHALL record a lawful basis for that processing, and MUST NOT treat public availability alone as sufficient justification. Discovery SHALL NOT imply consent; outreach SHALL invite a Founder to submit an Application rather than acting on a silently built profile, and a Founder SHALL be able to request correction or removal of their record through an auditable action.

#### Scenario: Public data still requires a purpose
- **WHEN** the system collects public personal data about a Founder
- **THEN** it records the sourcing-and-evaluation purpose and lawful basis and does not repurpose that data for an unrelated use

#### Scenario: Removal is requested
- **WHEN** a Founder requests correction or removal of their personal record
- **THEN** the system records an auditable action, removes or corrects the protected content, and makes dependent derived records unavailable rather than silently altering history

### Requirement: Sourcing-channel effectiveness and collection value are measured, not learned
The system SHALL record which source category or channel produced each candidate and SHALL be able to report channel effectiveness as telemetry, and SHALL apply a collection-value policy that prefers cheap high-signal sources and flags low-confidence or low-value data rather than over-collecting. These measures SHALL remain descriptive telemetry and MUST NOT become learned scoring weights, and channel statistics computed from small samples SHALL be marked provisional.

#### Scenario: Channel provenance is recorded
- **WHEN** a candidate is created from an outbound source
- **THEN** the system records which channel produced it so channel effectiveness can be reported later without altering the candidate's assessment

#### Scenario: Low-value collection is flagged rather than expanded
- **WHEN** an additional source would add cost but little new signal or only low-confidence data
- **THEN** the system flags it as low-value and leaves the field Unknown rather than over-collecting

#### Scenario: Channel statistics are provisional on small samples
- **WHEN** channel effectiveness is computed from few observations
- **THEN** the report marks the statistic provisional and does not feed it back as a scoring weight
