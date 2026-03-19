# Local Video MVP - Project Onboarding

This document is a fast onboarding guide for engineers joining this project.

## What this project is

`local-video-mvp` is a local-first pipeline to generate faceless explainer videos from a prompt.

Core goals:

- Produce final video files locally on macOS (`final.mp4`, subtitles, manifests)
- Keep a strict commercial-safe baseline in v1
- Support long-form generation (not shorts)
- Prefer quality over speed

Current target defaults:

- Resolution: `1280x720`
- Duration target: user-defined with tolerance (`+/-25%` by default)
- Voice style default: `calm-documentary`
- Bookend style default: `minimal-clean`

## Tech stack

- Python CLI package (`src/local_video_mvp`)
- Local orchestration in one pipeline class (`VideoPipeline`)
- Ollama for script generation (`qwen2.5:14b` default)
- MeloTTS, Kokoro, and Piper TTS
- Kokoro and Piper voice previews in TUI debug (for local voice quality evaluation)
- faster-whisper or heuristic captions
- ffmpeg for audio/video rendering

Main files:

- `src/local_video_mvp/cli.py` - CLI commands and flags
- `src/local_video_mvp/models.py` - pipeline config dataclass
- `src/local_video_mvp/pipeline.py` - end-to-end generation pipeline
- `README.md` - setup and run instructions

## Pipeline at a glance

1. Generate script plan
2. Enforce duration constraints (expand/compress script when needed)
3. Synthesize narration
4. Resolve visual assets
5. Generate captions (`.srt` + `.ass`)
6. Build timeline (intro/content/outro)
7. Render final video with ffmpeg
8. Write rights/provenance manifest + run report

Outputs per run are created under `projects/<project-id>/`.

## What has already been implemented

### Baseline MVP

- CLI-first local generation flow
- Rights/provenance manifest
- Structured logs and run report

### Subtitles

- Engagement subtitle chunking (few words per line)
- Burned subtitles in final MP4
- Subtitle presentation controls for preset, raised `mid-safe` positioning, and boxed keyword highlight styling

### Duration control

- Tolerance policy (`+/-25%` default)
- Auto-expand short scripts
- Auto-compress long scripts
- Duration metrics in reports

### Voice and pacing

- Default voice profile: `calm-documentary`
- Chunk-based TTS with pauses
- Pause-boundary refinement to reduce awkward breaks
- Voice inventory + A/B preview command

### Visuals and bookends

- Video effects presets: `clean`, `subtle-motion`, `dynamic`
- Intro/outro support with configurable timing/text
- Bookend style presets (`minimal-clean`, `cinematic-subtle`, `brand-image-motion`, `corner-fade`)
- Brand-driven bookends support logo + custom intro/outro background images
- Branded bookends now support `channel_name`, `intro_tagline`, and `outro_tagline`
- `brand-image-motion` uses a logo-led asymmetric layout instead of a centered title card
- `corner-fade` uses resolved scene visuals under both the top-left intro and the lower-middle two-step outro CTA
- Intro/outro title wrapping + safer text rendering for punctuation-heavy titles
- Fast/debug mode for shorter lower-cost validation runs
- Default runs can reuse `projects/brand-kit/` assets for branded bookends when that kit is present, but intro stays off unless explicitly enabled
- Ollama runs now fail fast instead of silently degrading into template-script output

## CLI commands you should know

Run full pipeline:

```bash
local-video-mvp run --help
```

Inspect a run:

```bash
local-video-mvp inspect --project-dir ./projects/<project-id>
```

List Melo voices:

```bash
local-video-mvp voices --melo-language EN
```

List Kokoro voices:

```bash
local-video-mvp voices --tts-engine kokoro --kokoro-lang-code en-us
```

Generate voice A/B samples:

```bash
local-video-mvp voice-ab --project-dir ./projects/<project-id> --speakers EN-US EN-Default EN-AU
```

Generate Kokoro voice A/B samples:

```bash
local-video-mvp voice-ab --project-dir ./projects/<project-id> --tts-engine kokoro --kokoro-lang-code en-us --voices af_heart af_sarah am_michael
```

Inspect local YouTube draft/auth readiness:

```bash
local-video-mvp youtube-auth
local-video-mvp youtube-auth --link
local-video-mvp youtube-auth --disconnect
local-video-mvp youtube-draft --project-dir ./projects/<project-id>
local-video-mvp youtube-publish --project-dir ./projects/<project-id>
```

Launch the terminal UI (experimental):

```bash
imagine
```

`imagine` opens the TUI with default startup values. Use `local-video-mvp tui ...` for custom startup values.

Default startup values:

