# Local Video MVP

Local-first MVP for generating long-form faceless explainer videos on macOS.

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
- Optional for script generation: `ollama`
- Optional for local TTS: `melo-tts`
- Optional for caption quality pass: `faster-whisper`

Install `ffmpeg` via Homebrew:

```bash
brew install ffmpeg
```

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
  --project-dir ./projects/diffusion-v1 \
  --minutes 5 \
  --resolution 1280x720 \
  --script-engine ollama \
  --ollama-model qwen2.5:14b \
  --tts-engine melo \
  --video-effects subtle-motion \
  --voice-profile calm-documentary \
  --caption-engine faster-whisper \
  --caption-style engagement \
  --caption-font-scale 0.9 \
  --caption-bottom-ratio 0.055 \
  --burn-subtitles \
  --duration-tolerance 0.25 \
  --strict-commercial-safe
```

Duration control defaults to a +/-25% tolerance around requested minutes.
If generated narration is too short, the pipeline auto-expands script content and retries synthesis.

Voice pacing defaults to `calm-documentary`. You can switch later with:

- `--voice-profile balanced`
- `--voice-profile energetic-explainer`

Pause boundaries are automatically refined to avoid awkward breaks before tiny trailing words and after common function words.

Video effects presets (long-form safe):

- `--video-effects clean` (no extra effects)
- `--video-effects subtle-motion` (light motion and color polish)
- `--video-effects dynamic` (stronger motion/color treatment)

If you use `--script-engine ollama`, make sure Ollama server is running:

```bash
ollama serve
```

In non-strict mode, when Ollama is unavailable the pipeline falls back to a local template script and logs a warning.
To force a hard failure instead, add `--require-ollama`.

If MeloTTS is not available yet, you can run a local system fallback for development:

```bash
local-video-mvp run \
  --prompt "Explain transformers in plain english" \
  --project-dir ./projects/transformers-v1 \
  --tts-engine say \
  --allow-system-tts
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

## Inspect logs after a run

Every run writes:

- `run.log` (line-by-line stage logs)
- `run_report.json` (status, timings, warnings, outputs)
- `captions.ass` (styled subtitle file used for burn-in)

`run_report.json` also includes `caption_stats`, `duration_stats`, and `pacing_stats` for quick quality checks.

Inspect quickly:

```bash
local-video-mvp inspect --project-dir ./projects/diffusion-v1
```

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
