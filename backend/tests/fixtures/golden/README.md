# Golden candidate fixtures

This directory is the deterministic, provider-neutral evaluation corpus for the
P0 sourcing and screening contracts. Every person, company, identifier, claim,
and source is fictional. Source URLs use the reserved `.invalid` top-level
domain and must never be fetched.

## Contract assumptions

- `fixture_schema_version` is `golden_candidate.v0.1.0`. It versions the test
  envelope, not a runtime API or persistence schema.
- Timestamps are UTC and evaluated against the fixed
  `evaluation_as_of` value. Tests must not substitute the current clock.
- Knowledge states use `known`, `unknown`, `not_disclosed`,
  `not_applicable`, and `conflicted`. A `known` value has `value` and Evidence;
  every other state has a reason. `conflicted` also retains its sourced
  alternatives.
- Thesis outcomes use `match`, `mismatch`, `unknown`, and `not_evaluated`.
  `not_evaluated` is reserved for a thesis criterion configured as
  `no_preference`.
- Identity outcomes use `create_new`, `link_existing`, `link_duplicate`, and
  `human_review`. The cross-signal fixture proves a safe duplicate link; the
  same-name fixture proves that name equality alone must not merge people.
- `expected.coverage.missing_history_quality_penalty` is always `0`. Sparse
  history may lower coverage and widen uncertainty, but it must not reduce a
  founder-quality factor or turn an Unknown into a negative fact.
- The exact Founder Score, Trust Score, conviction threshold, and numeric
  coverage rubric are deliberately not frozen here. Those are calibrated in
  later OpenSpec tasks. Fixtures assert state, provisionality, evidence usage,
  and qualitative outcome through `exact_numeric_score: "not_asserted"`.
- A Source Artifact records acquisition metadata. An Observation records what
  one source says. Evidence supplies the precise locator and polarity used by
  a Claim or expected result. Search-provider snippets are not represented as
  primary Evidence.

## Corpus coverage

The machine-readable [manifest](manifest.json) lists five examples spanning
developer activity, product launches, hackathons, research, accelerator
cohorts, and approved public updates. Together they cover a cold-start founder,
a returning founder, independent cross-signal corroboration, a safely linked
duplicate, same-name identity ambiguity, and a seeded traction contradiction.

