## ADDED Requirements

### Requirement: Founder intake is minimal and transparent
The experience SHALL allow a founder to submit an Application with only a company name and one pitch deck. Founder intake and status SHALL use a focused public shell that is separate from the investor workspace and MUST NOT expose an investor sidebar, thesis configuration, candidate queue, Opportunity, memo, Decision, or analyst-only terminology. Additional fields SHALL be visibly optional, and the page SHALL explain supported file constraints, processing purpose, submission state, and the 24-hour decision-readiness goal without requiring extra profile data.

#### Scenario: Founder submits the minimum application
- **WHEN** a founder provides a company name and supported deck
- **THEN** the experience accepts the Application, shows a submission reference, received time, and current status, and does not require optional profile fields

#### Scenario: Optional information is absent
- **WHEN** the founder submits no optional information
- **THEN** the Application proceeds and absent values are represented as Unknown rather than negative evidence; Not Disclosed is used only when intentional withholding is established

#### Scenario: Intake validation fails
- **WHEN** a required value is missing or the deck violates a disclosed constraint
- **THEN** the experience preserves valid input, shows an error summary and field-level guidance, associates errors programmatically, and moves focus to the error summary or first invalid control

#### Scenario: Founder cannot use drag and drop
- **WHEN** a keyboard or assistive-technology user selects a deck
- **THEN** a labeled native file control provides all functionality available through drag and drop

#### Scenario: Founder opens the Application route
- **WHEN** a founder follows a public Application or status link
- **THEN** the page presents only the founder task, brand, privacy and processing context, and status or follow-up information, and no investor navigation item identifies Founder Apply as part of the investor workspace

### Requirement: Founder can securely learn the outcome
The experience SHALL provide an unguessable founder-status link or equivalent capability after submission so company name plus deck remain the only required intake fields. The founder-facing view SHALL expose receipt time, current stage, focused follow-up requests, last update, the 24-hour target, and an approved final outcome or next action without exposing investor-only analysis.

#### Scenario: Founder saves the status link
- **WHEN** an Application is accepted
- **THEN** the confirmation clearly presents the founder-status capability, explains that it is private, and lets the founder return without creating an account

#### Scenario: Focused follow-up is requested
- **WHEN** screening identifies the smallest additional artifact or interview answer needed for a material gap
- **THEN** the status view presents that focused request and does not turn it into a mandatory initial profile form

#### Scenario: Founder checks the result
- **WHEN** an approved final outcome or next action is available
- **THEN** the status view presents it with its decision time and does not expose the investment memo, private diligence, or other candidates

### Requirement: Investor thesis exposes criterion intent
The workspace SHALL let an investor configure sector, stage, geography, check size, ownership target, and risk appetite, and SHALL visibly distinguish hard constraints, scored preferences, and No Preference together with each criterion's Unknown-value policy.

#### Scenario: Geography is irrelevant to the fund
- **WHEN** an investor configures geography as No Preference
- **THEN** geography is visibly excluded from eligibility and ranking and Unknown geography does not count against an Opportunity

#### Scenario: Known value fails a hard constraint
- **WHEN** a Known Opportunity value violates a hard constraint
- **THEN** the failed criterion and observed value are shown with the ineligibility result

#### Scenario: Hard-constraint value is unknown
- **WHEN** a value required for a hard constraint is Unknown
- **THEN** the criterion is visibly unresolved rather than represented as Pass or Fail and can be filtered as needing information

### Requirement: Sourcing workspace preserves origin and activation state
The workspace SHALL present inbound Applications and outbound discoveries in one navigable queue while preserving origin, triggering signal, workflow stage, freshness, source coverage, and activation status. Activating a candidate SHALL be described as pursuing outreach or an Application, never as making an investment.

#### Scenario: Investor reviews mixed sourcing results
- **WHEN** inbound and outbound records are present
- **THEN** each row or card identifies origin and current stage, and an outbound record identifies the signal that caused discovery

#### Scenario: Investor activates an outbound candidate
- **WHEN** an investor selects the activation action
- **THEN** the experience requires explicit human confirmation, records intent to pursue an Application, and may show an editable evidence-backed outreach draft without implying that outreach or investment occurred automatically

#### Scenario: Investor inspects ways to follow up
- **WHEN** accepted outbound Evidence contains a source-published website, contact page, public email, or public profile
- **THEN** the candidate detail presents those routes in a progressively disclosed contact panel with their source locator and unverified-identity state, and never invents a route when the value is Unknown

#### Scenario: Investor inspects an agentic sourcing run
- **WHEN** an outbound candidate was produced by the bounded retrieval graph
- **THEN** the audit layer exposes its query rounds, Evidence gaps, budgets, partial failures, and convergence stop reason without exposing private chain-of-thought

