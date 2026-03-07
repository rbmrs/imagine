# Local Video MVP

Local-first MVP for generating long-form faceless explainer videos on macOS.

New contributors should start with `PROJECT_ONBOARDING.md` for a quick project overview and workflow guide.

This build targets your constraints:

- Long-form videos (default 5 minutes for MVP iteration)
- 720p output first
- Local render and local files only (`final.mp4`, `final.srt`, manifest)
- Strict commercial-safe mode in v1 by default

## What this MVP does

Pipeline stages:

1. Prompt -> script plan (`script.json`)
2. Script -> narration audio (`narration.wav`)
3. Narration/script -> captions (`captions.srt`)
4. Scene plan -> stock asset fetch (Pixabay/Pexels optional) + local cache
5. Timeline assembly (`timeline.json`)
6. Deterministic render with `ffmpeg` -> `output/final.mp4`
7. Rights/provenance manifest (`rights_manifest.json`)

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.9+
- `ffmpeg` and `ffprobe` in PATH
  - `drawtext` support is required for intro/outro text rendering.
  - `subtitles` support is required for burned subtitle output.
- Optional for script generation: `ollama`
- Optional for local TTS: `melo-tts`, `piper-tts`
- Optional for caption quality pass: `faster-whisper`

Install `ffmpeg` via Homebrew:

```bash
brew install ffmpeg
```

If your `ffmpeg` build is missing `drawtext` or `subtitles`, install a build that includes those filters before running the pipeline.

Create and install package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Optional extras:

```bash
python -m pip install -e '.[voice,captions]'
```

First-time MeloTTS data setup (required once):

```bash
python -m unidic download
python - <<'PY'
import nltk
nltk.download('averaged_perceptron_tagger_eng')
nltk.download('punkt')
nltk.download('cmudict')
PY
```

Notes for zsh:

- Quote extras exactly as `'[voice,captions]'` to avoid glob expansion errors.
- If you paste commands in interactive zsh, avoid raw `# comments` lines unless `setopt interactivecomments` is enabled.

## API keys (optional, for stock footage)

Set one or both:

```bash
export PEXELS_API_KEY="your_key"
export PIXABAY_API_KEY="your_key"
```

If no key is available, the renderer still works using generated placeholder visuals.

## Run

Single-terminal pattern (starts Ollama in background and auto-stops it after completion):

```bash
source .venv/bin/activate
set -a; source .env; set +a

OLLAMA_PID=""
if ! pgrep -f "ollama serve" >/dev/null; then
  ollama serve >/tmp/ollama.log 2>&1 &
  OLLAMA_PID=$!
  sleep 2
fi
trap '[ -n "$OLLAMA_PID" ] && kill "$OLLAMA_PID"' EXIT
```

Then run:

```bash
local-video-mvp run \
  --prompt "Explain how diffusion models work for beginners" \
  --asset-keywords "cars, roads" \
  --project-dir ./projects/diffusion-v1 \
  --minutes 5 \
  --resolution 1280x720 \
  --script-engine ollama \
  --ollama-model qwen2.5:14b \
  --tts-engine melo \
  --video-effects subtle-motion \
  --include-intro \
  --include-outro \
  --intro-seconds 2.8 \
  --outro-seconds 3.0 \
  --outro-text "Thanks for watching" \
  --bookend-style brand-image-motion \
  --brand-logo-path ./projects/brand-kit/logo-option-3-geometric.png \
  --brand-intro-image-path ./projects/brand-kit/channel-bg-intro.jpg \
  --brand-outro-image-path ./projects/brand-kit/channel-bg-outro.jpg \
  --no-brand-use-scene-fallback \
  --voice-profile calm-documentary \
  --caption-engine faster-whisper \
  --caption-style engagement \
  --caption-font-scale 0.9 \
  --caption-bottom-ratio 0.055 \
  --burn-subtitles \
  --duration-tolerance 0.25 \
  --strict-commercial-safe
```

For quick debug loops, add `--fast-mode`. This applies a cheaper profile:

- caps target duration to 1 minute
- lowers render resolution/fps
- switches captions to `heuristic`
- disables intro/outro cards while keeping burned subtitles enabled
- relaxes external asset requirement so placeholder paths can still complete

Duration control defaults to a +/-25% tolerance around requested minutes.
If generated narration is too short, the pipeline auto-expands script content and retries synthesis.

Voice pacing defaults to `calm-documentary`. You can switch later with:

- `--voice-profile balanced`
- `--voice-profile energetic-explainer`

