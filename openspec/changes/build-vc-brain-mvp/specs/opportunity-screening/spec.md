## ADDED Requirements

### Requirement: Inbound and activated outbound opportunities share one full-screening contract
The system SHALL use the same versioned Screening Case lifecycle and Assessment Envelope schema for direct inbound Applications and for Outbound Candidates after they submit an Application. An Outbound Candidate whose signals cross a versioned conviction threshold, or whom an investor explicitly requests to analyze, SHALL receive a preliminary thesis-aware Assessment Envelope keyed to the candidate. That envelope SHALL use the common criterion, Founder Score, axis, Claim, Evidence, and coverage structures with Unknown values where application evidence or a resolvable Founder identity is absent, and MUST NOT be presented as completed diligence.

#### Scenario: Direct inbound application enters screening
- **WHEN** a valid inbound Application is accepted
- **THEN** the system creates a Screening Case that uses the common deterministic screening, intelligence, diligence, and recommendation stages

#### Scenario: Activated outbound candidate enters screening
- **WHEN** an Outbound Candidate submits a valid Application
- **THEN** the system creates the same kind of Screening Case while retaining outbound provenance and preliminary history

#### Scenario: Sparse outbound assessment is viewed
- **WHEN** an investor views an Outbound Candidate that has not applied
- **THEN** the system labels the Assessment Envelope as preliminary, exposes its evidence coverage, and omits any unsupported full-diligence conclusion

#### Scenario: Public signals cross the conviction threshold
- **WHEN** source-backed signals meet the active versioned threshold for preliminary intelligence
- **THEN** the system creates or refreshes a candidate-keyed preliminary Assessment Envelope and queues the candidate for human activation review without sending outreach automatically

#### Scenario: Preliminary candidate lacks a resolved founder
- **WHEN** a preliminary Outbound Candidate represents only a Company or team and no individual Founder identity can be resolved safely
- **THEN** the envelope marks Founder Score Unknown with reason `founder_identity_unresolved` rather than fabricating or attaching another person's score

### Requirement: Investor thesis is configurable and versioned
The system SHALL let an investor configure sectors, stage, geography, check size, ownership target, and risk appetite. Each criterion SHALL be explicitly configured as a hard constraint, a scored preference, or No Preference, with a declared policy for Unknown values. Every screening and ranking output SHALL reference the exact thesis version used.

#### Scenario: Geography is only a preference
- **WHEN** the active thesis sets geography to a scored preference
- **THEN** a non-matching known geography affects priority according to the visible rule but does not make the Opportunity ineligible

#### Scenario: Geography has no effect
- **WHEN** the active thesis sets geography to No Preference
- **THEN** geography produces a Not Evaluated criterion result and contributes neither a reward nor a penalty

#### Scenario: Hard criterion is unknown
- **WHEN** an applicable value is Unknown under a hard constraint
- **THEN** the criterion result is Indeterminate rather than silently Pass or Fail, and the configured policy determines whether the next action is Needs Information or Manual Review

#### Scenario: Thesis changes after assessment
- **WHEN** an investor edits the thesis after an assessment completed
- **THEN** the existing assessment retains its original thesis version and any reassessment references the new version

### Requirement: Deterministic screening is explainable
The system SHALL run versioned deterministic viability and thesis rules before expensive model analysis when the required inputs are available. Each rule result SHALL be Pass, Fail, Indeterminate, or Not Evaluated and SHALL expose its inputs, knowledge states, rule version, and concise reason. A human investor SHALL be able to override a rule outcome with a recorded rationale.

#### Scenario: Clear hard-constraint failure short-circuits analysis
- **WHEN** reliable Known data fails a configured hard constraint
- **THEN** the system records the exact failing rule and may skip expensive model work without hiding the Opportunity or its evidence

#### Scenario: Missing history reaches review
- **WHEN** a rule input is Unknown because a Founder has no public track record
- **THEN** the rule yields Indeterminate rather than Fail and routes the gap to information collection or human review

#### Scenario: Human overrides a rule
- **WHEN** an investor overrides a deterministic outcome
- **THEN** the system preserves both the original result and the investor's identity, time, and rationale