- prompt: `Autonomous cars`
- resolution: `720p` (`1280x720`)
- intro style: `corner-fade`
- outro style: `corner-fade` with two-step CTA
- asset keywords: `cars, roads`
- minutes: `2`
- script tone: `conversational`
- audience: `curious general audience`
- hook style: `surprising-fact`
- narrative mode: `story-led`
- example density: `balanced`
- voice: `EN / EN-US`, profile `calm-documentary`, speed `1.0`

Default storage behavior:

- TUI auto-creates run workspaces under `~/.imagine/projects/<prompt-slug>-<timestamp>/`
- TUI exports final video to `~/Downloads/<prompt-slug>-<timestamp>.mp4`
- intro title card now defaults to off and must be explicitly enabled in Settings when wanted

TUI shortcut focus for now:

- `R` opens run preflight (`minutes -> resolution -> subtitles`) and then runs/continues staged HITL flow for current workspace (`draft -> script review -> shot plan -> shot review -> preview -> finalize`).
- in `news` mode, `R` runs `sources -> source review -> draft -> script review -> shot plan -> shot review -> preview -> finalize` instead.
- `Y` opens the YouTube publish flow for finalized workspaces, including linked-account review, switch/disconnect, metadata editing, and thumbnail generate/pick/preview.
- `T` opens the standalone prompt-driven Thumbnail Studio so you can create thumbnail sessions from scratch, generate ComfyUI variants, and export a final thumbnail to Downloads.
- `P` opens the Prompt menu (`Video brief`, `Content mode`, `Script Profile`, `Asset keywords`, and `News sources` in `news` mode).
- `S` opens settings (`Minutes`, `Resolution`, `Subtitles`, `Voice`, `Voice profile`, `Voice speed`, `Intro title card`, `External editor`, `Thumbnail ComfyUI`, `Thumbnail export folder`, `Thumbnail Ollama hooks`, `HITL`, `Fast mode`, `Narration voice policy`, and `Visual Assets`).
- `C` opens workspace cleanup, where `Space` marks folders, `A` selects all, and `Enter` deletes.
- `Q` exits the TUI.

The TUI persists the last values changed via the `Prompt` menu, `Settings`, and Run preflight in `~/.imagine/tui_settings.json`. Startup values from CLI/defaults are used only when the persisted file is absent or contains invalid fields. The dedicated `Prompt` shortcut now stays small: `Video brief`, `Content mode`, `Script Profile`, `Asset keywords`, plus `News sources` when `Content mode` is `news`. `Script Profile` is a bundled preset that writes the hidden audience/hook/narrative/example settings for generation. When you switch to `news`, the TUI pre-fills a short curated RSS feed set that you can inspect and edit. Asset policy still covers provider enable/disable, still-image fallback, `asset mode` (`prefer video` through `images only`), `image motion style`, whether attribution-required sources remain eligible, and experimental `Coverr` / `Vecteezy` fallback toggles. Voice preview now lives in `Settings -> Voice` (`Enter` selects, `Space` previews), and Settings also hold the `External editor` command template, the `Intro title card` toggle, and standalone thumbnail settings for `Thumbnail ComfyUI`, `Thumbnail export folder`, and `Thumbnail Ollama hooks`. `Thumbnail ComfyUI` now opens a guided manager that can review status, auto-detect an existing install, auto-start it on launch, install a managed local copy, and change the URL or install path without leaving the TUI. TUI also surfaces strict-safe narration policy state directly in the main screen and inside Settings.

YouTube caption defaults now follow subtitle policy:

- when `Burned subtitles` is off, new YouTube drafts default caption upload to on
- when `Burned subtitles` is on, new YouTube drafts default caption upload to off
- finalized projects now auto-generate `output/thumbnail_yt.jpg`, so `Publish now` has a default thumbnail even if you never opened the thumbnail editor
- `Edit thumbnail` in YouTube publish now opens a Pillow-powered Thumbnail Studio for generated thumbnails, with hook suggestions, style presets, optional badge text, text plate contrast control, text/color/size/alignment/position controls, background regeneration, inline preview, quick CTR diagnostics, and an `Open in external editor` round-trip through `publish/thumbnail_work.png`
- oversized or unsupported thumbnails are auto-converted to an upload-safe JPEG before YouTube publish when `ffmpeg` is available
- YouTube caption upload requires the OAuth token to include `youtube.force-ssl`
- Shot Review now shows a miniature preview for the currently highlighted shot in the blocked-shot list, using the selected image directly or a still frame from the selected video before you press `Enter`

