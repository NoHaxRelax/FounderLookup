# Live demo acceptance

The merge contract is one network-free PRD journey:

```bash
cd backend
uv run pytest -q tests/integration/test_prd_demo_acceptance.py
```

It proves, against one service instance:

- a versioned Thesis and the compound technical-founder/Berlin/AI-infrastructure query;
- minimum company-plus-PDF intake, private immutable storage, the provider-neutral OCR seam,
  idempotent replay, founder status, full Screening, and human Decision;
- bounded outbound graph execution across hackathon, developer-activity, and product-launch
  sources, alongside an empty result and a safe partial provider failure;
- cache reuse without repeated acquisition, duplicate artifact, duplicate candidate, or repeated
  OCR;
- exact hackathon event/project/participant locators, unverified display identities, public contact
  provenance, and a separately acquired public deck;
- exact Google Slides showcase URL plus its derived `/export/pdf` acquisition URL and
  normalization provenance;
- only signature-checked `application/pdf` bytes reach public-deck OCR; HTML and arbitrary
  documents do not;
- Founder Score remains separate from three independent axes, with cold-start positive evidence;
- claim-level Trust, page citations, a blocking Contradiction, readiness, timing, the five required
  memo sections, Recommendation, and a separate human Decision;
- no autonomous outreach and no fund-transfer capability.

## Opt-in live provider proof

The live test is deliberately skipped by ordinary test runs. It exercises Tavily Search/Extract,
OpenAI strict structured extraction, bounded public PDF download, and Mistral OCR against the
approved Speechium showcase and its seven-page public deck.

Export the already configured keys into the test process, then run:

```bash
cd backend
set -a
source .env
set +a
FOUNDERLOOKUP_RUN_LIVE_TESTS=1 \
FOUNDERLOOKUP_LIVE_HACKATHON_URL=https://devpost.com/software/speechium-by-wako-ai \
uv run pytest -q -s tests/live/test_tavily_hackathon_pipeline.py
```

Success requires a concrete Mistral model beginning with `mistral-ocr-4`, seven non-empty OCR
pages, the exact public Slides edit URL and normalized export URL, safe telemetry, and no secret
value in persisted telemetry.

## Railway public-deck settings

Broad Tavily search does not authorize direct downloads. Production must separately set:

```dotenv
FOUNDERLOOKUP_PUBLIC_PDF_ALLOWED_DOMAINS=docs.google.com,googleusercontent.com
FOUNDERLOOKUP_PUBLIC_PDF_MAX_BYTES=5000000
FOUNDERLOOKUP_PUBLIC_PDF_TIMEOUT_SECONDS=30
FOUNDERLOOKUP_PUBLIC_PDF_MAX_REDIRECTS=5
```

`googleusercontent.com` permits its generated subdomains. Keep the excluded-domain setting empty
unless an explicit deny is needed. Mistral OCR must also be enabled for public classification;
missing or blocked OCR configuration remains an explicit safe Unknown while preserving the deck
artifact and provenance.