### Requirement: Compound natural-language queries produce inspectable plans
The system SHALL accept a multi-attribute natural-language discovery request in one user interaction and translate it into a typed, editable Opportunity Query Plan containing recognized criteria, operators, bounded retrieval queries, source categories, Unknown-value policies, and unresolved phrases. The translation MAY use a model- or agent-assisted planning pass, but deterministic filtering and thesis rules SHALL execute the validated plan against normalized data. Optional semantic retrieval or reranking MUST remain labeled, MUST NOT override a Known hard-constraint failure, and MUST NOT turn missing evidence into a negative fact. The system MUST NOT execute generated arbitrary database or shell code.

#### Scenario: Investor submits a compound query
- **WHEN** an investor asks for a technical founder in Berlin building AI infrastructure with enterprise traction, no prior VC backing, and a top-tier accelerator history
- **THEN** one submission produces an inspectable Opportunity Query Plan and results whose match, mismatch, and Unknown outcomes are individually inspectable without requiring five manual filters

#### Scenario: Query term is ambiguous
- **WHEN** a phrase cannot be mapped confidently to the supported filter vocabulary
- **THEN** the system marks that phrase unresolved and asks for confirmation or leaves it out rather than silently inventing a criterion

#### Scenario: Outbound facts are not yet in Memory
- **WHEN** a validated Opportunity Query Plan requires public signals that have not been collected
- **THEN** the planning pass may emit bounded provider-neutral retrieval queries, after which acquired Evidence is normalized and the same deterministic criteria are evaluated

#### Scenario: Search silence cannot prove a negative
- **WHEN** the query requests no prior VC backing but collection finds no reliable funding record
- **THEN** prior backing remains Unknown rather than being treated as a Known match for no prior backing

#### Scenario: Semantic reranking disagrees with a hard rule
- **WHEN** semantic relevance ranks an Opportunity highly but Known data fails a hard thesis constraint
- **THEN** the hard-rule result remains Fail and the semantic rank cannot make the Opportunity eligible

### Requirement: Founder Score is persistent, evidence backed, and uncertainty aware
The system SHALL produce a 0–100 heuristic Founder Score snapshot for each screened Founder, with a score version, as-of time, evidence coverage, factor contributions, qualitative uncertainty band, and provisional status where evidence is sparse. A numerical prediction or confidence interval MUST NOT be shown unless it has been calibrated and its method is disclosed. Missing public history SHALL reduce coverage and widen uncertainty rather than contribute a zero or negative factor. Founder Score SHALL persist across Opportunities and SHALL be an input to, but not a replacement for, the Founder Axis.

#### Scenario: Experienced founder reaches a new opportunity
- **WHEN** a known Founder begins a new Screening Case
- **THEN** the case references the latest eligible Founder Score snapshot and retains the historical series used to show trend

#### Scenario: Cold-start founder is scored
- **WHEN** a first-time Founder has only limited application evidence
- **THEN** the system produces a clearly provisional score based only on available evidence, displays low coverage and high qualitative uncertainty, and does not penalize absent networks or public profiles

#### Scenario: New milestone changes the score
- **WHEN** verified new evidence changes one or more Founder Score factors
- **THEN** the system creates a new snapshot and never overwrites the prior score or its factor explanation

### Requirement: Cold-start founders receive an affirmative evidence path
The system SHALL evaluate positive founder-supplied and work-product Evidence such as a deck, prototype, technical artifact, hackathon output, research contribution, or structured interview without requiring those items in the initial Application. Sparse public history SHALL place a Founder in an explicit exploration or low-coverage lane rather than silently burying the record, and the system SHALL propose the smallest next Evidence request that could resolve a material uncertainty.

#### Scenario: Work product supports a cold-start founder
- **WHEN** a Founder has no funding, GitHub, or network history but provides strong, relevant, source-backed work-product Evidence
- **THEN** the Founder may receive an Activate or Advance Recommendation based on that Evidence while public-footprint coverage remains low