Checkpoint modals appear between stages so you can approve/review before continuing. In `news` mode the first checkpoint is `Source Review`, where each article candidate can be marked `Approve facts only`, `Approve facts + screenshot`, or `Reject`, and the flow stays blocked until the configured minimum approved-source/domain gate is satisfied. After draft, TUI opens script review first so you can read the full script, edit scene text, open it in your configured external editor, approve it, or regenerate only the script. Once the script is approved, the pipeline runs `shot-plan` and opens `Shot Review`, where the blocked-shot list now shows a miniature preview for the currently highlighted shot before you open it, and the per-shot screen still lets you review the rendered visual, final narration audio, and burned subtitles together. MVP top-level controls stay minimal: `Play`, `Approve`, `Regenerate`, `Try still image`, and `Refine keywords`, with `Esc` returning to the shot list. `Regenerate` now means next unused video clip, `Try still image` means next unused still image, and `Refine keywords` opens the manual editor path. Preview no longer auto-opens a media player; TUI leaves the workflow at the finalize checkpoint and shows the `review/preview.mp4` path so you can inspect it in your preferred player before pressing `R` again. Finalize reuses the approved preview render when inputs are unchanged; otherwise it re-renders. Manifest writes now also export `publish/youtube_description_credits.txt`, and new YouTube drafts append any required credits block from the rights manifest automatically.

TUI Downloads export now copies both the finalized video (`<project>.mp4`) and a separate thumbnail image (`<project>-thumbnail.jpg` when auto-generated).

For draft runs, TUI tries to start `ollama serve` automatically. If Ollama is still unavailable, TUI now blocks the run and shows an `Ollama unavailable` modal instead of generating placeholder narration.

Fast mode is intended for cheap validation passes. It caps runs to roughly 1 minute, lowers render cost, switches captions to heuristic timing, keeps burned subtitles enabled, uses shorter intro/outro cards, and allows placeholder assets so pipeline plumbing can be checked quickly.

For best in-terminal preview support, install `mpv`:

```bash
brew install mpv
```

Ghostty is supported through the kitty graphics path when available for shot-level terminal previews. Full preview playback is now manual from the shown `review/preview.mp4` path instead of a TUI modal or auto-opened player.

For Piper voice testing in debug mode, install runtime deps in the same environment used by TUI:

```bash
python -m pip install piper-tts
python -m pip install pathvalidate
```

Current Piper preview catalog includes curated EN-US options for rapid auditioning, but strict-safe mode blocks attribution-required LibriTTS entries and any non-allowlisted custom weights.

Current voice picker scope:

- language picker is restricted to `EN` for now
- speaker picker shows available EN speakers on this machine
- Kokoro English voices are available in TUI voice selection and Settings voice previews
- voice pickers now show strict-safe badges plus per-entry policy details while you browse
- Piper previews are limited to allowlisted presets when strict-safe mode is active
- the selected narration voice is also reused for the spoken outro CTA, and the PT-BR religious channel profile now localizes that ending to Portuguese

TUI stock key discovery order:

1. process env (`PEXELS_API_KEY`, `PIXABAY_API_KEY`, `COVERR_APP_ID`, `COVERR_API_KEY`, `VECTEEZY_ACCOUNT_ID`, `VECTEEZY_API_KEY`)
2. repo `.env`
3. `~/.config/imagine/stock_api_keys.json` (or `IMAGINE_STOCK_KEYS_FILE`)

`Coverr` and `Vecteezy` remain off by default and are intentionally treated as lower-priority fallbacks. `Coverr` is video-only, uses local hourly request tracking for demo-style access, and marks selected assets as attribution-required with provider-branding reminders. `Vecteezy` can contribute both video and still-image fallback candidates and records its quota snapshot into `run_report.json` and `rights_manifest.json`.

Hard guard behavior (no placeholders allowed):

- if no stock keys are found, run is blocked before start
- if any scene resolves to placeholder, run is rejected
- if unique clips are exhausted, run is paused before render and TUI asks for broader keywords

TUI logs are always written to `~/.imagine/logs/`:

- `latest.log` (pointer to latest session)
- `tui-YYYYMMDD-HHMMSS.log` (session file)

## Recommended local run pattern

Use one terminal with automatic Ollama lifecycle and venv cleanup:

Team note: this is the preferred execution pattern for this project.

