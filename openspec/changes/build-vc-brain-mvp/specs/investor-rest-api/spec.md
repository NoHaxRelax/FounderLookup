## ADDED Requirements

### Requirement: API is versioned and discoverable
The system SHALL expose the MVP HTTP contract under `/api/v1`, SHALL publish an OpenAPI description of the contract, and SHALL introduce a new major path rather than silently breaking an existing major version.

#### Scenario: Client discovers the contract
- **WHEN** a client retrieves the service OpenAPI document
- **THEN** it describes Application intake and founder status, theses, sourcing runs and candidate activation/outreach, query and screening commands, run status/retry, the nested Opportunity detail read model, and human Decisions with their request and response schemas

#### Scenario: Client requests an unsupported version
- **WHEN** a client calls a resource under an unsupported major-version path
- **THEN** the service returns a structured Not Found response and does not silently route the request to a different version

### Requirement: Domain resources have stable contracts
The API SHALL expose Applications, Outbound Candidates, Opportunities, Investment Theses, pipeline runs, and Decisions with opaque stable identifiers, UTC timestamps, documented lifecycle states, and identifier-based relationships. The nested Opportunity detail SHALL represent its Founder, Company, Screening Case, Claims, Evidence, Assessment Envelope, memo, and Recommendation as distinct typed components with stable domain identifiers. Generated components SHALL identify the input, policy, and thesis revisions from which they were produced. P0 SHALL NOT require standalone CRUD routes or generic relationship traversal for every nested domain type.

#### Scenario: Investor retrieves an opportunity
- **WHEN** an authorized investor retrieves an Opportunity
- **THEN** the response identifies its origin, linked Founder and Company, current workflow state, latest Assessment Envelope and memo references, current human Decision if any, and related run identifiers

#### Scenario: Client follows a relationship
- **WHEN** an Opportunity detail references its Founder, Company, assessment, memo, Claim, Evidence item, or run
- **THEN** the nested or linked representation uses the stable domain identifier rather than a web-search, model, or other provider identifier

#### Scenario: Screening exposes separate score concepts
- **WHEN** a Screening Case has an accepted Assessment Envelope
- **THEN** the response exposes the persistent per-person Founder Score separately from the Founder, Market, and Idea-versus-Market axes and does not synthesize the axes into an undocumented aggregate

### Requirement: API knowledge values are unambiguous
Every decision-relevant field that can be absent or disputed SHALL use a shared knowledge-value contract with Known, Unknown, Not Disclosed, Not Applicable, and Conflicted states. Non-Known states SHALL carry a reason, and Conflicted values SHALL include the alternatives and their Evidence references. The API MUST NOT encode missing knowledge as zero, false, an empty string, or an invented default.

#### Scenario: Geography has not been established
- **WHEN** no reliable source establishes an Opportunity's geography
- **THEN** the response marks geography as Unknown with a reason and does not infer a country from unrelated evidence

#### Scenario: Sources disagree on revenue
- **WHEN** credible sources report incompatible current revenue values
- **THEN** the API marks revenue as Conflicted and returns the alternatives with their Claim and Evidence references

#### Scenario: Cap table is withheld
- **WHEN** a founder has explicitly withheld cap-table information
- **THEN** the API returns Not Disclosed rather than Unknown, an empty table, or invented ownership figures

### Requirement: Minimum inbound intake is safe and idempotent
`POST /api/v1/applications` SHALL accept multipart form data with a non-blank company name and one supported pitch deck as the only required domain inputs. The request SHALL carry an idempotency key; replaying the same key with the same normalized fields and deck content SHALL return the original Application and ingestion-run identifiers without creating duplicates.

#### Scenario: Founder submits the minimum application
- **WHEN** a client submits a company name, a valid deck, and a new idempotency key
- **THEN** the service stores one Application, queues ingestion, returns `202 Accepted`, and provides the Application identifier, run identifier, investor run-status location, and an unguessable founder-status capability

#### Scenario: Client retries after losing the response
- **WHEN** the same payload and deck bytes are replayed with the same idempotency key
- **THEN** the service returns the original identifiers plus a still-valid or newly issued founder-status capability and creates neither a second Application nor a second ingestion run

