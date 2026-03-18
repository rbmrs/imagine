# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`local-video-mvp` is a local-first CLI pipeline for generating long-form faceless explainer videos on macOS. The project generates commercial-safe videos from text prompts, handling script generation, narration synthesis, asset acquisition, and final video rendering entirely locally.

**Primary user surface:** Terminal UI (`imagine` command)
**Secondary surface:** CLI (`local-video-mvp` command)

New features should support TUI first or alongside CLI. CLI-only implementations are considered incomplete unless explicitly scoped that way.

## Essential Commands

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
python -m pip install -e '.[voice,captions]'
```

### Running the Pipeline

**TUI (primary):**
```bash
imagine
```

**CLI full run:**
```bash
local-video-mvp run \
  --prompt "Your topic" \
  --project-dir ./projects/demo \
  --minutes 5 \
  --script-engine ollama \
  --ollama-model qwen2.5:14b \
  --tts-engine melo \
  --caption-engine faster-whisper
```

**Staged workflow (draft → review → shot-plan → preview → finalize):**
```bash
# Draft only (script.json + narration.txt)
local-video-mvp run --workflow-stage draft --prompt "Topic" --project-dir ./projects/demo

# Draft with full asset/narration/caption prep for review
local-video-mvp run --workflow-stage draft --prepare-scene-review --prompt "Topic" --project-dir ./projects/demo

# Approve script
local-video-mvp run --workflow-stage review --prompt "Topic" --project-dir ./projects/demo

# Plan shots (1-2 per scene)
local-video-mvp run --workflow-stage shot-plan --prompt "Topic" --project-dir ./projects/demo

# Render preview
local-video-mvp run --workflow-stage preview --prompt "Topic" --project-dir ./projects/demo

# Finalize (promotes preview or re-renders)
local-video-mvp run --workflow-stage finalize --prompt "Topic" --project-dir ./projects/demo
```

**News mode workflow (sources → source-review → draft → review → shot-plan → preview → finalize):**
```bash
local-video-mvp run \
  --workflow-stage sources \
  --content-mode news \
  --prompt "Topic" \
  --news-feed-url https://rss.nytimes.com/services/xml/rss/nyt/World.xml \
  --project-dir ./projects/news-demo
```

### Inspection and Utilities
```bash
# Inspect run outputs and metrics
local-video-mvp inspect --project-dir ./projects/demo

# List available voices
local-video-mvp voices --melo-language EN
local-video-mvp voices --tts-engine kokoro --kokoro-lang-code en-us

# Generate voice A/B comparison samples
local-video-mvp voice-ab \
  --project-dir ./projects/demo \
  --speakers EN-US EN-Default EN-AU

# Replace specific clips without regenerating narration
local-video-mvp replace-clips \
  --project-dir ./projects/demo \
  --clip-names scene-01 scene-03 \
  --asset-keywords "new, keywords"

# Refresh shot candidates before regeneration
local-video-mvp prepare-shot-candidates \
  --project-dir ./projects/demo \
  --shot-id scene_001_shot_01 \
  --key-info "Updated direction"

# YouTube auth and publishing
local-video-mvp youtube-auth
local-video-mvp youtube-auth --link
local-video-mvp youtube-draft --project-dir ./projects/demo
local-video-mvp youtube-publish --project-dir ./projects/demo
```

### Testing
```bash
# Currently only one test file exists
python -m pytest -q tests/test_news_mode.py
python -m pytest -q tests/test_news_mode.py::test_specific_case
python -m pytest -q -k "keyword"
```

### Recommended Local Run Pattern
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

# Run pipeline commands here

trap - EXIT INT TERM
cleanup
deactivate
```

## Architecture Overview

### Core Components

**Entry points:**
- `src/local_video_mvp/cli.py` - CLI command definitions, argument parsing, input validation
- `src/local_video_mvp/tui.py` - Terminal UI (curses-based), user workflow orchestration

