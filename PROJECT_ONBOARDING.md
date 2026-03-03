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
- MeloTTS (primary) / macOS `say` (fallback, gated)
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
- Bookend style presets (`minimal-clean`, `cinematic-subtle`)
- Intro/outro title wrapping + safer text rendering for punctuation-heavy titles

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

TUI shortcut focus for now:

- `R` runs the preferred full pipeline profile from this onboarding guide.
- `I` runs `inspect` for the selected project.
- `P`/`D`/`M` edit prompt, project dir, and minutes.

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
  --prompt "Your topic" \
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
  --bookend-style minimal-clean \
  --caption-engine faster-whisper \
  --caption-style engagement \
  --duration-tolerance 0.25 \
  --strict-commercial-safe \
  --verbose

local-video-mvp inspect --project-dir ./projects/demo

trap - EXIT INT TERM
cleanup
deactivate
```

## Run artifacts and debugging

Important files in each project run:

- `run.log` - step-by-step execution logs
- `run_report.json` - status, timings, warnings, outputs, metrics
- `rights_manifest.json` - provenance and config snapshot
- `timeline.json` - final clip structure

If a run fails, start with:

1. `run_report.json`
2. `run.log`
3. ffmpeg error section in logs (usually rendering issues)

## Current project status and next major milestone

Current state: core MVP + subtitle/pacing/duration/effects/bookend improvements are in place.

Next planned milestone:

- Human-in-the-loop workflow (`draft -> review -> preview -> finalize`) so users can edit script/pacing before final render.

## Contribution notes

- Keep commits small and focused.
- Avoid committing `.env` (already gitignored).
- Prefer local reproducibility and deterministic ffmpeg paths.
- Validate with a short smoke run after changes.