#### Scenario: Key is reused for different content
- **WHEN** an existing idempotency key is replayed with a different normalized payload or deck content hash
- **THEN** the service returns `409 Conflict` and preserves the original Application

#### Scenario: Uploaded deck is invalid
- **WHEN** a deck exceeds the configured limit or its file signature does not match a supported media type
- **THEN** the service returns the applicable structured `413` or `415` error and stores neither an Application nor an accessible file

#### Scenario: Deck extraction later fails
- **WHEN** a structurally accepted deck cannot be parsed asynchronously
- **THEN** the original private Source Artifact remains available to authorized users, the run identifies the failed stage, and extracted fields remain Unknown rather than fabricated

### Requirement: Uploaded decks remain private
The API SHALL treat uploaded decks as untrusted private files, store them under server-generated identifiers rather than client paths, preserve a content hash and safe display name, and never publish them through an unauthenticated static URL.

#### Scenario: Authorized investor opens deck evidence
- **WHEN** an authorized investor follows an Evidence item into an uploaded deck
- **THEN** the service returns the permitted file or page representation through a protected endpoint without exposing a local path

#### Scenario: Anonymous client requests a deck
- **WHEN** a client without investor authorization requests a private deck or derived private content
- **THEN** the service returns a structured authorization error without revealing whether sensitive content exists

### Requirement: Thesis contract preserves intent and history
The API SHALL expose versioned Investment Thesis resources. Sector, stage, geography, check size, ownership target, and risk appetite SHALL each declare hard constraint, scored preference, or No Preference, a value or range where required, and an Unknown-value policy. Thesis updates SHALL create a new revision rather than rewriting the version used by an earlier run.

#### Scenario: Investor makes geography non-binding
- **WHEN** the investor updates geography to No Preference
- **THEN** the stored revision preserves that mode and does not require a geography value

#### Scenario: Investor submits an invalid criterion
- **WHEN** the investor supplies an unsupported mode, malformed range, or omits a value required by the chosen mode
- **THEN** the API returns field-level validation errors and leaves the active thesis revision unchanged

#### Scenario: Screening references a thesis
- **WHEN** a screening run is accepted
- **THEN** its resource records the exact thesis revision even if the active thesis later changes

### Requirement: Long-running work is asynchronous and observable
The API SHALL create sourcing, ingestion, and screening work as asynchronous pipeline runs. An accepted command SHALL return `202 Accepted` and a run resource exposing its kind, queued, running, succeeded, partially succeeded, or failed status, stage summaries, safe failure details, result links, and queued, started, and completed timestamps. An authorized retry command SHALL preserve accepted stage outputs and either resume from the last safe boundary or create a linked retry run against the same identifiable input snapshot.

#### Scenario: Investor starts a sourcing run
- **WHEN** an authorized investor submits a valid sourcing-run request
- **THEN** the service returns a run identifier and Location header without holding the connection open for discovery

#### Scenario: Investor polls a running job
- **WHEN** the investor retrieves a non-terminal run
- **THEN** the response includes current state, completed-stage summaries, and timing without exposing framework checkpoints or private model reasoning

#### Scenario: One source fails but usable results remain
- **WHEN** a run has usable results while one external source or stage fails
- **THEN** the run becomes Partially Succeeded, links usable results, and reports the retryable failure without discarding prior work

#### Scenario: Run fails terminally
- **WHEN** a run cannot produce its required result
- **THEN** the status resource remains retrievable with Failed state, completion timing, last successful stage, and a safe error summary

#### Scenario: Investor retries a failed stage
- **WHEN** an authorized investor retries a failed or partially succeeded run
- **THEN** the API returns the active or linked retry-run identifier, preserves prior accepted results, and records the retry attempt without duplicating successful work

### Requirement: Outbound candidate activation is explicit
The API SHALL expose Outbound Candidates separately from Applications and SHALL require an explicit authorized activation action to change sourcing workflow state. Activation SHALL record human intent to pursue outreach or invite an Application and MAY create a source-backed, human-editable outreach draft. Sending or copying the draft and recording contact status SHALL require an explicit human action. Activation SHALL NOT claim that outreach occurred or create an investment Decision.