#### Scenario: Investor completes outreach
- **WHEN** an investor copies, sends through an approved human-controlled channel, or records completion of outreach
- **THEN** the experience records actor, time, channel, and contact status while keeping the Outbound Candidate distinct from an Application

#### Scenario: No records match
- **WHEN** the active thesis and query yield no results
- **THEN** the empty state identifies active constraints and offers a way to inspect, relax, or clear them

### Requirement: Investor workflows use task-first progressive disclosure
Each investor page SHALL make its current primary task, material blockers, and concise decision summary understandable without exposing the complete domain model at once. Secondary settings and analytical detail SHALL remain directly reachable through labeled collapsible regions, drawers, dialogs, or detail routes. Progressive disclosure MUST NOT hide information required for consent, a material contradiction, the three independent axes, the Recommendation-versus-Decision distinction, or the consequence of the primary action.

#### Scenario: Investor opens sourcing
- **WHEN** the sourcing workspace first renders
- **THEN** the compound request and scannable candidate results form the primary layer, while thesis editing, source budgets, deterministic filters, and complete query interpretation are closed secondary controls whose active state remains summarized

#### Scenario: Investor opens an Opportunity
- **WHEN** an Opportunity detail first renders
- **THEN** identity, readiness, Recommendation, material blockers, and a compact non-averaged three-axis summary appear first, while complete Claims, Evidence, Trust factors, provenance, and run history are available on demand

#### Scenario: Investor opens a memo
- **WHEN** the memo and Decision experience first renders
- **THEN** Recommendation, evidence gaps, contradictions relevant to acting, and the explicit human Decision command appear first, while memo sections, adversarial detail, citations, and audit history are progressively disclosed

#### Scenario: Secondary detail is hidden
- **WHEN** criteria, Evidence, or diagnostics are collapsed
- **THEN** their count, state, or material warning remains visible and the disclosure control has an accessible name and expanded state

### Requirement: Natural-language sourcing is inspectable
The workspace SHALL let an investor submit a compound natural-language sourcing request in one interaction and SHALL show the resulting Opportunity Query Plan, including interpreted criteria, planned source categories, unresolved phrases, and Unknown policy before or alongside results. The investor SHALL be able to correct, remove, or confirm an interpreted criterion without manually rebuilding the request as separate filters.

#### Scenario: Investor submits a compound query
- **WHEN** an investor asks for a technical founder in Berlin building AI infrastructure with enterprise traction and no prior VC backing
- **THEN** one submission displays the inspectable plan and distinguishes Known matches, Known non-matches, and unresolved values for each interpreted criterion

#### Scenario: Interpretation is ambiguous
- **WHEN** the system cannot confidently map part of a query to the supported vocabulary
- **THEN** that phrase is labeled unresolved and is not silently converted into a different filter

### Requirement: Dashboard preserves independent analytical signals
The investor dashboard SHALL expose queue order, workflow stage, axis trends, evidence coverage, contradictions, elapsed time, and the three independent opportunity axes through a scannable summary with details on demand. It MUST NOT hide disagreement among the axes behind one average or give every metric equal visual weight.

#### Scenario: Investor reviews screened opportunities
- **WHEN** screened Opportunities are available
- **THEN** each result shows origin, stage, thesis-fit summary, three axes, trend state, material gaps, contradiction indicator, and the reason for its queue position

#### Scenario: Axes disagree
- **WHEN** Founder is strong, Market is Bear, and Idea-versus-Market is Pivotable
- **THEN** the disagreement remains visible in list and detail views and no overall axis average replaces it

#### Scenario: Results are incomplete
- **WHEN** sourcing, enrichment, or screening is still running
- **THEN** partial results show last update, pending work, and incomplete status and are not styled as a final Recommendation

### Requirement: Score concepts are visually and semantically distinct
The Opportunity detail SHALL distinguish the persistent person-level Founder Score, the Founder, Market, and Idea-versus-Market Axis Assessments, and per-Claim Trust Scores in label, location, and explanation.

#### Scenario: Investor opens an opportunity
- **WHEN** the detail view loads
- **THEN** Founder Score is labeled as persistent across Opportunities, the three axes are labeled as current-Opportunity assessments, and Trust Scores appear beside individual Claims

#### Scenario: Founder has multiple opportunities
- **WHEN** a Founder has history across multiple Companies or Applications
- **THEN** the experience separates persistent Founder history from evidence and analysis specific to the current Opportunity

### Requirement: Evidence, uncertainty, and contradictions are one action away
Every material Claim in an assessment, memo, or Recommendation SHALL show its knowledge or verification state and provide a direct path to its Trust Score factors and exact Evidence locator. Unknown, Not Disclosed, Not Applicable, and Conflicted information SHALL remain visibly distinct.