Pause boundaries are automatically refined to avoid awkward breaks before tiny trailing words and after common function words.

Asset relevance control:

- `--asset-keywords "cars, roads"` constrains footage search to those terms
- when keywords are set, stock queries always include them
- scene clips are assigned uniquely per run (no repeated stock clip across scenes)

Video effects presets (long-form safe):

- `--video-effects clean` (no extra effects)
- `--video-effects subtle-motion` (light motion and color polish)
- `--video-effects dynamic` (stronger motion/color treatment)

Intro/outro options:

- `--include-intro` / `--no-include-intro`
- `--include-outro` / `--no-include-outro`
- `--intro-seconds 2.8`
- `--outro-seconds 3.0`
- `--outro-text "Thanks for watching"`
- `--bookend-style minimal-clean` (default), `--bookend-style cinematic-subtle`, or `--bookend-style brand-image-motion`
- `--brand-logo-path /path/to/logo.png` (supports png/jpg/webp; svg rasterized when possible)
- `--brand-intro-image-path /path/to/intro-bg.jpg`
- `--brand-outro-image-path /path/to/outro-bg.jpg`
- `--brand-use-scene-fallback` / `--no-brand-use-scene-fallback`

If you use `--script-engine ollama`, make sure Ollama server is running:

```bash
ollama serve
```

In non-strict mode, when Ollama is unavailable the pipeline falls back to a local template script and logs a warning.
To force a hard failure instead, add `--require-ollama`.

To run narration with Piper instead of Melo:

```bash
local-video-mvp run \
  --prompt "Explain transformers in plain english" \
  --project-dir ./projects/transformers-v1 \
  --tts-engine piper \
  --piper-voice-id en_US-john-medium
```

List available local Melo voices:

```bash
local-video-mvp voices --melo-language EN
```

Generate A/B voice samples from your existing project script:

```bash
local-video-mvp voice-ab \
  --project-dir ./projects/diffusion-v1 \
  --speakers EN-US EN-Default EN-AU \
  --sample-words 130 \
  --voice-profile calm-documentary
```

Outputs are written under `output/voice_ab/`, including:

- individual speaker WAV files
- `ab_compare.wav` (single file for quick back-to-back listening)
- `voice_ab_report.json`

## TUI quick start (experimental)

You can launch a terminal UI and trigger the onboarding run profile with a single key.

```bash
imagine
```

`imagine` opens the TUI with default values:

- prompt: `Autonomous cars`
- asset keywords: `cars, roads`
- minutes: `2`
- voice: `EN / EN-US`, profile `calm-documentary`, speed `1.0`

Storage behavior with TUI defaults:

- TUI auto-creates a run workspace under `~/.imagine/projects/<prompt-slug>-<timestamp>/`
- after a successful run, TUI exports only the final MP4 to `~/Downloads/<prompt-slug>-<timestamp>.mp4`

If you want custom startup values, you can still use:

```bash
local-video-mvp tui \
  --prompt "Your topic" \
  --asset-keywords "cars, roads" \
  --project-dir ~/.imagine/projects \
  --minutes 5 \
  --melo-language EN \
  --melo-speaker EN-US \
  --voice-profile calm-documentary \
  --voice-speed 1.0
```

Key bindings:

- `R`: run/advance HITL flow (`draft (review-ready) -> scene review -> preview -> finalize`) for the active workspace
- `S`: open settings (`HITL` On/Off and `Fast mode` On/Off for quicker debug runs)
- `E`: edit parameters (`prompt`, `asset keywords`, `minutes`, `voice speed`) and choose from lists for language/speaker/profile
- `D`: open debug menu (terminal video playback test + unified `Test voices` list with Melo and Piper entries)
- `C`: clean old auto-managed workspaces (mark with Space, delete with Enter)
- `Q`: quit (stops active run first)

The TUI uses checkpoint modals between stages. After draft, it opens a scene review hub (pick scenes with Up/Down + Enter) and then a per-scene editor where you can:

- edit/approve scene voiceover text directly,
- preview narration (scene segment or regenerated scene sample),
- preview scene clip (in-terminal when supported),
- replace a rejected clip with same or expanded keywords.

Preview is prompted only after all scene checkpoints are approved. After preview rendering, TUI opens a Preview Actions modal so you can play `review/preview.mp4` in terminal before finalizing.

From the scene review hub, press `G` to auto-approve all remaining scenes and generate preview immediately.

Install `mpv` for in-terminal scene playback:

```bash
brew install mpv
```

Ghostty works with the kitty graphics path (`mpv --vo=kitty`) on compatible setups. Current Preview Actions playback is terminal-only and reports an in-terminal backend error if unsupported.

Current MVP voice picker scope:

- language picker supports `EN` only for now
- Melo speaker picker is loaded from available EN speakers on your machine
- Piper picker uses curated built-in voices (same catalog used by Debug -> Test voices)

If Ollama is not already running, the TUI tries to start `ollama serve` automatically and stops it on exit when it was started by the TUI.

Stock asset keys are auto-discovered by TUI (in this order):

1. current process env (`PEXELS_API_KEY`, `PIXABAY_API_KEY`)
2. repo `.env`
3. `~/.config/imagine/stock_api_keys.json` (or `IMAGINE_STOCK_KEYS_FILE`)

Example key file:

```json
{
  "PEXELS_API_KEY": "your_pexels_key",
  "PIXABAY_API_KEY": "your_pixabay_key"
}
```

TUI enforces a hard guard against placeholders:

- if no stock keys are found, `R` is blocked before the run starts
- if any scene still resolves to a placeholder, the run is rejected
- if unique clips run out, the run pauses before render and prompts you to broaden asset keywords

For CLI runs outside TUI, you can enforce the same rule with:

```bash
local-video-mvp run --require-external-assets ...
```

TUI logs are written to a standard location:

- `~/.imagine/logs/latest.log` (pointer to latest session)
- `~/.imagine/logs/tui-YYYYMMDD-HHMMSS.log` (session file)

You can override the log directory with `IMAGINE_TUI_LOG_DIR`.

## Inspect logs after a run

Every run writes:

- `run.log` (line-by-line stage logs)
- `run_report.json` (status, timings, warnings, outputs)
- `captions.ass` (styled subtitle file used for burn-in)
- `review/clip_catalog.json` (human-readable clip names + source metadata for review)

`run_report.json` also includes `caption_stats`, `duration_stats`, and `pacing_stats` for quick quality checks.

Inspect quickly:

```bash
local-video-mvp inspect --project-dir ./projects/diffusion-v1
```

Replace specific clips by name (without regenerating narration/script):

```bash
local-video-mvp replace-clips \
  --project-dir ./projects/diffusion-v1 \
  --clip-names 03-why-lidar-fusion-matters 07-common-mistakes \
  --asset-keywords "autonomous vehicles, sensors, traffic"
```

Run staged workflow from CLI (same `run` command, different stage):

```bash
# 1) draft (script + narration + review-ready clip catalog/timeline)
local-video-mvp run --workflow-stage draft --prepare-scene-review --prompt "Your topic" --project-dir ./projects/demo

# 2) review (optional: approve current script.json, or pass reviewed JSON)
local-video-mvp run --workflow-stage review --prompt "Your topic" --project-dir ./projects/demo

# 3) preview (renders review/preview.mp4)
local-video-mvp run --workflow-stage preview --prompt "Your topic" --project-dir ./projects/demo

# 4) finalize (promotes preview to output/final.mp4 when inputs are unchanged; otherwise re-renders)
local-video-mvp run --workflow-stage finalize --prompt "Your topic" --project-dir ./projects/demo
```

TUI uses the same stage primitives but follows: `draft --prepare-scene-review -> scene review hub -> preview -> finalize`.

If stock API keys are missing, the report warns that placeholder visuals were used.

## Output structure

```txt
projects/<project>/
  prompt.txt
  script.json
  narration.txt
  narration.raw.wav
  narration.wav
  captions.srt
  captions.ass
  timeline.json
  review/
    script_approved.json
    clip_catalog.json
    scene_review_state.json
    narration_state.json
    captions_state.json
    timeline_state.json
    preview_render_state.json
    preview.mp4
    preview.srt
  rights_manifest.json
  assets/
    cache/
  output/
    final.mp4
    final.srt
```

## Commercial-safe notes

- Strict mode is enabled by default and intended to avoid risky defaults.
- Rights metadata is recorded for downloaded assets when available.
- You should still review platform/model licenses before distribution.
- Keep `ffmpeg` distribution/license requirements in mind for shipping apps.

## Current scope and next steps

This is a CLI-first MVP. Suggested next milestones:

1. Add local queue worker and resumable jobs
2. Add SwiftUI wrapper app
3. Upgrade caption pass to `faster-whisper` by default
4. Improve scene-aware pacing and transitions
