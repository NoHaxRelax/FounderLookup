# FounderLookup frontend

React + TypeScript + Vite starter for the four P0 demo surfaces:

- founder Application intake and bounded founder status;
- thesis, compound sourcing, candidate activation, and outreach intent;
- Opportunity detail with independent axes, Claims, Trust, Evidence, and contradictions;
- cited memo, Recommendation, and explicit human Decision.

The app starts in deterministic fixture mode, so frontend work does not depend on a live
provider, investment-intelligence model, or orchestration framework. All data access crosses the
typed `FounderLookupClient` interface in `src/api/`. Fixture interpretation is demo data; it is not
presented as a backend model result.

The founder intake intentionally requires only company name and a PDF deck (10 MiB maximum,
matching the backend default). Its idempotency key is retained across retries of the same payload,
and submission errors preserve form state. Candidate activation is outbound-only and records the
human-edited outreach draft without sending it.

## Run and verify

```bash
npm install
npm run dev
npm run typecheck
npm run lint
npm test
npm run build
```

`.env.example` records the non-secret local API URL for a host integration. The starter does not
automatically switch clients from an environment variable: `App` deliberately defaults to the
fixture client. Never put an investor credential or founder-status capability in a `VITE_*`
variable because Vite embeds those values in browser assets.

## Backend integration

`src/api/httpClient.ts` targets the implemented FastAPI `/api/v1` surface. Construct
`HttpFounderLookupClient` with the full versioned base URL (for example,
`http://localhost:8000/api/v1`) and a runtime investor-credential callback, then inject it into
`App`. The browser model remains camelCase; `contractAdapter.ts` is the explicit snake_case wire
boundary.

The HTTP client uses only these implemented operations:

| UI operation | FastAPI contract |
| --- | --- |
| Initial investor workspace | compose `GET /theses/active`, `GET /outbound-candidates?limit=50`, and `GET /opportunities?limit=50` |
| Opportunity detail | `GET /opportunities/{id}?expand=claims,evidence`, then related `GET /runs/{id}` |
| Typed query execution | `POST /queries` with `{ "plan": <validated QueryPlan> }` |
| Founder application | public multipart `POST /applications` with `company_name` and `deck` plus `Idempotency-Key` |
| Founder status | public `GET /founder-status` with `X-Founder-Status-Capability` |
| Candidate activation | `POST /outbound-candidates/{id}/activate` with `outreach_draft` |
| Human decision | `POST /opportunities/{id}/decisions` |

The founder capability lives in the URL fragment only for client-side return routing and is sent
to FastAPI only in the capability header. It is never placed in a query/path, analytics event, or
log by this starter.

Current backend gaps are exposed rather than papered over:

- FastAPI executes a validated typed Query Plan but has no natural-language-to-plan endpoint.
  Fixture mode demonstrates interpretation; HTTP mode asks for an already inspectable plan.
- Opportunity reads omit company/founder display names and Source Artifact classification/name
  metadata. The adapter labels stable IDs and preserves those fields as Unknown instead of
  inventing values.
- There is no `/workspace` aggregate resource, so the HTTP client composes the three real reads.
- A fresh backend has no active thesis; create the first thesis before HTTP workspace composition
  can return `GET /theses/active` successfully.
- The intake contract neither accepts nor stores a contact email, so the UI does not request one.

Keep provider response types and provider secrets behind the backend contract.

## Accessibility baseline

The starter uses semantic landmarks and forms, native `details` and `dialog`, a skip link,
visible `:focus-visible` outlines, text-plus-icon states, minimum 24 CSS-pixel targets, larger
coarse-pointer targets, responsive/container layouts, reduced-motion rules, and a forced-colors
fallback. Neumorphic shadows are decorative: borders, labels, and state text remain when shadows
disappear.

The palette follows a Tiaohe-style environmental-gray approach: rice-paper and celadon surfaces,
one consistent colored-shadow light source, and restrained cinnabar/jade saturation for hierarchy.

Before a demo, also check keyboard-only navigation, 320 CSS pixels, 400% zoom, forced colors,
reduced motion, and contrast in a real browser. Automated tests are useful but do not certify
WCAG conformance.
