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
- MeloTTS and Piper TTS
- Piper voice previews in TUI debug (for local voice quality evaluation)
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
- Subtitle style controls (size/position/timing)

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
- Bookend style presets (`minimal-clean`, `cinematic-subtle`, `brand-image-motion`)
- Brand-driven bookends support logo + custom intro/outro background images
- Branded bookends now support `channel_name`, `intro_tagline`, and `outro_tagline`
- `brand-image-motion` uses a logo-led asymmetric layout instead of a centered title card
- Intro/outro title wrapping + safer text rendering for punctuation-heavy titles
- Fast/debug mode for shorter lower-cost validation runs
- Default runs auto-use `projects/brand-kit/` assets for branded bookends when that kit is present

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

Generate voice A/B samples:

```bash
local-video-mvp voice-ab --project-dir ./projects/<project-id> --speakers EN-US EN-Default EN-AU
```

Launch the terminal UI (experimental):

```bash
imagine
```

`imagine` opens the TUI with default startup values. Use `local-video-mvp tui ...` for custom startup values.

Default startup values:

- prompt: `Autonomous cars`
- asset keywords: `cars, roads`
- minutes: `2`
- voice: `EN / EN-US`, profile `calm-documentary`, speed `1.0`

Default storage behavior:

- TUI auto-creates run workspaces under `~/.imagine/projects/<prompt-slug>-<timestamp>/`
- TUI exports final video to `~/Downloads/<prompt-slug>-<timestamp>.mp4`
- intro/outro default to branded bookends when `projects/brand-kit/` is present; otherwise the configured card style is used

TUI shortcut focus for now:

- `R` runs/continues staged HITL flow for current workspace (`draft (review-ready) -> scene review -> preview -> finalize`).
- `S` opens settings (`HITL` On/Off and `Fast mode` On/Off).
- `E` edits prompt/asset-keywords/duration/speed and uses list pickers for language/speaker/profile.
- `D` opens debug menu (terminal preview playback test + unified `Test voices` list with `[Melo]` and `[Piper]` entries).
- `C` opens workspace cleanup, where you mark folders and press Enter to delete.
- `Q` exits the TUI.

The TUI persists the last values changed via `Edit` plus settings toggles in `~/.imagine/tui_settings.json`. Startup values from CLI/defaults are used only when the persisted file is absent or contains invalid fields.

Checkpoint modals appear between stages so you can approve/review before continuing. After draft, TUI opens a scene review hub (Up/Down + Enter), then scene-by-scene HITL review (text -> narration -> clip), including clip replacement with same/new keywords before preview/finalize. Once preview is rendered, TUI offers Preview Actions (play preview in terminal, finalize now, or back). Finalize now reuses the approved preview render when inputs are unchanged; otherwise it re-renders.

Fast mode is intended for cheap validation passes. It caps runs to roughly 1 minute, lowers render cost, switches captions to heuristic timing, keeps burned subtitles enabled, uses shorter intro/outro cards, and allows placeholder assets so pipeline plumbing can be checked quickly.

Scene review hub shortcut: press `G` to auto-approve remaining scenes and immediately generate preview.

For best in-terminal preview support, install `mpv`:

```bash
brew install mpv
```

Ghostty is supported through the kitty graphics path when available. Current Preview Actions playback is terminal-only and surfaces backend errors when unsupported.

For Piper voice testing in debug mode, install runtime deps in the same environment used by TUI:

```bash
python -m pip install piper-tts
python -m pip install pathvalidate
```

Current Piper debug catalog includes commercial-friendlier EN-US options (including high-tier LibriTTS/LJSpeech entries) for rapid auditioning.

Current voice picker scope:

- language picker is restricted to `EN` for now
- speaker picker shows available EN speakers on this machine

TUI stock key discovery order:

1. process env (`PEXELS_API_KEY`, `PIXABAY_API_KEY`)
2. repo `.env`
3. `~/.config/imagine/stock_api_keys.json` (or `IMAGINE_STOCK_KEYS_FILE`)

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
  --prepare-scene-review \
  --prompt "Your topic" \
  --asset-keywords "cars, roads" \
  --project-dir ./projects/demo \
  --minutes 5 \
  --resolution 1280x720 \
  --script-engine ollama \
  --ollama-model qwen2.5:14b \
  --require-ollama \
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
  --duration-tolerance 0.25 \
  --strict-commercial-safe \
  --verbose

local-video-mvp run \
  --workflow-stage review \
  --prompt "Your topic" \
  --project-dir ./projects/demo

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
- `review/script_approved.json` - approved script snapshot used by preview/finalize stages
- `review/clip_catalog.json` - human-readable clip names + source metadata for quick review
- `review/scene_review_state.json` - per-scene HITL approval state
- `review/narration_state.json` - narration hash metadata used to detect stale narration after script edits
- `review/captions_state.json` - caption input signature and cached caption stats
- `review/timeline_state.json` - timeline input signature
- `review/preview_render_state.json` - preview render signature used to fast-path finalize
- `review/preview.mp4` / `review/preview.srt` - preview artifacts before finalization
- `rights_manifest.json` - provenance and config snapshot
- `timeline.json` - final clip structure

If a run fails, start with:

1. `run_report.json`
2. `run.log`
3. ffmpeg error section in logs (usually rendering issues)

## Current project status and next major milestone

Current state: core MVP + subtitle/pacing/duration/effects/bookend improvements are in place.

Current active milestone:

- Human-in-the-loop workflow (`draft -> scene review -> preview -> finalize`) with floating checkpoint modals and clip replacement loop before finalize.

## Contribution notes

- Keep commits small and focused.
- Avoid committing `.env` (already gitignored).
- Prefer local reproducibility and deterministic ffmpeg paths.
- Validate with a short smoke run after changes.