```bash
source .venv/bin/activate
set -a; source .env; set +a

OLLAMA_PID=""
cleanup() {
  if [ -n "$OLLAMA_PID" ]; then
    kill "$OLLAMA_PID" 2>/dev/null || true
    wait "$OLLAMA_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! pgrep -f "ollama serve" >/dev/null; then
  ollama serve >/tmp/ollama.log 2>&1 &
  OLLAMA_PID=$!
  sleep 2
fi

local-video-mvp run \
  --workflow-stage draft \
  --prompt "Your topic" \
  --project-dir ./projects/demo

local-video-mvp run \
  --workflow-stage draft \
  --prepare-scene-review \
  --prompt "Your topic" \
  --asset-keywords "cars, roads" \
  --project-dir ./projects/demo \
  --minutes 5 \
  --resolution 1280x720 \
  --script-engine ollama \
  --ollama-model qwen2.5:14b \
  --require-ollama \
  --script-tone curiosity-driven \
  --target-audience "curious general audience" \
  --hook-style surprising-fact \
  --narrative-mode story-led \
  --example-density balanced \
  --tts-engine melo \
  --voice-profile calm-documentary \
  --video-effects subtle-motion \
  --include-intro \
  --include-outro \
  --channel-name IMAGINE \
  --intro-tagline "Explainers about AI systems and ideas" \
  --outro-tagline "Watch next" \
  --bookend-style minimal-clean \
  --caption-engine faster-whisper \
  --caption-style engagement \
  --subtitle-preset highlight-follow \
  --subtitle-position mid-safe \
  --subtitle-accent-color sunflower \
  --subtitle-bold \
  --subtitle-outline \
  --duration-tolerance 0.25 \
  --strict-commercial-safe \
  --verbose

Subtitle presets currently include `regular`, `highlight-follow`, and `highlight-box-follow`. The boxed preset now has separate highlight text and box background colors.

local-video-mvp run \
  --workflow-stage review \
  --prompt "Your topic" \
  --project-dir ./projects/demo

local-video-mvp run \
  --workflow-stage shot-plan \
  --prompt "Your topic" \
  --project-dir ./projects/demo

local-video-mvp prepare-shot-candidates \
  --project-dir ./projects/demo \
  --shot-id scene_001_shot_01 \
  --key-info "Updated shot direction"

local-video-mvp run \
  --workflow-stage preview \
  --prompt "Your topic" \
  --project-dir ./projects/demo

local-video-mvp run \
  --workflow-stage finalize \
  --prompt "Your topic" \
  --project-dir ./projects/demo

local-video-mvp inspect --project-dir ./projects/demo

local-video-mvp replace-clips \
  --project-dir ./projects/demo \
  --clip-names 03-why-lidar-fusion-matters 07-common-mistakes \
  --asset-keywords "autonomous vehicles, sensors, traffic"

trap - EXIT INT TERM
cleanup
deactivate
```

## Run artifacts and debugging

Important files in each project run:

- `run.log` - step-by-step execution logs
- `run_report.json` - status, timings, warnings, outputs, metrics
- `review/news_source_candidates.json` - news-mode source intake packet with candidate metadata, gate settings, and screenshot/source-card paths
- `review/news_review_state.json` - per-source approval decisions for the news gate
- `review/news_brief.json` - approved-source fact brief used for news script generation
- `review/script_approved.json` - approved script snapshot created by the `review` stage and used by preview/finalize
- `review/shot_plan.json` - deterministic 1-2 shot plan per scene
- `review/shot_review_state.json` - per-shot HITL approval state and block reasons
- `review/shots/<shot-id>/preview.mp4` - rendered mini preview used in shot review
- `review/clip_catalog.json` - human-readable clip names, chosen asset metadata, and stored ranked candidates for quick review
- `review/scene_review_state.json` - per-scene HITL approval state
- `review/narration_state.json` - narration hash metadata used to detect stale narration after script edits
- `review/captions_state.json` - caption input signature and cached caption stats
- `review/timeline_state.json` - timeline input signature
- `review/preview_render_state.json` - preview render signature used to fast-path finalize
- `review/preview.mp4` / `review/preview.srt` - preview artifacts before finalization
- `rights_manifest.json` - provenance, policy, and provider-usage snapshot
- `publish/youtube_description_credits.txt` - description-ready credits export derived from the rights manifest
- `timeline.json` - final clip structure

`draft` without `--prepare-scene-review` now stops after writing `script.json` and `narration.txt`. Narration audio, captions, and timeline assets are created during `preview`, `finalize`, or `draft --prepare-scene-review`.

If a run fails, start with:

1. `run_report.json`
2. `run.log`
3. ffmpeg error section in logs (usually rendering issues)

## Current project status and next major milestone

Current state: core MVP + subtitle/pacing/duration/effects/bookend improvements are in place.

Current active milestone:

- Human-in-the-loop workflow (`draft -> script review -> shot plan -> shot review -> preview -> finalize`) with per-shot mini previews and focused visual regeneration.
- Additive news workflow (`sources -> source review -> draft -> script review -> shot plan -> shot review -> preview -> finalize`) with U.S.-only editorial review artifacts.

## Contribution notes

- Keep commits small and focused.
- Avoid committing `.env` (already gitignored).
- Prefer local reproducibility and deterministic ffmpeg paths.
- Validate with a short smoke run after changes.