**Data layer:**
- `src/local_video_mvp/models.py` - Data contracts (PipelineConfig, ScriptPlan, Scene, TimelineClip, etc.)

**Pipeline orchestration:**
- `src/local_video_mvp/pipeline.py` - VideoPipeline class containing all generation stages:
  - Script generation (Ollama or template)
  - Duration enforcement (expand/compress)
  - Narration synthesis (MeloTTS, Kokoro, Piper)
  - Asset resolution (Pexels, Pixabay, Coverr, Vecteezy)
  - Caption generation (faster-whisper or heuristic)
  - Timeline assembly (intro/content/outro)
  - Video rendering (ffmpeg)
  - Rights manifest generation

**YouTube integration:**
- `src/local_video_mvp/youtube.py` - OAuth flow, draft management, thumbnail generation, publishing

### Pipeline Flow

```
1. Generate script (JSON with scenes)
2. Enforce duration constraints (expand/compress if needed)
3. Synthesize narration audio (chunked TTS with pacing)
4. Resolve visual assets (stock footage or images)
5. Generate captions (.srt + styled .ass)
6. Build timeline (intro + content + outro)
7. Render video with ffmpeg
8. Write rights manifest and run report
```

### Human-in-the-Loop (HITL) Workflow

Standard: `draft → script review → shot-plan → shot review → preview → finalize`
News mode: `sources → source review → draft → script review → shot-plan → shot review → preview → finalize`

**Key review artifacts:**
- `review/script_approved.json` - Approved script snapshot
- `review/shot_plan.json` - Planned 1-2 shots per scene
- `review/shot_review_state.json` - Per-shot approval/block state
- `review/shots/<shot-id>/preview.mp4` - Per-shot mini previews
- `review/news_source_candidates.json` - News source intake (news mode only)
- `review/news_review_state.json` - Per-source approval decisions (news mode only)

### Output Structure

```
projects/<project>/
  prompt.txt
  script.json                           # Generated script plan
  narration.txt                         # Full narration text
  narration.raw.wav                     # Unprocessed narration audio
  narration.wav                         # Processed narration audio
  captions.srt                          # Standard subtitle format
  captions.ass                          # Styled subtitles for burn-in
  timeline.json                         # Final clip structure
  review/
    news_source_candidates.json         # News intake packet
    news_review_state.json              # Editorial approvals
    news_brief.json                     # Approved-source facts
    script_approved.json                # Approved script snapshot
    shot_plan.json                      # Shot-level plan
    shot_review_state.json              # Shot approval state
    clip_catalog.json                   # Asset metadata + candidates
    preview.mp4                         # Preview render
    shots/
      <shot-id>/
        preview.mp4                     # Per-shot preview
  rights_manifest.json                  # Provenance and policy
  assets/cache/                         # Downloaded stock assets
  output/
    final.mp4                           # Final rendered video
    final.srt                           # Final subtitles
    thumbnail_yt.jpg                    # Auto-generated thumbnail
  publish/
    youtube_description_credits.txt     # Credits for YouTube
  run.log                               # Stage-by-stage execution log
  run_report.json                       # Status, timings, metrics
```

### Key Design Patterns

**Deterministic rendering:** All inputs are hashed and tracked to enable preview reuse during finalize when inputs haven't changed.

**Strict commercial-safe defaults:** All TTS engines, stock providers, and asset selections are filtered through commercial safety policies by default. Attribution-required sources are flagged and tracked in rights manifests.

**Fallback transparency:** When API keys are missing or assets fail, placeholders may be used in fast mode, but production runs (TUI) enforce external asset requirements and block on missing resources.

**Duration control:** Scripts are expanded or compressed during preflight to meet target duration with configurable tolerance (default ±25%).

**Pause refinement:** TTS pause boundaries are automatically adjusted to avoid awkward breaks before trailing words or after function words.

**Shot-level review:** Each scene is broken into 1-2 shots with independent visual selection, allowing focused regeneration without re-rendering narration.