#### Scenario: Investor activates a candidate
- **WHEN** an authorized investor activates an Outbound Candidate
- **THEN** the API appends an activation event, preserves discovery provenance, returns the candidate's new workflow state, and may return an editable outreach draft whose factual personalization cites approved Evidence

#### Scenario: Investor records outreach
- **WHEN** an authorized investor confirms that outreach was copied, sent through an approved channel, or otherwise completed
- **THEN** the API appends the contact method, human actor, time, and status without treating the contact as an Application or Decision

#### Scenario: Candidate later applies
- **WHEN** a minimum Application is linked to an activated candidate
- **THEN** the API creates or links the Opportunity and common Screening Case without duplicating the Founder or Company history

### Requirement: Founder status access is capability scoped
The API SHALL provide an unguessable, revocable founder-status capability when an Application is accepted, without making Application resources generally public. That capability SHALL expose only the receipt time, current founder-facing stage, focused information requests, and final outcome or next action needed to let the founder know within 24 hours; it MUST NOT expose the investor memo, private diligence, other candidates, or hidden reasoning.

#### Scenario: Founder checks a pending application
- **WHEN** a founder presents the valid status capability for an in-progress Application
- **THEN** the API returns the founder-facing stage, last update, 24-hour target state, and any focused information request without requiring an investor credential

#### Scenario: Founder checks a completed outcome
- **WHEN** a human Decision or approved next action is available
- **THEN** the founder-status response presents the approved outcome or action and its time without exposing investor-only analysis

#### Scenario: Invalid status capability is presented
- **WHEN** an absent, invalid, expired, or revoked capability is used
- **THEN** the service returns a generic structured authorization response that does not reveal whether an Application exists

### Requirement: Collections are bounded and filterable
The API SHALL expose bounded candidate and Opportunity collections with deterministic ordering, an identifier tie-breaker, and a documented maximum result count. It SHALL support documented filters for origin, workflow state, sector, stage, geography, thesis criterion result, recommendation, each independent axis and trend, contradiction state, evidence coverage, and field knowledge state.

#### Scenario: Result set exceeds the MVP bound
- **WHEN** more records match than the documented maximum result count
- **THEN** the response returns the deterministic leading results, reports that the result was truncated, and does not imply that the returned set is exhaustive

#### Scenario: Investor filters for unknown geography
- **WHEN** geography knowledge state is filtered to Unknown
- **THEN** the service returns explicitly unknown records instead of treating them as matching every known geography

#### Scenario: Investor combines deterministic filters
- **WHEN** an investor filters for inbound AI-infrastructure Opportunities at a specified stage with a Bullish Market Axis
- **THEN** every returned record satisfies the normalized conjunction and the response echoes that filter interpretation

### Requirement: Natural-language search exposes its interpretation
The API SHALL accept compound natural-language Opportunity queries and SHALL return the validated Opportunity Query Plan, planning mode and version, unresolved phrases, bounded provider-neutral retrieval requests if any, Unknown-value policy, and per-result match rationale. A model- or agent-assisted planner MAY propose the plan, but deterministic query and thesis execution SHALL consume the validated typed form. The implementation MUST NOT execute model-generated arbitrary SQL, shell commands, or unvalidated provider expressions.

#### Scenario: Investor submits a compound query
- **WHEN** the investor searches for a technical founder in Berlin building AI infrastructure with enterprise traction, no prior VC backing, and top-tier accelerator history
- **THEN** the response includes one typed Opportunity Query Plan, its parsed criteria and retrieval requests, unresolved terms, and Match, Mismatch, or Unknown outcome for each criterion on every result

#### Scenario: Query cannot be resolved safely
- **WHEN** no supported typed interpretation can be produced
- **THEN** the API returns a structured validation response and does not substitute a keyword-only result without disclosure

#### Scenario: Planner requests external discovery
- **WHEN** the validated plan includes bounded retrieval requests for data absent from canonical Memory
- **THEN** the API creates or links a sourcing run and never exposes a Tavily-, Exa-, or other provider-specific query contract to the client

### Requirement: Claims and Evidence are expandable and traceable
The nested Opportunity detail SHALL be compact by default and SHALL support explicit expansion of its Claims and Evidence components. Each Claim SHALL expose its Trust Score state, verification state, contradiction references, and Evidence identifiers. Each Evidence item SHALL identify its Source Artifact, collection time, exact locator, and current availability without exposing provider secrets.

