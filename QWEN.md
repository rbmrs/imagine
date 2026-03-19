# Local Video MVP - QWEN Context Guide

## Project Overview

**Local Video MVP** (`local-video-mvp`) is a local-first Python CLI application for generating long-form faceless explainer videos on macOS. The project prioritizes commercial-safe defaults, local rendering, and deterministic output generation.

### Core Purpose
- Generate explainer videos from text prompts (default 5 minutes, 720p)
- Local-first pipeline: all rendering happens on-device using `ffmpeg`
- Strict commercial-safe licensing by default
- Human-in-the-loop workflow with review checkpoints

### Architecture
- **Package layout**: `src/local_video_mvp/` with setuptools
- **CLI entry points**: `local-video-mvp` (CLI) and `imagine` (TUI)
- **Pipeline stages**: script → narration → captions → assets → timeline → render → manifest

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.9+ |
| CLI Framework | Custom argparse-based CLI |
| TUI | Textual/ Rich-based terminal UI |
| Script Generation | Ollama (qwen2.5:14b default) or template |
| TTS Engines | MeloTTS, Kokoro, Piper |
| Captions | faster-whisper or heuristic |
| Video Rendering | ffmpeg (with drawtext, subtitles filters) |
| Asset Sources | Pexels, Pixabay (primary); Coverr, Vecteezy (experimental) |

## Repository Structure

```
/Users/rafaelbm/Dev/AI/Imagine/
├── src/local_video_mvp/
│   ├── __init__.py          # Version: 0.1.0
│   ├── cli.py               # CLI commands and TUI entry point
│   ├── models.py            # Dataclasses: PipelineConfig, ScriptPlan, Scene, etc.
│   ├── pipeline.py          # VideoPipeline class: stage orchestration
│   ├── tui.py               # Terminal UI implementation
│   ├── youtube.py           # YouTube OAuth and publish flow
│   └── trends.py            # Trending topics integration
├── tests/
│   └── test_news_mode.py    # News workflow tests
├── projects/                # Generated project runs (git-ignored)
├── docs/                    # Documentation
├── pyproject.toml           # Package configuration
├── README.md                # Setup and usage guide
├── PROJECT_ONBOARDING.md    # Engineer onboarding
├── AGENTS.md                # Autonomous agent operating rules
└── .gitignore
```

## Building and Running

### Setup
```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install package
python -m pip install -U pip
python -m pip install -e .

# Optional: voice and caption support
python -m pip install -e '.[voice,captions]'
```

### System Dependencies
- **Required**: `ffmpeg`, `ffprobe` (with `drawtext` and `subtitles` filters)
- **Optional**: `ollama`, `mpv` (terminal playback), `playwright` (news screenshots)

```bash
brew install ffmpeg mpv
python -m playwright install chromium
```

### Main CLI Commands
```bash
# Full pipeline run
local-video-mvp run --prompt "Your topic" --project-dir ./projects/demo

# Inspect a project
local-video-mvp inspect --project-dir ./projects/<project-id>

# Voice utilities
local-video-mvp voices --melo-language EN
local-video-mvp voice-ab --project-dir ./projects/<id> --speakers EN-US EN-AU

# YouTube integration
local-video-mvp youtube-auth
local-video-mvp youtube-draft --project-dir ./projects/<id>

# Launch TUI
imagine
```

### Workflow Stages (HITL)
```bash
# 1. Draft (script + narration plan)
local-video-mvp run --workflow-stage draft --prompt "Topic" --project-dir ./projects/demo

# 2. Review (approve/edit script)
local-video-mvp run --workflow-stage review --project-dir ./projects/demo

# 3. Shot plan (visual asset planning)
local-video-mvp run --workflow-stage shot-plan --project-dir ./projects/demo

# 4. Preview (render review/preview.mp4)
local-video-mvp run --workflow-stage preview --project-dir ./projects/demo

# 5. Finalize (promote to output/final.mp4)
local-video-mvp run --workflow-stage finalize --project-dir ./projects/demo
```

### News Workflow (Editorial Mode)
```bash
# Sources → Source Review → Draft → Script Review → Shot Plan → Shot Review → Preview → Finalize
local-video-mvp run \
  --workflow-stage sources \
  --content-mode news \
  --prompt "Iran conflict update" \
  --news-feed-url https://rss.nytimes.com/services/xml/rss/nyt/World.xml \
  --project-dir ./projects/iran-news
```

## Output Contract

Each project run produces:
```
projects/<project-id>/
├── script.json                 # Scene-by-scene script plan
├── timeline.json               # Clip timeline structure
├── rights_manifest.json        # Provenance and licensing metadata
├── run_report.json             # Status, timings, warnings, metrics
├── run.log                     # Step-by-step execution log
├── output/
│   ├── final.mp4               # Final rendered video
│   └── final.srt               # Subtitle file
├── review/                     # HITL review artifacts
│   ├── script_approved.json
│   ├── shot_plan.json
│   ├── shot_review_state.json
│   ├── clip_catalog.json
│   └── shots/<shot-id>/preview.mp4
└── publish/
    └── youtube_description_credits.txt
```