#### Scenario: One follow-up could resolve the key gap
- **WHEN** a cold-start assessment has a single material uncertainty that blocks readiness
- **THEN** the system proposes a focused follow-up such as one artifact, interview question, or verification step rather than requesting an exhaustive profile

#### Scenario: Sparse profiles are ranked
- **WHEN** high-coverage and low-coverage founders share a queue
- **THEN** the system keeps an inspectable exploration bucket or coverage-aware view so source richness alone cannot silently determine priority

### Requirement: Three opportunity axes remain independent
Every full Assessment Envelope SHALL contain independent Founder, Market, and Idea-versus-Market Axis Assessments. Market SHALL use Bullish, Neutral, Bear, or Unknown; the Founder and Idea-versus-Market axes SHALL use their versioned documented categorical rubrics. Each axis SHALL contain Improving, Stable, Declining, or Unknown trend, evidence coverage, confidence, supporting and counter-evidence Claim references, and open questions. The system MUST NOT average the axes into one investment score.

#### Scenario: Axes disagree
- **WHEN** the Founder Axis is strong while the Market Axis is Bear and the Idea-versus-Market Axis is Pivotable
- **THEN** all three conclusions remain visible and the recommendation explains how the disagreement affected the next action

#### Scenario: Trend lacks history
- **WHEN** an axis has fewer than the required dated observations for a trend
- **THEN** its trend is Unknown rather than Stable

#### Scenario: Opportunity is ranked
- **WHEN** the system assigns a queue priority or recommendation tier
- **THEN** it uses a transparent, versioned decision matrix whose rule contributions are visible without replacing the three axes

### Requirement: Intelligence modules return one structured schema
The intelligence pipeline SHALL separately evaluate market conditions, idea novelty and quality, founder and team evidence, idea-versus-market viability, and validation or adversarial checks, then synthesize them through the common Assessment Envelope. These are logical analysis responsibilities and MUST remain testable independently of any agent-orchestration framework or model provider.

The selected live implementation MAY use OpenAI `gpt-5.6-luna` strict Structured Outputs behind the same framework-neutral specialist ports as deterministic fakes; it does not require or share the outbound LangGraph retrieval state. Founder-private inputs MAY be used for this hackathon MVP only when both the OpenAI private-data enablement and separate hackathon-risk acknowledgement are explicit. Model output is only a proposal. Acceptance still requires the same schema, citation, Claim/Evidence, bias, contradiction, Unknown, Recommendation-versus-Decision, and run-version validations used by deterministic adapters. Provider refusal, malformed output, unsupported citations, or timeout SHALL fail closed without erasing already accepted stage outputs.

#### Scenario: Specialist output is accepted
- **WHEN** a logical analysis module completes
- **THEN** its output is schema-valid, identifies input Evidence and Claims, records confidence and gaps, and can be consumed without parsing free-form prose

#### Scenario: Specialist output is invalid
- **WHEN** a model response is malformed, unsupported by cited Evidence, or violates an output constraint
- **THEN** the pipeline rejects that output, records the validation failure, and routes the affected conclusion to retry or Manual Review

#### Scenario: Founder presentation is assessed
- **WHEN** the founder/team analysis evaluates how a Founder presents the opportunity
- **THEN** it limits the assessment to claim clarity, consistency, responsiveness, and evidence quality and excludes appearance, accent, name, charisma, production polish, protected traits, and socioeconomic proxies

### Requirement: Canonical Memory and orchestration state remain separate
Intelligence modules SHALL read an immutable input snapshot from canonical Memory and SHALL propose structured outputs for validation before persistence. Temporary model messages, checkpoints, or scratch state MUST NOT become authoritative founder facts or silently mutate canonical records.

#### Scenario: Agent proposes a new claim
- **WHEN** an intelligence module infers an investor-relevant Claim
- **THEN** the Claim is validated against its cited Evidence and stored as a derived, versioned record rather than overwriting source Observations

#### Scenario: Run is retried
- **WHEN** an interrupted intelligence run resumes or restarts
- **THEN** temporary orchestration state may be restored while the canonical input snapshot and already accepted domain records remain identifiable and unchanged

