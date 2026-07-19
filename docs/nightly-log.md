# Nightly build log

Newest last. Each entry records: cycle, task, what shipped, tests, commit(s), notes.
`NEEDS ELIAS` marks a parked decision for morning review.

## 2026-07-19, kickoff
Plan agreed with Elias: push to `main` (Rares's agent pulls only), full night, full
Data/ML lane, per `docs/nightly-plan.md`. Already shipped: GitHub and OpenAlex OSINT
source adapters + shared `_support`, root README. Starting the loop.

## 2026-07-19, cycle 1
Task 3.9: expand OSINT palette. Fanned out a Workflow (3 parallel agents) to draft
Hacker News (PUBLIC_SOCIAL), PatentsView (PATENT), and Semantic Scholar (RESEARCH)
source adapters + deterministic tests, mirroring the OpenAlex template on the shared
_support helpers. Fixed one mypy nit (invariant dict in a test helper). Green: 71
tests, ruff, mypy. Palette now: GitHub, OpenAlex, HN, PatentsView, Semantic Scholar.
Note: response shapes follow each API's public docs; live-API field validation is
deferred to when adapters run against real endpoints. No NEEDS ELIAS.

## 2026-07-19, cycle 2
Task 3.2: cross-source entity resolution (the "collapse one founder from many
footprints" piece Elias flagged). Built `ingestion/identity.py`: a pure, reversible
`resolve_identities()` over provider-neutral `IdentitySignal`s using union-find.
Rules: signals from the same source record are one entity; a shared strong identifier
(handle / profile URL / external id / email, URL-normalized) links records across
sources at high confidence; a display name matching across two or more independent
source categories is surfaced as one entity but flagged NEEDS_REVIEW (a name alone is
not proof); same-name-same-source records stay separate; single-source records resolve
at low confidence. Built it myself (not fanned out) since the matching logic is subtle.
8 deterministic tests. Green: 79 tests, ruff, mypy. Commit 98df322.
Scope note: this is in-lane and self-contained. Merging resolved entities into the
canonical Memory store (SubjectRef / Founder / Company) is the SWE persistence layer
and a later paired step, so no shared-contract change was made. Confidences are
heuristic and uncalibrated by design. No NEEDS ELIAS.

## 2026-07-19, cycle 3
Task 3.4 (the two most foundational score containers): v0 `screening/rubrics.py`
producing the frozen `ClaimTrustScore` and `FounderScoreSnapshot`, deterministic and
versioned (`claim-trust-rubric.v0`, `founder-score-rubric.v0`). Ran it as an ultracode
workflow: 3 diverse-lens design agents (psychometrics / anti-gaming / fairness) ->
1 synthesis+build agent -> 3 adversarial verifiers. I then read the module myself and
re-ran the authoritative full gate. Green: 124 tests (45 new), ruff, mypy.
Design highlights: claim trust picks a STATE before a number (UNSUPPORTED/UNSCORED
withhold rather than fake a low score); corroboration is positive-only and
contradiction negative-only, so absence never moves a score; founder score hard-zeroes
vanity signals (follower reach, pedigree, polish, team size), weights costly-to-fake
positives, and only a present evidence-backed negative can go sub-baseline; coverage
touches only provisional/uncertainty, never the arithmetic; cold-start rests at 50, not 0.
The three invariants (missing-history-never-decrements, reproducible-and-versioned,
contract-and-no-false-precision) were verified exhaustively (all 4096 trust-signal combos,
198 founder cases, 200 shuffled orderings x 3 hash seeds -> byte-identical). Commit 61868d8.
Scope note: this is the [DATA/ML] numeric-weighting half of task 3.4; the thesis-rule,
contradiction, trend-sufficiency, and decision-readiness rubrics remain for later cycles.
Two v0 calibrations worth a glance in the morning (not blockers, logged not parked):
(a) a single costly-to-fake positive under HIGH coverage is LOW-uncertainty yet still
`provisional` (present_costly < 2) -- confirm that split is intended; (b) founder score
can reach exactly 100.0 when all costly positives are FULL; v0 permits it via clamp,
a v1 may want a cap below 100. No NEEDS ELIAS.

## 2026-07-19, cycle 4
Task 3.11 (Area of Research 1 confidence method): `screening/confidence.py`, a
framework-neutral, deterministic, versioned (`ar1-confidence.v0`) estimator over
caller-supplied score samples, no live model. Three pure entry points:
`estimate_confidence_band` (robust median point + a coarse band whose WIDTH, never the
center, absorbs dispersion / thin-coverage / few-sample penalties; a four-factor product
confidence in [0,1]; explicit reason-coded abstention that never lowers the point),
`identity_swap_bias_check` (signed median shift under a counterfactual identity swap,
flagged past a threshold, abstains when a side is empty), and
`subgroup_calibration_report` (binned expected calibration error per subgroup, small
subgroups flagged not dropped). Non-finite inputs rejected at the boundary. Own frozen
dataclasses, no domain model touched. Built as a design-council + adversarial-verify
workflow; interrupted once by the overnight Windows reboot, then resumed from cache with
nothing lost. All three invariants hold (720-permutation and multi-hash-seed sweeps).
42 tests. Gated scoped (a second workflow was concurrently writing founder_reads.py):
166 green excluding the in-flight file, ruff + mypy clean on the module. Commit 7fafeb9.
Notes (logged, not blockers): the band is a dispersion interval, not a standard-error
confidence interval (documented in-module); product-form confidence hits exactly 0 when
any single channel zeroes, meaning "declined / low support" not "certainly wrong"; the
bias check is threshold-only, a v1 could compare the shift against pooled spread. No NEEDS ELIAS.

## 2026-07-19, cycle 5 (parallel with cycle 4)
Task 3.10: builder-signal vs fundability reads. Launched as a SECOND workflow running
concurrently with cycle 4 on disjoint files (Elias asked what else could run alongside),
integrated separately with scoped then full gating so the two never collided.
`screening/founder_reads.py`: two evidence-graded reads over the frozen trait taxonomy,
versioned (`founder-reads.v0`). builder_signal_read counts only costly-to-fake /
peer-validated / outcome-linked substance (vanity hard-zeroed via registry max_weight);
fundability_read models conventional VC pattern-matching (pedigree, presentation,
audience, team) and under-weights deep craft a VC never inspects; the two zero-sets are
mirror images, which is what lets the reads diverge in both directions. An A/B/C grade
ladder (1.0/0.6/0.3) is a real anti-gaming ceiling: pure self-assertion caps at 70.4,
below STRONG. builder_fundability_gap surfaces the signed gap + a label
(under_networked_strong_builder / substance_light_but_fundable / aligned), never blended.
Adversarial verify caught the gap rationale overclaiming "substance-light" when substance
is actually unrated (50) or moderate; I applied the fix (gate the wording on the builder
level) and added two honesty regression tests. 30 tests; full suite 196 green. Commit 7b40625.
Note: grade monotonicity is defined on magnitude (the negative factor subtracts more at a
stronger grade); a reviewer wanting signed monotonicity would keep negatives in the
Founder Score only. No NEEDS ELIAS.

## 2026-07-19, cycle 6
Task 3.4 axis half: the three INDEPENDENT screening axes. `screening/axes.py`,
deterministic + versioned (`axis-rubric.v0`), producing the frozen FounderAxisAssessment
/ MarketAxisAssessment / IdeaVsMarketAxisAssessment. One internal AxisPosition computed
from KnowledgeValue signal reads (only KNOWN readings vote) is mapped to each axis's own
four-value vocabulary. The fairness rule is asymmetric on purpose: a sufficiency gate
(>=2 present reads under MEDIUM/HIGH, >=3 under LOW) plus a strict gate that downgrades a
would-be NEGATIVE under LOW coverage to UNKNOWN, so thin evidence yields UNKNOWN and never
WEAK/BEAR, while a well-corroborated cold-start positive is still credited. A pole needs a
clear net lean AND only minor opposition, else MIXED (conflict surfaced, not blended).
Confidence is an explicit-unknown KnowledgeValue when nothing is assessable; trend needs
dated history (UNKNOWN, never STABLE, below the floor). No averaging, no aggregate, no
cross-axis leakage; assemble_independent_axes only bundles. Design-council +
adversarial-verify workflow; all invariants hold (13,824-case sweeps, cross-axis
independence proven byte-for-byte). 31 tests; full suite 227 green. Commit cef569c.
The screening judge now has both halves: numeric rubrics (claim trust + founder score),
the three axes, builder-vs-fundability, and AR1 confidence. Next: assemble the
candidate-keyed preliminary Assessment Envelope (3.3). No NEEDS ELIAS.
