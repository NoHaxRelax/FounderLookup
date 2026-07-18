# VC Brain

The VC Brain context covers discovery and evaluation of founders and opportunities from first signal through a human investment decision. Its language keeps persistent person-level history, opportunity-level analysis, and claim-level trust separate.

## People and opportunities

**Founder**:
A person whose persistent identity, evidence history, milestones, and Founder Score can span multiple companies and opportunities.
_Avoid_: Candidate, profile, applicant

**Company**:
An organization a founder is building or has built; the same company can accumulate multiple source records and applications over time.
_Avoid_: Deal, application, profile

**Outbound Candidate**:
A founder, team, or company discovered from public signals and considered for outreach but not yet represented by a founder-submitted application.
_Avoid_: Applicant, screened opportunity, investment

**Application**:
A founder-submitted request for consideration whose minimum contents are a company name and pitch deck.
_Avoid_: Opportunity, deal, candidate

**Opportunity**:
The particular founder-company proposition being considered for investment; it is the subject of an Application and its Screening Case.
_Avoid_: Company, Founder, profile

**Screening Case**:
The versioned workflow record through which an Application moves from first-pass screening to diligence, decision readiness, and a human Decision.
_Avoid_: Agent run, application, score

## Memory and evidence

**Memory**:
The durable, time-aware record of source artifacts, observations, claims, evidence, identities, and score history used by the VC Brain.
_Avoid_: Chat history, agent scratchpad, vector store

**Source Artifact**:
An immutable acquired input such as a pitch deck, web page snapshot, repository response, interview transcript, or source API record, stored with origin and retrieval metadata.
_Avoid_: Claim, evidence score, summary

**Observation**:
A normalized fact extracted from one Source Artifact without silently resolving conflicts with other observations.
_Avoid_: Conclusion, recommendation

**Claim**:
An investor-relevant assertion about a founder, company, market, or opportunity that can be supported, contradicted, or left unresolved by Evidence.
_Avoid_: Source, fact, model thought

**Evidence**:
A precise locator into a Source Artifact that supports or refutes a Claim.
_Avoid_: Citation text without a source, model rationale

**Contradiction**:
Two or more incompatible observations or claims that remain visible until explicitly resolved.
_Avoid_: Low confidence, missing data

## Evaluation

**Investment Thesis**:
A versioned investor lens containing sector, stage, geography, check-size, ownership, and risk criteria, each configured as a constraint, preference, or no preference.
_Avoid_: Query, hardcoded fund profile

**Founder Score**:
A persistent, evidence-backed, time-series estimate of one Founder that survives across Applications and contributes to—but never replaces—the Founder Axis.
_Avoid_: Opportunity score, axis average, Trust Score

**Axis Assessment**:
One of three independent opportunity-level conclusions—Founder, Market, or Idea versus Market—each with its own rating, trend, confidence, claims, and evidence.
_Avoid_: Composite score, Founder Score

**Trust Score**:
A confidence measure attached to one Claim and its Evidence, never a single company-wide credibility number.
_Avoid_: Founder Score, company score

**Assessment Envelope**:
The common structured output of preliminary or full evaluation, containing thesis results, coverage, scores, axis assessments, claims, evidence, contradictions, gaps, and a recommendation as applicable.
_Avoid_: Unstructured agent response, investment memo

**Recommendation**:
The system's evidence-backed proposal for the next investment-workflow action.
_Avoid_: Decision, automated money movement

**Decision**:
The human investor's recorded action and rationale on a Screening Case.
_Avoid_: Recommendation, model prediction

## Knowledge states

**Unknown**:
A value the system does not currently know; it is not false, zero, or evidence of weakness.
_Avoid_: Missing-as-failed, not applicable

**Not Disclosed**:
A value explicitly withheld, declined, or confirmed not supplied by the relevant party; bare omission remains Unknown.
_Avoid_: Unknown, false

**Not Applicable**:
A field that has no meaningful value for the subject or situation.
_Avoid_: Unknown, no preference

**Conflicted**:
A value for which credible Source Artifacts currently support incompatible observations.
_Avoid_: Unknown, low confidence

**No Preference**:
An Investment Thesis policy that deliberately gives a criterion no filtering or ranking effect; it says nothing about whether the underlying data is known.
_Avoid_: Not applicable, unknown

## Discovery language

**Opportunity Query Plan**:
A validated, typed translation of one natural-language sourcing request into criteria, bounded retrieval queries, Unknown-value policies, and unresolved terms that deterministic execution can inspect.
_Avoid_: Raw prompt, generated SQL, opaque agent search