### Requirement: Trust Score is claim level
Every material Claim SHALL have its own 0–100 Trust Score or an explicit Unscored state. The score SHALL be derived from source provenance, source independence, recency, extraction certainty, corroboration, and contradictions, and SHALL expose those contributing factors. The system MUST NOT substitute model self-confidence or a single company-wide Trust Score.

#### Scenario: Claim has independent corroboration
- **WHEN** two independent, reliable Source Artifacts support the same current traction Claim
- **THEN** the Claim's Trust Score records the corroboration and references both pieces of Evidence

#### Scenario: Claim is only founder asserted
- **WHEN** a material Claim appears only in a founder-supplied deck and has not been externally verified
- **THEN** the system labels it asserted but unverified, assigns trust accordingly, and does not present it as an established fact

#### Scenario: Claim is unsupported
- **WHEN** a generated conclusion has no valid Evidence
- **THEN** the Claim remains Unscored or Unsupported and cannot be used as a factual memo assertion

### Requirement: Contradictions and diligence gaps are first-class outputs
The system SHALL cross-reference material Claims, preserve contradictory values, and expose unresolved contradictions, stale sources, missing required information, and proposed next diligence actions before a case becomes decision ready.

#### Scenario: Seeded revenue contradiction is detected
- **WHEN** a deck and an external source report incompatible current revenue values
- **THEN** the Assessment Envelope flags the contradiction, cites both sources, lowers or withholds trust for the unresolved Claim, and creates a diligence action

#### Scenario: Missing cap table is optional for the MVP memo
- **WHEN** the cap table is Not Disclosed
- **THEN** the memo marks the gap explicitly without fabricating data and the case may continue under a visible diligence policy

### Requirement: Investment memo is concise, structured, and cited
The system SHALL generate a versioned investment memo containing Company Snapshot, Investment Hypotheses, SWOT, Problem and Product, and Traction and KPIs. Optional sections SHALL be included only when useful and SHALL explicitly state Unknown, Not Disclosed, Not Applicable, or Conflicted information. Every material factual assertion SHALL link to a Claim and its Evidence.

#### Scenario: Required memo is generated
- **WHEN** a full assessment completes with sufficient evidence for review
- **THEN** the memo contains all five required sections, an explicit recommendation and next actions, and claim-level citations without padding

#### Scenario: Optional data is unavailable
- **WHEN** financial projections, customer references, or cap table data are unavailable
- **THEN** the relevant included section states the precise knowledge state rather than guessing or silently omitting the gap

### Requirement: Decision readiness follows a versioned policy
The system SHALL evaluate each full Screening Case against a versioned Decision Readiness policy and SHALL expose a readiness status of Not Evaluated, Blocked, Ready, or Ready With Accepted Risk separately from the Recommendation. At minimum, readiness requires the deck to be parsed or its parse failure explicitly reviewed, all thesis criteria to have outcomes, all three axes to be present with Unknown permitted only where policy allows, a provisional or established Founder Score, all five required memo sections, every material Claim to cite Evidence or be marked Unsupported, enumerated contradictions and diligence gaps, and one clear Recommendation with next action. Critical Unknown or Conflicted items SHALL set readiness to Blocked and lead to a Needs Information or Manual Review Recommendation rather than a false Ready state unless a human explicitly accepts the documented risk.

#### Scenario: Case satisfies readiness policy
- **WHEN** every required readiness item is present and no unresolved policy blocker remains
- **THEN** the system marks the Screening Case Decision Ready and records the policy version and evaluation details

#### Scenario: Critical contradiction remains
- **WHEN** a material contradiction is classified as blocking by the active readiness policy
- **THEN** readiness is Blocked, the Recommendation is Needs Information or Manual Review, and the system identifies the smallest resolving diligence action

#### Scenario: Human accepts a known gap
- **WHEN** an authorized investor accepts a policy blocker with a rationale
- **THEN** readiness becomes Ready With Accepted Risk and the record preserves the blocker, acceptance, actor, and time instead of erasing the gap

