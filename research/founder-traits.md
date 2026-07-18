# Founder Trait Taxonomy (reference for the Founder axis and Founder Score)

Backs the `opportunity-screening` requirement "Founder scoring uses an evidence-graded trait taxonomy" and tasks 3.4 / 3.10. This is a research reference for whoever calibrates the rubric, not a spec artifact.

## The core reframe: three lists people conflate

1. What VCs **say** they value (rhetoric): determination, founder-market fit, storytelling.
2. What **data** says predicts building success: a prior successful venture, founder age (peak ~45), conscientiousness, prior operating experience.
3. What is **observable** from public sources: shipping cadence, writing depth, external contributions, registry-verified prior ventures.

The trap: most *measurable* signals predict who gets **funded**, not who **succeeds**, and funding is biased. Anchor scoring on building outcomes, never on who was previously funded.

## Trait taxonomy

### Tier A - evidence-backed, weight these

| Trait | Data strength | Best cheap proxy | Cold-start? | Watch-out |
|---|---|---|---|---|
| Building/shipping track record (prior founding + operating experience) | Strong | GitHub external-org PRs, launches, registry-verified prior companies | Partial | most measurable and predictive signal we have |
| Founder-market fit (earned domain insight) | Rhetoric-strong, empirically sector-dependent | role-industry alignment, domain-specific writing/OSS | Yes | judgment call, not a metric |
| Execution cadence / follow-through (conscientiousness) | Moderate (most consistent Big Five) | shipped releases, closed-issue ratio, code longevity, writing cadence | Yes | gameable (green-square farming); cap it |
| Drive / relentlessness (need-for-achievement) | Moderate (r~.25-.30); #1 in VC rhetoric | multi-year persistence arcs, "kept going" over job-hopping | Partial | hard to measure honestly; do not confuse with charisma |

### Tier B - real but soft

| Trait | Note | Proxy | Watch-out |
|---|---|---|---|
| Communication / clarity of thinking | strongest cold-start signal | writing depth (costly to fake) | **charisma is an anti-signal**: high-polish pitches predicted worse outcomes; score for insight, not fluency |
| Ability to recruit / leadership | empirically endogenous | who chose to work with them (co-contributors, hires) | raw network size = bias |
| Coachability / integrity | deal-killer if absent | cross-source consistency (feeds Trust), "changed my mind" writing | mostly a consistency/reference check |

### Tier C - folklore, anti-signals, bias flags (do NOT weight positively)

- Charisma / pitch polish -> anti-signal
- Elite pedigree / brand-name employer -> predicts *funding*, not *success*; bias flag to discount
- Youth -> refuted (successful-founder age peaks ~45)
- Team size / # co-founders -> predicts capital raised (~21% more), not outcomes (~uncorrelated)
- Follower counts / GitHub stars / LinkedIn endorsements -> gameable vanity (6M fake stars found in one study); cap hard
- "Grit" as its own trait -> ~4% variance, redundant with conscientiousness

## Cold-start subset (costly-to-fake, peer-validated)

- Writing scored for specificity + non-obvious insight + evidence of iteration (not length/polish)
- Narrative coherence across artifacts (landing page <-> writing <-> application)
- A live product / waitlist with even ~10 real users
- Peer-validated community footprint (accepted Stack Overflow answers, cited Hacker News comments)
- The application itself (least-gated artifact)

All emitted with a wide confidence band + evidence-coverage count, never a confident low score.

## Fundability vs builder signal (the differentiator)

Compute a builder-signal read (costly-to-fake, outcome-linked) separately from a fundability read (network, pedigree, prior funding). Surface strong-builder, low-fundability founders as underrated. That gap is the brief's equitable-capital-allocation thesis turned into a feature.

## Confidence (Area of Research 1)

Demo path: sample the reasoned sub-score N times at temperature > 0; the band is the dispersion of those samples. Works on Claude (no log-probabilities needed). A large snap-versus-reasoned divergence lowers confidence. Research stretch: token-level logprob/entropy via a logprob-capable model (OpenAI top_logprobs) or a local small-model ensemble on HPC; caveat for the writeup: a small local ensemble measures the small models' confusion, not the strong model's epistemic uncertainty. Numeric intervals only after documented calibration.

## Anti-bias guardrails

- Anchor calibration on observed building outcomes, never on funding history (funding launders bias into the label).
- Standing counterfactual swap tests (name / school / pronoun) that flag a material score delta as a bias defect.
- Per-subgroup calibration reporting.
- Below a coverage threshold, abstain with a wide band or route to human review rather than emit a confident low score.

## Predictive validity (Area of Research 3)

Hold-out founders with known later outcomes, scored blind. Report Spearman rank agreement between predicted rank and realized outcome, the calibration and coverage of the confidence bands, and a comparison against a naive capital-raised baseline; report per identifiable subgroup where sample size permits.

## Sources

- Azoulay, Jones, Kim, Miranda, *Age and High-Growth Entrepreneurship* (NBER w24489) - successful-founder age peaks ~45.
- Gompers, Kovner, Lerner, Scharfstein, *Performance Persistence* - serial-founder ~30% vs 18% first-timers.
- Tamaseb, *Super Founders* - unicorn-founder dataset; team size and pedigree weak.
- Crede, Tynan, Harms, *Much Ado About Grit* - grit ~4% variance, redundant with conscientiousness.
- Song & Allen (charisma trap) - high-charisma pitches predicted worse follow-on/survival.
- *Discrimination in the Venture Capital Industry* (arXiv 2010.16084), HKS gender/VC review, Crunchbase diversity data - funding bias.
- 2026 YC study (arXiv 2512.13755) - credential models explained under 4% of funding variance; team size ~21% more capital.
- GitHub fake-stars study (arXiv 2412.13459).
