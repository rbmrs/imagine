# Terminal Video Creation API Integration

Working note created on 2026-03-10 from:
- User-provided PDF: `/Users/rafaelbm/Downloads/Terminal Video Creation API Integration.pdf`

Purpose:
- Preserve the useful parts of the PDF as implementation context for `Imagine`.
- Keep the conclusions actionable for future asset/TUI work.
- Avoid re-parsing the PDF every time we revisit video-source strategy.

Note:
- This is a distilled research note, not a verbatim extraction.
- Some provider feasibility/licensing points were checked against official docs on 2026-03-10 and 2026-03-11.

## Core Thesis

The PDF is directionally right about the main problem:
- better YouTube video quality will require more visual diversity than only Pexels + Pixabay video search
- a multi-source asset layer is better than a single-provider pipeline
- licensing and attribution must be first-class, not an afterthought
- duration fit and aspect-ratio fit matter as much as raw provider count

The most important product takeaway for `Imagine` is this:
- quality improves more from better candidate ranking and better human review controls than from simply adding many more providers

## What From The PDF Is Worth Keeping

These ideas should remain part of the working direction:

1. Unified search and normalization
- Query multiple providers.
- Normalize all results into one internal candidate model.
- Deduplicate before selection.

2. Duration-aware filling
- Favor clips that fit the requested scene duration.
- Avoid blindly accepting short clips that force bad repeats or weak placeholders.

3. Vertical / aspect-ratio awareness
- Track whether a candidate is naturally close to target framing.
- Do not rely only on naive crop behavior when quality matters.

4. Automated rights tracking
- Preserve source, creator, license, attribution, and restriction metadata in machine-readable form.
- Keep this tied to each selected asset and review record.

5. Hybrid retrieval strategy
- Search stock sources first.
- Reserve AI generation for explicit fallback or premium mode.

## What To Treat Carefully

The PDF grouped several ideas together that should stay separate in the actual architecture:

1. Source providers
- Pexels
- Pixabay
- Vecteezy
- Coverr
- GIPHY Clips

2. Transform / delivery services
- ImageKit
- Cloudinary
- Mux
- api.video
- Shotstack

3. Generative fallback
- Veo
- Runway
- Sora
- Kling

These categories solve different problems and should not be implemented as one generic “provider” layer.

## Provider Assessment For Imagine

### Keep as foundation

Pexels:
- Good default source.
- Official API is suitable for both videos and photos.
- Commercial-safe default posture is compatible with current repo direction.

Pixabay:
- Good default source.
- Useful because it covers both video and image search.
- Commercial-safe default posture is broadly compatible with current repo direction.

### Good next provider, but only with policy gates

Vecteezy:
- Best serious next provider candidate from the PDF list.
- Must be added with explicit policy handling for attribution/editorial/commercial constraints.
- Should not silently enter the default safe path until licensing rules are modeled clearly.
- Official developer pricing currently shows free general API calls and 500 downloads per month on the free API tier.
- The V2 API exposes separate `general` and `download` quota buckets, which is useful for future balancing logic.
- The free license also includes usage restrictions beyond attribution; for videos, digital use is allowed for unlimited views but production use is limited to projects with budgets up to `$1,000`.

### Not recommended as default runtime provider

Coverr:
- Directionally interesting for more cinematic footage.
- Not a good default fit for current strict commercial-safe behavior.
- Attribution/logo and commercial constraints make it risky as a silent default.

Mixkit:
- Valuable as inspiration and possibly for manual curation.
- Weak fit for runtime automation.
- Better treated as manual ingest or a pre-curated local source, not a live API-backed provider.

GIPHY Clips:
- Niche source for reaction, loop, or transition moments.
- Not a strong default b-roll source for long-form explainer quality.

### Optional premium fallback, not default

AI video providers:
- Useful for specific visual gaps.
- Should be opt-in, cost-aware, and clearly separated from the free stock path.
- Best added only after stock search, ranking, and review controls are solid.

## What Matters More Than More Providers

This is the key correction to the PDF from the repo’s perspective:

- More providers alone do not guarantee better output.
- Better ranking, shortlist visibility, and review control are the bigger wins.

For `Imagine`, the real order of value is:

1. normalized candidate model
2. ranking and rejection logic
3. shortlist-aware review UX
4. provider expansion
5. smart crop / transform services
6. AI gap filling

## How This Maps To The Current Repo

Relevant code areas:
- `src/local_video_mvp/models.py`
- `src/local_video_mvp/pipeline.py`
- `src/local_video_mvp/tui.py`

Current status as of 2026-03-10:
- candidate normalization exists
- Pexels and Pixabay video + image search are now supported
- ranked candidate shortlists are stored in `review/clip_catalog.json`
- TUI can inspect stored candidates
- TUI can choose a stored candidate directly during scene review

This means the repo is no longer just “search and take the first decent clip”.

## Working Architecture Direction

Recommended asset stack:

1. Provider fetch layer
- one adapter per provider
- provider-specific API parsing
- no selection logic here

2. Normalized candidate layer
- unified fields such as:
  - `media_type`
  - `duration_seconds`
  - `width`
  - `height`
  - `license_name`
  - `license_url`
  - `attribution_required`
  - `restriction_flags`
  - `ranking_score`
  - `quality_score`

3. Ranking / policy layer
- duration fit
- aspect-ratio fit
- resolution quality
- provider trust
- uniqueness / diversity
- commercial-safe gating

4. Review layer
- show shortlist
- show why a candidate ranked well
- allow direct candidate choice
- allow re-search when shortlist is bad

5. Optional premium fallback layer
- AI generation
- smart crop / transform APIs
- advanced moderation/tagging

## Practical Guidance For Future Work

Next good moves after the recent shortlist-selection work:

1. TUI asset strategy controls
- allow/disallow still images
- provider enable/disable
- provider priority
- attribution-safe gating
- future vertical-output preference

2. Stronger policy model
- explicit `editorial_only`
- explicit `commercial_safe`
- explicit attribution display requirements

3. Better ranking inputs
- scene-intent relevance
- stronger search query generation
- face / subject density when vertical output is added

4. Additional providers
- start with Vecteezy only if policy handling is ready
  - and treat it as a lower-priority secondary source on free accounts

5. Local curated source support
- allow a local library of pre-approved high-quality assets
- this may be more valuable than scraping weak providers

6. Optional transform services
- smart crop only when vertical output becomes a product priority

## Why Keeping This Note Helps

Yes, this should help.

Why:
- it avoids reparsing the PDF in future sessions
- it preserves the implementation conclusions, not just the raw text
- it captures the provider caveats that matter to the repo
- it gives us a stable local artifact we can reopen while planning

Why a distilled note is better than a raw extraction:
- the PDF contains useful direction but also some ideas that are not a clean fit for this repo
- the note keeps the valuable context while filtering out the noisy or weakly actionable parts

## If Needed Later

If we want a second artifact, the next useful one would be:
- a provider matrix with columns for:
  - official API
  - media types
  - attribution requirement
  - commercial-safe default
  - rate limit
  - implementation priority

That would be the best companion document to this note.