#### Scenario: Investor expands a memo claim
- **WHEN** an investor requests a memo with Claims and Evidence expanded
- **THEN** each material Claim exposes its Trust Score factors and the exact source locators that support or contradict it

#### Scenario: Evidence comes from a deck
- **WHEN** a Claim cites an uploaded pitch deck
- **THEN** the Evidence includes the private deck identifier and page or slide locator without exposing a public file URL

#### Scenario: Original web source becomes unavailable
- **WHEN** a previously captured source can no longer be reached
- **THEN** historical Evidence retains captured provenance, reports source availability separately, and does not silently delete the dependent Claim

### Requirement: Memo revisions are immutable and gap aware
The nested Opportunity detail SHALL represent memos as immutable revisions linked to their Opportunity, Assessment Envelope, run, and thesis revision. Each memo SHALL preserve the five required sections and explicit knowledge states for missing material; later generation SHALL create a new memo identifier rather than overwrite history.

#### Scenario: Investor retrieves the latest memo
- **WHEN** an Opportunity has multiple completed memo revisions
- **THEN** the latest relation resolves to the newest revision while every earlier memo remains retrievable

#### Scenario: Required-section data is unavailable
- **WHEN** a required memo section lacks a reliable fact
- **THEN** the structured response retains the section and marks the gap Unknown, Not Disclosed, Not Applicable, or Conflicted as appropriate

### Requirement: Human Decisions append to audit history
The API SHALL let an authorized human investor record a Decision with a disposition, rationale, and references to the assessment and memo reviewed. Decisions SHALL be immutable, attributed events and P0 SHALL provide no in-place update operation; a model Recommendation MUST NOT impersonate or replace a human Decision. Recording a Decision MUST NOT send outreach or transfer funds.

#### Scenario: Investor records a decision
- **WHEN** an investor posts a valid Decision
- **THEN** the service returns `201 Created`, stores actor and time, links the reviewed revisions, and appends an audit event

#### Scenario: Screening reruns after a decision
- **WHEN** a new assessment changes the system Recommendation
- **THEN** the existing human Decision remains unchanged and both values remain separately retrievable

### Requirement: Errors are safe and machine readable
The API SHALL return `application/problem+json` problem details with type, title, HTTP status, stable application error code, and request identifier. Validation errors SHALL identify affected fields. Responses and logs MUST NOT expose stack traces, local paths, credentials, private file contents, raw provider secrets, or private model reasoning.

#### Scenario: Request validation fails
- **WHEN** a request contains an invalid enum, malformed identifier, or missing required input
- **THEN** the service returns the applicable 4xx status with stable field-level errors and a request identifier

#### Scenario: Unexpected server failure occurs
- **WHEN** an unhandled internal failure occurs
- **THEN** the client receives a generic problem response and request identifier while diagnostic details remain in protected telemetry

### Requirement: MVP access boundary is deliberately small
The MVP SHALL protect investor, thesis, candidate, opportunity, run, claim, evidence, memo, decision, audit, and private-file operations with one configured investor credential. Unauthenticated access SHALL be limited to minimum inbound submission and explicitly documented health or documentation routes; founder-status retrieval is the sole capability-authenticated exception and reveals only the bounded founder-facing projection defined above. Public intake and status access SHALL be rate limited, intake SHALL be size limited, CORS SHALL use an explicit allowlist, and credentials, status capabilities, and sensitive response data SHALL be redacted from logs.

#### Scenario: Authorized investor accesses a protected resource
- **WHEN** a request presents the configured investor credential
- **THEN** the service authorizes the request without requiring MVP user accounts, organizations, tenant selectors, or role administration

#### Scenario: Anonymous founder submits an application
- **WHEN** a rate-compliant anonymous client submits a valid minimum Application
- **THEN** the service accepts intake but grants no general access to investor resources or private Application data

#### Scenario: Client requests hidden reasoning
- **WHEN** a client attempts to expand provider prompts, framework state, private chain-of-thought, or credentials
- **THEN** the service rejects or ignores that unsupported expansion while continuing to expose intended citations, rule results, validation outcomes, and concise rationale