#### Scenario: Minimum application does not identify a founder
- **WHEN** company name plus deck does not establish any individual Founder identity safely
- **THEN** the system fabricates no placeholder Founder, records Founder Score and Founder Axis as Unknown for reason `founder_identity_unresolved`, keeps readiness Blocked, and issues a focused founder-identification request

### Requirement: Recommendation remains distinct from human Decision
The system SHALL produce an evidence-backed Recommendation such as Advance, Needs Information, Manual Review, or Do Not Pursue, with reasons and next actions. A human investor SHALL make and record the final investment Decision; the MVP MUST NOT transfer capital automatically.

#### Scenario: System recommends advancing
- **WHEN** an Assessment Envelope supports an Advance recommendation
- **THEN** the case remains awaiting a human Decision and exposes the axes, Founder Score, material Claims, Trust Scores, contradictions, and gaps used

#### Scenario: Human disagrees with recommendation
- **WHEN** an investor records a different Decision
- **THEN** the system preserves both the Recommendation and the human Decision with the decision rationale and timestamp

### Requirement: Screening runs are reproducible and auditable
Each screening run SHALL record schema, thesis, deterministic-rule, score, model, prompt, tool, and policy versions; the canonical input snapshot time; stage statuses; retries and failures; elapsed time; and accepted structured outputs. Audit traces SHALL contain sources, tool actions, rule results, validation summaries, and concise rationales but MUST NOT expose private model chain-of-thought.

#### Scenario: Assessment is reproduced for review
- **WHEN** an investor audits a completed assessment
- **THEN** the system identifies the exact input snapshot and versions that produced every accepted rule and structured conclusion

#### Scenario: Model provider changes
- **WHEN** a case is reassessed with a different model or prompt version
- **THEN** the new Assessment Envelope remains distinct and comparable to the previous version

### Requirement: Decision-readiness time is measurable
The system SHALL measure time from inbound Application acceptance or outbound first signal through activation, screening, diligence, and decision readiness, and SHALL expose stage durations, failures, and whether the 24-hour target was met.

#### Scenario: Case becomes decision ready
- **WHEN** a Screening Case reaches decision-ready status
- **THEN** the system records total elapsed time, per-stage durations, and the result against the 24-hour target

#### Scenario: Case waits for founder information
- **WHEN** a required diligence answer is pending from a Founder
- **THEN** the timeline distinguishes active system processing from external waiting time without hiding either duration

### Requirement: Soft-trait assessments carry a calibrated confidence band
Each subjective sub-assessment that contributes to an Axis Assessment or the Founder Score, such as resilience, founder-market fit, or execution, SHALL carry an explicit confidence expression derived from repeated sampling of the reasoned estimate rather than from model self-report alone. The primary method SHALL sample the reasoned sub-score multiple times and derive a confidence band from the dispersion of those samples, and SHALL remain framework- and provider-neutral so it does not depend on token log-probabilities. A large divergence between an initial snap estimate and the final reasoned estimate SHALL lower confidence. A numeric interval SHALL be shown only after the method is documented and calibrated against fixtures.

#### Scenario: A stable estimate yields a tight band
- **WHEN** repeated reasoned samples of a sub-score agree closely
- **THEN** the assessment reports high confidence and a narrow band

#### Scenario: A volatile estimate yields a wide band
- **WHEN** repeated reasoned samples of a sub-score disagree substantially
- **THEN** the assessment reports low confidence and a wide band rather than one confident number

#### Scenario: Snap and reasoned estimates diverge
- **WHEN** an initial pre-reasoning estimate and the final reasoned estimate differ beyond a versioned threshold
- **THEN** the recorded confidence is lowered and the divergence is retained as a factor

#### Scenario: Cold-start evidence is sparse
- **WHEN** a sub-assessment rests on little Evidence
- **THEN** it is provisional, its band is wide, and low coverage is shown separately from the estimate