#### Scenario: Investor inspects a supported claim
- **WHEN** an investor opens a Claim
- **THEN** the experience shows its Trust Score, verification state, source identity, observation time, and exact locator such as a deck slide or URL excerpt

#### Scenario: Claim lacks acceptable evidence
- **WHEN** a Claim has no acceptable Evidence
- **THEN** it is labeled Unsupported or Unknown and no fabricated citation or inferred certainty is displayed

#### Scenario: Sources conflict
- **WHEN** credible sources support incompatible values
- **THEN** the experience presents both values, sources, timestamps, and trust states before the investor acts and does not silently choose one

#### Scenario: Evidence overlay closes
- **WHEN** a keyboard user closes an evidence dialog, drawer, or preview
- **THEN** focus returns to the Claim citation that opened it

### Requirement: Trends are honest about history
Every Improving, Stable, or Declining trend SHALL expose its observation window and supporting dated values. Insufficient comparable history SHALL display Unknown or Insufficient History rather than Stable. Visual trends SHALL have an equivalent structured-text or table representation.

#### Scenario: Supported trend is displayed
- **WHEN** enough comparable observations establish a direction
- **THEN** the experience shows the direction, time window, observations, and source freshness

#### Scenario: History is insufficient
- **WHEN** there are not enough comparable dated observations
- **THEN** the experience displays Insufficient History and does not display Stable

#### Scenario: Chart cannot be perceived
- **WHEN** a chart is unavailable to a user or does not fit the current view
- **THEN** the same values, time period, and direction remain available as accessible structured content

### Requirement: Investment memo is evidence backed and gap preserving
The memo experience SHALL include Company Snapshot, Investment Hypotheses, SWOT, Problem and Product, and Traction and KPIs. It SHALL show generation and evidence-as-of times, provide Claim-level citations, retain required sections when information is missing, and offer an adversarial view of weaknesses and open diligence.

#### Scenario: Core memo is decision ready
- **WHEN** screening and diligence reach decision-ready status
- **THEN** all five required sections are present and material assertions link to Evidence and Trust details

#### Scenario: Required information is absent
- **WHEN** a required section lacks reliable information
- **THEN** the section remains present with the precise Unknown, Not Disclosed, Not Applicable, or Conflicted state rather than invented prose

#### Scenario: Investor opens the adversarial view
- **WHEN** an investor requests the adversarial perspective
- **THEN** weaknesses, fragile assumptions, counter-evidence, contradictions, and open questions link back to their Claims or missing Evidence

### Requirement: Human controls the Decision
The experience SHALL keep the system Recommendation distinct from the human Decision and SHALL require an explicit confirmation before recording Advance, Decline, Hold, or Request More Information. Decision labels SHALL remain distinct from deterministic rule results such as Pass or Fail. The experience MUST NOT claim that recording a Decision sends outreach or transfers funds.

#### Scenario: Investor records a decision
- **WHEN** an investor selects a disposition and supplies the required rationale
- **THEN** confirmation identifies the Opportunity, choice, unresolved contradictions, and material evidence gaps before the Decision is recorded

#### Scenario: Investor overrides the recommendation
- **WHEN** the human Decision differs from the Recommendation
- **THEN** both remain visible and the experience records the investor, time, and rationale

#### Scenario: Investor approves a recommendation
- **WHEN** an investor confirms an approval related to a $100K check
- **THEN** the success state says the Decision was recorded and does not state that funds moved

### Requirement: Workflow timing and failure recovery are visible
The experience SHALL expose progress from first signal or Application through decision readiness, including stage, start time, elapsed time, external waiting, stale data, partial failures, and retryable actions.

#### Scenario: Investor tracks the 24-hour target
- **WHEN** an Opportunity is in progress
- **THEN** list and detail views show current stage, elapsed time, and decision-readiness target, with approaching or missed targets expressed through text and not color alone

#### Scenario: Background step fails
- **WHEN** sourcing, enrichment, screening, or memo generation fails
- **THEN** the experience identifies the failed step and last successful state and offers an authorized retry without discarding completed evidence

#### Scenario: Background state changes
- **WHEN** an asynchronous operation changes state
- **THEN** the visible status updates and assistive technology receives a concise, non-disruptive announcement without focus being stolen

### Requirement: Experience is responsive and keyboard operable
Founder and investor flows SHALL retain content and functionality at 320 CSS pixels and 400% zoom except for intrinsically two-dimensional content. They SHALL use semantic landmarks, headings, labels, names, roles, values, and states; provide logical keyboard order, a skip mechanism, and no pointer-only interaction or keyboard trap.