## Important Conventions

### Code Style
- Use `from __future__ import annotations` at top of files
- Type annotate public and non-trivial functions
- Prefer built-in generics: `list[str]`, `dict[str, Any]`, `X | None`
- Use 4-space indentation, double quotes, f-strings
- Naming: `snake_case` for functions/vars, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants
- CLI flags use `kebab-case`

### Path Handling
- Use `pathlib.Path` consistently
- Normalize with `.expanduser().resolve()`
- Validate paths at CLI boundary

### Error Handling
- Validate user inputs at CLI boundary
- Raise actionable errors: `RuntimeError`, `ValueError` with clear context
- Capture and surface subprocess stderr on failure

### Subprocess Management
- Time-bound all subprocess calls
- Check return codes explicitly
- Log both stdout and stderr for debugging

## Critical Invariants

### Must Preserve
- Output artifact paths (`output/final.mp4`, `output/final.srt`, `script.json`, `timeline.json`, `rights_manifest.json`, `run_report.json`, `run.log`)
- run_report.json schema keys (add only, don't remove without migration)
- Strict commercial-safe defaults
- UTF-8 text encoding
- Deterministic JSON structure

### Must Not
- Commit `.env`, secrets, credentials, large binaries
- Silently relax strict-safe behavior
- Rename/remove core output files without explicit request
- Use destructive git operations unless requested
- Introduce unrelated refactors in feature/fix tasks

## Validation Strategy

For behavior-affecting changes, run at least one validation:
- Targeted command check for CLI changes
- Smoke run through affected pipeline stages
- Artifact/report integrity check

**Minimum validation for pipeline edits:**
```bash
# 1. Run a short generation
local-video-mvp run --workflow-stage draft --prompt "Test" --project-dir ./projects/test --minutes 1 --fast-mode

# 2. Inspect outputs
local-video-mvp inspect --project-dir ./projects/test

# 3. Confirm expected files exist
ls -la ./projects/test/{script.json,narration.txt,run_report.json,run.log}
```

## Debugging Failed Runs

Check in this order:
1. `run_report.json` (status, failing stage, warnings)
2. `run.log` (first error and stage context)
3. ffmpeg/ffprobe stderr details

Then:
1. Classify failing domain (deps/input/script/tts/captions/assets/render)
2. Reproduce minimally
3. Patch smallest root cause
4. Re-run targeted validation

## TUI State Management

**Persisted settings:** `~/.imagine/tui_settings.json`
- Reused on next launch unless missing or invalid
- Updated through Prompt menu, Settings, and Run preflight

**Auto-managed workspaces:** `~/.imagine/projects/<prompt-slug>-<timestamp>/`

**Logs:** `~/.imagine/logs/tui-YYYYMMDD-HHMMSS.log` (symlinked as `latest.log`)

**Stock API keys discovery:**
1. Process environment variables
2. Repo `.env` file
3. `~/.config/imagine/stock_api_keys.json` (or `$IMAGINE_STOCK_KEYS_FILE`)

## External Dependencies

**Required in PATH:**
- `ffmpeg` (with `drawtext` and `subtitles` filters)
- `ffprobe`

**Optional services:**
- `ollama serve` (for script generation; TUI auto-starts if needed)
- MeloTTS, Kokoro, Piper (for narration; install via `pip install -e '.[voice]'`)
- faster-whisper (for caption quality pass; install via `pip install -e '.[captions]'`)
- `mpv` (for terminal video playback in TUI)

**Stock asset providers (API keys required for production runs):**
- Pexels (primary)
- Pixabay (primary)
- Coverr (experimental fallback, video-only)
- Vecteezy (experimental fallback, video + images)

## Additional Documentation

- **README.md** - Setup, installation, command reference, feature overview
- **PROJECT_ONBOARDING.md** - Quick-start guide for new contributors, implementation status
- **AGENTS.md** - Strict operating rules for autonomous coding agents (instruction precedence, invariants, contribution guidance)