## Development Conventions

### Code Style
- **Imports**: `from __future__ import annotations` at top; group stdlib → third-party → local
- **Indentation**: 4 spaces, double quotes, f-strings
- **Naming**: `snake_case` (functions/vars), `PascalCase` (classes), `UPPER_SNAKE_CASE` (constants)
- **Types**: Built-in generics (`list[str]`, `dict[str, Any]`), `X | None` for optionals
- **Annotation**: Type annotate public and non-trivial internal functions

### Linting and Testing
```bash
# Recommended checks (no pinned configs yet)
python -m ruff check src
python -m ruff format --check src
python -m mypy src
python -m black --check src

# Test execution
python -m pytest -q
python -m pytest -q tests/test_news_mode.py
python -m pytest -q -k "duration"
```

### Git Practices
- Small, focused commits with logical grouping
- Never commit `.env`, secrets, credentials, or large binaries
- Prefer reversible commits for experimental work
- Keep docs synchronized with CLI/runtime behavior

## Key Configuration Points

### API Keys (Optional - for stock footage)
```bash
export PEXELS_API_KEY="your_key"
export PIXABAY_API_KEY="your_key"
export COVERR_APP_ID="your_coverr_app_id"
export COVERR_API_KEY="your_coverr_api_key"
export VECTEEZY_ACCOUNT_ID="your_vecteezy_account_id"
export VECTEEZY_API_KEY="your_vecteezy_api_key"
```

Key discovery order (TUI):
1. Process environment variables
2. Repo `.env` file
3. `~/.config/imagine/stock_api_keys.json`

### TUI Defaults
- Prompt: `Autonomous cars`
- Resolution: `1280x720`
- Minutes: `2`
- Voice: `EN / EN-US`, profile `calm-documentary`, speed `1.0`
- Intro/Outro style: `corner-fade`
- Asset keywords: `cars, roads`

TUI persists settings to `~/.imagine/tui_settings.json`.

### TUI Key Bindings
| Key | Action |
|-----|--------|
| `R` | Run/advance HITL flow (draft → finalize) |
| `Y` | YouTube publish flow |
| `P` | Prompt menu (brief, mode, profile, keywords, sources) |
| `S` | Settings (resolution, voice, subtitles, assets) |
| `D` | Debug menu (playback test, thumbnail preview) |
| `C` | Clean old workspaces |
| `Q` | Quit |

## Invariants and Constraints

### MUST
- Preserve strict commercial-safe defaults unless explicitly changed
- Preserve artifact paths and report schema keys
- Keep subprocess calls time-bounded and return-code checked
- Keep text/JSON writes UTF-8 and deterministic
- Run validation steps for non-trivial changes

### MUST NOT
- Commit `.env`, secrets, or credentials
- Silently relax strict-safe behavior
- Rename/remove core output files without explicit request
- Use destructive git operations unless explicitly requested
- Introduce unrelated refactors in feature/fix tasks

## Failure Triage Flow

1. Check `run_report.json` (status, failing stage, warnings)
2. Check `run.log` (first error and stage context)
3. Check ffmpeg/ffprobe stderr details
4. Classify domain: deps/input/script/tts/captions/assets/render/report
5. Reproduce minimally
6. Patch smallest root cause
7. Re-run targeted validation

## Current Project Status

### Implemented
- Baseline MVP pipeline and CLI
- Subtitle chunking + burn-in with presentation presets
- Duration tolerance with auto expand/compress
- Voice pacing refinements + pause-boundary improvements
- Voice inventory and A/B preview
- Visual effects presets (`clean`, `subtle-motion`, `dynamic`)
- Intro/outro bookends with style presets
- Human-in-the-loop workflow with shot-level mini previews
- News workflow with editorial source review
- YouTube OAuth integration and publish flow
- TUI with checkpoint modals and settings persistence

### Next Milestone
- Continue refining HITL flow and TUI polish
- Expand visual asset strategies for news mode
- Improve shot regeneration and keyword refinement

## Additional Notes

- **Instruction precedence**: User task → AGENTS.md → README.md → PROJECT_ONBOARDING.md
- **TUI priority**: Prefer TUI support for user-facing features alongside CLI
- **Validation**: Minimum practical validation = short generation + inspect + confirm outputs
- **Ollama**: TUI auto-starts `ollama serve` and stops on exit when TUI-managed
- **Stock assets**: Hard guard against placeholders—runs blocked if no keys or if any scene resolves to placeholder