#### Scenario: Workspace reflows
- **WHEN** the workspace is viewed at 320 CSS pixels
- **THEN** navigation, filters, summaries, Evidence, memo content, and Decision actions remain operable without page-level horizontal scrolling

#### Scenario: Two-dimensional content must scroll
- **WHEN** a comparison table intrinsically requires two dimensions
- **THEN** scrolling is confined to a labeled keyboard-accessible region and equivalent structured content remains available

#### Scenario: User navigates without a pointer
- **WHEN** a user operates any intake, filter, sourcing, detail, memo, Evidence, or Decision flow with a keyboard
- **THEN** every function is reachable in logical order, focus remains visible, and no action depends on hover, drag, or pointer precision

#### Scenario: Overlay opens
- **WHEN** a dialog or evidence preview opens
- **THEN** it has an accessible name, focus moves into it, permitted Escape dismissal works, and focus returns to the invoking control when it closes

### Requirement: Visual states meet WCAG 2.2 AA
The default experience SHALL meet WCAG 2.2 AA contrast and non-color requirements, including at least 4.5:1 contrast for normal text, 3:1 for large text and meaningful non-text boundaries or states, and visible focus indicators. Status MUST NOT be communicated by color, shadow, position, or motion alone.

#### Scenario: Investor identifies state without color
- **WHEN** an Opportunity is selected, contradicted, overdue, improving, declining, approved, or declined
- **THEN** visible text, iconography, shape, or another non-color cue and a programmatic state name communicate the meaning

#### Scenario: Control receives keyboard focus
- **WHEN** a control receives keyboard focus
- **THEN** an unobscured, contrast-compliant focus indicator appears and is not represented only by a neumorphic shadow

#### Scenario: Forced-colors mode is active
- **WHEN** the operating system enables forced colors
- **THEN** controls, boundaries, focus, selection, charts, Evidence states, and contradiction indicators remain visible using appropriate system colors

#### Scenario: Reduced motion is requested
- **WHEN** the user requests reduced motion
- **THEN** non-essential transitions and animated status or trend effects are removed or reduced without losing information

### Requirement: Chinese-art-inspired neumorphism is progressive styling
The visual theme SHALL use low-saturation rice-paper, celadon, jade, or ink-like environmental-gray surface tokens with palette-tinted highlights and shadows. Normal cards, inputs, buttons, panels, tags, and navigation items SHALL use zero-width borders; related tonal fills, rounded shape, spacing, and a consistent paired highlight/shadow or inset state SHALL create the soft tactile hierarchy. Saturated cinnabar, azure, or jade accents SHALL be reserved for a small number of primary actions and active states. Neumorphic depth MUST NOT be the only affordance or state indicator: labels, text/icon state, shape/fill changes, and explicit focus outlines remain required.

#### Scenario: Default theme renders
- **WHEN** the default visual theme is active
- **THEN** muted environmental colors and borderless paired or inset depth carry the surface hierarchy, one saturated accent guides the primary action, and every text, control, focus, and Evidence state still meets contrast requirements

#### Scenario: Decorative effects disappear
- **WHEN** shadows, gradients, or other decorative effects are unsupported, disabled, removed in print, or overridden by forced colors
- **THEN** labels, fills, icons, spacing, and structure still distinguish controls and pressed, selected, disabled, focused, and contradicted states; forced-colors mode may add system-color borders or outlines

#### Scenario: Dense analytical view is displayed
- **WHEN** the investor reviews a data-dense dashboard or memo
- **THEN** neumorphic elevation is limited to the current task and meaningful grouping, secondary detail is progressively disclosed, and the page does not compress evidence labels, make every item appear interactive, or render all analytical records at equal prominence

### Requirement: The underrated-founder gap and confidence are visible
The investor experience SHALL surface, where present, the builder-signal versus fundability reading and SHALL flag Founders with strong builder signal and low fundability as underrated opportunities, and SHALL show the confidence band on subjective assessments alongside their evidence coverage. These SHALL be presented as distinct, labeled signals and MUST NOT be collapsed into the three axes or into a single overall score.

#### Scenario: An underrated founder is highlighted
- **WHEN** a Founder has strong builder-signal Evidence but low conventional fundability
- **THEN** the experience labels the gap as an underrated-opportunity signal and links to the Evidence behind each read

#### Scenario: Confidence is shown with coverage
- **WHEN** a subjective assessment carries a confidence band
- **THEN** the experience shows the band and its evidence coverage so that a wide band from sparse evidence is not read as a low score

#### Scenario: The gap does not replace the axes
- **WHEN** the builder-versus-fundability reading is displayed
- **THEN** it appears as a distinct labeled signal and the three independent axes remain separately visible
