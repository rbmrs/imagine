# Asset Provider Matrix

Last updated: 2026-03-11

Purpose:
- Keep a single decision table for video/image asset sources relevant to `Imagine`.
- Separate implementation priority from licensing/policy fit.
- Make future provider decisions faster and less dependent on chat history.

Current policy decisions:
- Free providers only for now
- Attribution-required sources are usable by default
- Manual-ingest sources should be tracked
- AI video providers are future-only

Important nuance:
- “Attribution-required is usable by default” does not mean every provider can be treated identically.
- Some providers are satisfied by credits in the YouTube description.
- Some providers appear to require stronger in-product attribution or logos, which should be treated as conditional rather than silently accepted.

## Status Key

- `Now`: good fit for current implementation work
- `Later`: valid target, but only after stronger policy/UI support
- `Conditional`: technically usable, but provider terms do not map cleanly to current UX
- `Manual`: useful as a local curated source, not a runtime API integration
- `Future`: intentionally out of near-term implementation scope
- `Avoid`: poor fit for the current product direction

## Matrix

| Provider | Source Type | Official API | Media Types | Attribution / Commercial-Safe Summary | Default Policy Fit | Implementation Priority | Notes |
|---|---|---|---|---|---|---|---|
| Pexels | API | Yes | Video, Image | Official API docs say to provide a prominent Pexels link and credit the photographer/source when possible. | Yes, with attribution support | Now | Strong foundation provider; already integrated. Official docs also state a free API limit of 200 requests per hour and 20,000 per month. |
| Pixabay | API | Yes | Video, Image | API license is broadly usable for royalty-free media, but the docs require showing users where search results come from and preserving links back to Pixabay when results are displayed. | Yes | Now | Strong foundation provider; already integrated. Official API rate limit is 100 requests per 60 seconds, with `X-RateLimit-*` headers and HTTP 429 on excess. |
| Vecteezy Free | API | Yes | Video, Image, SVG, PNG | Free API access is usable, but free-license content requires attribution on all end products and the free license still carries usage constraints. Paid plans remove attribution burden; paid is out of scope. | Yes, but policy-sensitive | Later | Strong next candidate, but should be a gated secondary provider. Official developer pricing currently shows free general API calls and 500 downloads per month. Live `account/info` on this free account reported `download.call_limit=500` on 2026-03-11. |
| Coverr API | API | Yes | Video | API is free, but docs say free API access is for cases where you do not charge for it and requires showing a clickable Coverr logo. This does not map cleanly to a YouTube-description-only workflow. | Conditional | Conditional | Good content quality, but not a clean default fit without provider-specific attribution handling. |
| GIPHY Clips | API | Limited / approval-gated | Short video with sound | Access requires approval and attribution. Better for reactions/transitions than long-form explainer b-roll. | Conditional | Later | Keep as niche source, not core footage layer. On 2026-03-11, the provided beta key returned `403 Forbidden` on live Clips search while regular GIF search succeeded, which confirms the approval gate is active for this account. |
| Mixkit | Manual ingest | No strong public runtime API | Video, Audio, Templates | Free and restricted item types exist. Runtime automation is weak, and terms/license handling are not clean enough for unattended provider integration. | Manual only | Manual | Treat as curated local library, not a live provider. |
| Mazwai | Manual ingest / possible later integration | Not selected for direct integration now | Video | Commonly discussed as attribution-required. Could be useful, but policy/load path is not yet worth the complexity. | Manual or later | Manual | Keep in research pool, not near-term implementation. |
| Local Curated Library | Manual ingest | N/A | Video, Image | Best place for pre-approved generic b-roll and stills that we can cache locally. | Yes | Now | This is the most practical manual-ingest path. |
| AI Video Providers | AI generation | Varies, mostly paid | Video | Out of current scope because the team wants free providers only for now. | Future-only | Future | Track separately from stock-source decisions. |

## Practical Interpretation

### Safe current foundation

Use and improve:
- Pexels
- Pixabay
- Local curated library

### Best next serious expansion candidate

Add later:
- Vecteezy Free

Why later:
- it is a real API-backed source
- it expands both video and image coverage
- but it needs stronger attribution and licensing policy handling before it should be enabled by default
- free-plan economics are materially tighter than Pexels/Pixabay because downloads are capped monthly

## Provider Limit Notes

### Pexels