### Requirement: Founder scoring uses an evidence-graded trait taxonomy
The Founder Axis and Founder Score factors SHALL be organized as a versioned trait taxonomy in which each trait is graded by the strength of evidence that it predicts building success and by how costly its supporting signal is to fabricate. Costly-to-fake and peer-validated signals such as shipped and externally adopted work, corroborated domain experience, and sustained follow-through SHALL be weighted above easily gamed vanity signals such as follower counts, repository stars, or self-authored endorsements. Attributes that are not evidence-backed predictors of building success, including presentation charisma, institutional pedigree, youth, and raw team size, MUST NOT contribute positively to founder quality and MAY be recorded only as neutral context or a bias flag.

#### Scenario: Costly-to-fake signal outranks a vanity metric
- **WHEN** a Founder has strong externally adopted work but a low follower count
- **THEN** the founder factors weight the adopted work above the follower count and explain why

#### Scenario: Folklore attribute is excluded from quality
- **WHEN** institutional pedigree or a polished presentation is present
- **THEN** it does not raise founder quality and is recorded only as neutral context rather than a positive factor

#### Scenario: Cold-start founder is graded on work product
- **WHEN** a first-time Founder has only work-product Evidence and public writing
- **THEN** the taxonomy scores those costly-to-fake artifacts and does not require gameable public-footprint signals

### Requirement: The system distinguishes builder signal from fundability
For each Founder the system SHALL express a builder-signal read derived from costly-to-fake and outcome-linked Evidence separately from a fundability read that reflects the signals the conventional funding market rewards such as network, pedigree, and prior funding. The system SHALL make the gap between the two reads visible and SHALL be able to surface Founders with strong builder signal and low fundability as high priority so that merit the network-gated market would overlook is not buried. Neither read SHALL be presented as the human Decision.

#### Scenario: An under-networked strong builder is surfaced
- **WHEN** a Founder shows strong builder-signal Evidence but low conventional fundability
- **THEN** the system flags the Founder as an underrated opportunity and explains the gap with its Evidence

#### Scenario: A well-networked weak builder is not inflated
- **WHEN** a Founder has high fundability signals but weak builder Evidence
- **THEN** the builder-signal read remains low and the gap is shown rather than averaged away

### Requirement: Scoring is outcome-anchored and bias-audited
Calibration of Founder Score, trait, and confidence rubrics SHALL be anchored on observed building outcomes and MUST NOT use historical funding decisions as the target, because funding history encodes network and demographic bias. The system SHALL support a standing counterfactual check that re-runs an assessment with identity-correlated attributes such as name, inferred gender, or school swapped and SHALL flag a material change in score as a bias defect, and SHALL be able to report score calibration separately for identifiable subgroups. Below a versioned Evidence-coverage threshold the system SHALL abstain with a wide band or route to human review rather than emit a confident low score.

#### Scenario: A swap changes the score
- **WHEN** an assessment is re-run with only identity-correlated attributes swapped and the score changes beyond a versioned tolerance
- **THEN** the system flags a bias defect for review rather than shipping the score

#### Scenario: Subgroup calibration is reported
- **WHEN** calibration is evaluated
- **THEN** the system can report rank agreement and interval coverage separately for identifiable subgroups so worse calibration for any subgroup is visible

#### Scenario: Funding history is not the label
- **WHEN** a scoring rubric is calibrated
- **THEN** its target is an observed building outcome and not whether comparable founders were previously funded

### Requirement: Founder-scoring predictive validity is measurable
The MVP SHALL provide a versioned evaluation that tests whether Founder scoring carries real signal, using a hold-out set of Founders with known later outcomes scored blind to those outcomes. The evaluation SHALL report rank agreement such as a Spearman correlation between predicted founder rank and realized outcome, the calibration and coverage of the confidence bands, and a comparison against a naive capital-raised baseline, and SHALL report these separately for identifiable subgroups where sample size permits.

#### Scenario: Scoring is compared to outcomes
- **WHEN** blind scores are compared to held-out realized outcomes
- **THEN** the evaluation reports rank agreement and whether the model beats a capital-raised baseline

#### Scenario: Confidence bands are checked
- **WHEN** calibration is evaluated
- **THEN** the evaluation reports whether stated confidence bands contain the realized outcome at approximately their claimed rate