- Official API docs currently state `200 requests per hour`.
- Official API docs also state `20,000 requests per month`.
- This is workable as a primary provider, but we should still avoid wasteful re-search and lean on shortlist caching.

### Pixabay

- Official API docs currently state `100 requests per 60 seconds`.
- Pixabay exposes `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`.
- This is generous enough for current scene-based search, especially with query caching.

### Vecteezy Free

- Official developer pricing currently shows:
  - general API calls are free
  - downloads are free
  - max downloads are `500/month`
- Official V2 API schema separates `general` and `download` quotas via the account endpoint.
- Official license terms for free content also matter:
  - attribution is required on all end products
  - free videos permit unlimited digital views
  - free videos are limited to projects with production budgets up to `$1,000`
- On 2026-03-11, the live `account/info` response for the provided free account reported:
  - `current.general.call_count = 0`
  - `current.general.call_limit = 0`
  - `current.download.call_count = 0`
  - `current.download.call_limit = 500`
- Important caution:
  - the docs clearly model quota headers for general endpoints, but the quick live requests did not return those headers in our test
  - the meaning of `general.call_limit = 0` is not clearly documented on the public docs page, so we should not assume an unlimited search budget without observing account behavior over time

## Recommended Access Strategy

### Current best balance

- Keep Pexels and Pixabay as primary providers.
- Add Vecteezy only as a lower-priority provider.
- Query Vecteezy only when:
  - primary providers produced weak shortlists, or
  - the user explicitly enables it for broader coverage

### Why this balance is better

- Pexels and Pixabay have simpler operational economics for repeated search.
- Vecteezy free is better used as a quality-expansion pool than as a default every-scene provider.
- This reduces the risk of burning monthly Vecteezy downloads on scenes that Pexels/Pixabay could already satisfy.

### Implementation implication

- Vecteezy should be integrated with:
  - provider toggle support
  - visible attribution/provider-warning text
  - persistent local caching of downloaded assets
  - a lower default search priority than Pexels and Pixabay

### Useful but not clean default fits

Conditional:
- Coverr API
- GIPHY Clips

Why conditional:
- they have provider-specific attribution/product-display requirements that are not the same as “put credits in the YouTube description”

### Good to track, not good to automate live

Manual:
- Mixkit
- Mazwai

Why manual:
- useful quality pool
- weak or unsuitable runtime automation fit
- better as local pre-curated content

## Why Attribution Was Initially A Risk Concern

Even though the current policy is “usable by default”, the reason to be cautious was never “writing credits is hard”.

The actual risks were:
- attribution rules differ by provider
- some require platform/logo visibility, not just creator credit
- some attach usage limits to free licenses
- the product did not yet auto-export or validate credits at publish time
- a silent compliance miss is worse than a slightly smaller asset pool

That is why the conservative default was initially attractive.

Now that the team preference is clear, the better rule is:
- allow attribution-required providers in the matrix and roadmap
- but keep provider-specific exceptions visible
- never assume “YouTube description credit” satisfies every provider

## Recommended Product Rules From This Matrix

1. Add a TUI/provider policy layer
- allow provider enable/disable
- allow “attribution-required sources”
- show provider-specific warnings

2. Add exportable credits
- generate a YouTube-description-ready credits block from `rights_manifest.json`

3. Keep provider-specific gates
- some providers may still require conditional handling even if attribution-required sources are generally allowed

4. Build a local curated asset library
- this is the right place for Mixkit-like sources and pre-downloaded generic b-roll

## Source Links

- [Pexels API documentation](https://www.pexels.com/api/documentation/)
- [Pixabay API documentation](https://pixabay.com/api/docs/)
- [Vecteezy API docs (V2 Swagger)](https://www.vecteezy.com/api-docs/api/v2/swagger.json)
- [Coverr API docs: Before you start](https://api.coverr.co/docs)
- [Coverr API docs: How to access the API](https://api.coverr.co/docs/start/)
- [Vecteezy developers](https://www.vecteezy.com/developers)
- [Vecteezy licensing](https://www.vecteezy.com/licensing)
- [Vecteezy license agreement](https://www.vecteezy.com/licensing-agreement)
- [Mixkit license](https://mixkit.co/license/)
- [Mixkit terms](https://mixkit.co/terms/)
- [GIPHY Clips docs](https://developers.giphy.com/docs/clips/)
