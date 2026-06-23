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
4. Scene plan -> stock asset fetch (Pexels/Pixabay primary search, optional Coverr/Vecteezy experimental fallbacks) + local cache
5. Timeline assembly (`timeline.json`)
6. Deterministic render with `ffmpeg` -> `output/final.mp4`
7. Rights/provenance manifest (`rights_manifest.json`)
8. YouTube credits export (`publish/youtube_description_credits.txt`, additive)

In `news` mode, the pipeline inserts a `sources` stage before script generation. That stage ingests RSS/Atom feeds, extracts article text, prepares source screenshots/source cards under `review/`, and blocks `draft`, `preview`, and `finalize` until at least 2 approved editorial sources from 2 distinct domains are recorded.

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.9+
- `ffmpeg` and `ffprobe` in PATH
  - `drawtext` support is required for intro/outro text rendering and news source overlays.
  - `subtitles` support is required for burned subtitle output.
- Optional for script generation: `ollama`
- Optional for local TTS: `melo-tts`, `piper-tts`, `kokoro`
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

For news screenshot capture, install a Playwright browser once:

```bash
python -m playwright install chromium
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

Kokoro notes:

- `python -m pip install -e '.[voice]'` now includes Kokoro.
- the first English Kokoro run may download the upstream model, voice pack, and spaCy model once before synthesis starts.

Notes for zsh:

- Quote extras exactly as `'[voice,captions]'` to avoid glob expansion errors.
- If you paste commands in interactive zsh, avoid raw `# comments` lines unless `setopt interactivecomments` is enabled.

## API keys (optional, for stock footage)

Set any provider you plan to use:

```bash
export PEXELS_API_KEY="your_key"
export PIXABAY_API_KEY="your_key"
export COVERR_API_KEY="your_key"
export COVERR_APP_ID="your_coverr_app_id"
export VECTEEZY_ACCOUNT_ID="your_vecteezy_account_id"
export VECTEEZY_API_KEY="your_vecteezy_api_key"
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
  --script-tone curiosity-driven \
  --target-audience "curious general audience" \
  --hook-style surprising-fact \
  --narrative-mode story-led \
  --example-density balanced \
  --tts-engine melo \
  --video-effects subtle-motion \
  --include-intro \
  --include-outro \
  --intro-seconds 2.8 \
  --outro-seconds 3.0 \
  --outro-text "Thanks for watching" \
  --channel-name IMAGINE \
  --intro-tagline "Explainers about AI systems and ideas" \
  --outro-tagline "Watch next" \
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
  --subtitle-preset highlight-follow \
  --subtitle-position mid-safe \
  --subtitle-accent-color sunflower \
  --subtitle-bold \
  --subtitle-outline \
  --duration-tolerance 0.25 \
  --strict-commercial-safe
```

News workflow example:

```bash
local-video-mvp run \
  --workflow-stage sources \
  --content-mode news \
  --prompt "Iran conflict update" \
  --news-feed-url https://rss.nytimes.com/services/xml/rss/nyt/World.xml \
  --news-feed-url https://feeds.bbci.co.uk/news/world/rss.xml \
  --project-dir ./projects/iran-news \
  --script-engine template
```

In news mode, source review is mandatory before `draft`. The TUI provides a `Source Review` modal for this, while CLI stages share the same review artifacts under `review/news_source_candidates.json` and `review/news_review_state.json`. After script approval, both news and non-news runs continue through `shot-plan`, which writes `review/shot_plan.json`, `review/shot_review_state.json`, and per-shot mini previews under `review/shots/<shot-id>/`.

Duration control defaults to a +/-25% tolerance around requested minutes.
Short scripts are expanded during preflight before narration is synthesized. After a script is approved, the pipeline preserves that script and warns if the final narration lands outside the target window instead of rewriting it again.

Voice pacing defaults to `calm-documentary`. You can switch later with:

- `--voice-profile balanced`
- `--voice-profile energetic-explainer`

Pause boundaries are automatically refined to avoid awkward breaks before tiny trailing words and after common function words.

Script voice controls:

- `--script-tone conversational|documentary|curiosity-driven|analytical`
- `--target-audience "viewers outside Japan who enjoy cultural explainers"`
- `--hook-style surprising-fact|question|problem-first|story-first`
- `--narrative-mode story-led|explainer|argument-led`
- `--example-density light|balanced|heavy`

Asset relevance control:

- `--asset-keywords "cars, roads"` constrains footage search to those terms
- when keywords are set, stock queries always include them
- `--channel-profile refugio_da_fe` enables channel-aware visual vocabulary and short stock-query boosts for supported profiles
- scene clips are assigned uniquely per run (no repeated stock clip across scenes)
- `--asset-mode prefer-video|balanced|prefer-images|images-only` controls whether scenes should stay video-first or become image-led
- `--image-motion-style static|slow|balanced|fast` controls how still-image scenes are animated during render
- image-led scenes now reuse ranked shortlist stills to build short crossfaded montages when enough image candidates are available

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
- `--channel-name IMAGINE`
- `--intro-tagline "Explainers about AI systems and ideas"`
- `--outro-tagline "Watch next"`
- `--bookend-style minimal-clean` (default), `--bookend-style cinematic-subtle`, `--bookend-style brand-image-motion`, or `--bookend-style corner-fade`
- `--brand-logo-path /path/to/logo.png` (supports png/jpg/webp; svg rasterized when possible)
- `--brand-intro-image-path /path/to/intro-bg.jpg`
- `--brand-outro-image-path /path/to/outro-bg.jpg`
- `--brand-use-scene-fallback` / `--no-brand-use-scene-fallback`

In the TUI channel flow, the selected narration voice is also reused for the spoken outro CTA. The PT-BR religious channel profile now localizes that ending to Portuguese.

When `projects/brand-kit/` contains the standard channel assets, the CLI now auto-promotes the default bookend style to `brand-image-motion` and uses those assets unless you explicitly provide alternate brand paths or a different non-default style.

In branded mode, the intro/outro renderer is logo-led and asymmetric by default:

- intro uses channel name + optional tagline with a smaller left-aligned video title
- outro uses a branded header, title, end-screen boxes, and an optional CTA tagline
- scene-derived frozen frames stay fallback-only unless you enable `--brand-use-scene-fallback`

`corner-fade` is the simpler burned-in bookend treatment: the intro uses the resolved first-scene visual (or a neutral fallback when none exists) with a top-left title and single red underline, while the outro uses the resolved last-scene visual behind two lower-middle CTA beats (`Thanks for watching`, then `Remember to like the video and subscribe`) with opacity-only fades.

If you use `--script-engine ollama`, make sure Ollama server is running:

```bash
ollama serve
```

When `--script-engine ollama` is selected, the pipeline now fails fast if Ollama is unavailable or returns an invalid script plan. Use `--script-engine template` only when you intentionally want the local placeholder/debug script path.

To run narration with Piper instead of Melo:

```bash
local-video-mvp run \
  --prompt "Explain transformers in plain english" \
  --project-dir ./projects/transformers-v1 \
  --tts-engine piper \
  --piper-voice-id en_US-john-medium
```

To run narration with Kokoro:

```bash
local-video-mvp run \
  --prompt "Explain transformers in plain english" \
  --project-dir ./projects/transformers-kokoro \
  --tts-engine kokoro \
  --kokoro-lang-code en-us \
  --kokoro-voice af_heart
```

List available local Melo voices:

```bash
local-video-mvp voices --melo-language EN
```

List available Kokoro voices:

```bash
local-video-mvp voices --tts-engine kokoro --kokoro-lang-code en-us
```

Generate A/B voice samples from your existing project script:

```bash
local-video-mvp voice-ab \
  --project-dir ./projects/diffusion-v1 \
  --speakers EN-US EN-Default EN-AU \
  --sample-words 130 \
  --voice-profile calm-documentary
```

Generate Kokoro A/B voice samples:

```bash
local-video-mvp voice-ab \
  --project-dir ./projects/diffusion-v1 \
  --tts-engine kokoro \
  --kokoro-lang-code en-us \
  --voices af_heart af_sarah am_michael \
  --sample-words 130 \
  --voice-profile calm-documentary
```

Outputs are written under `output/voice_ab/`, including:

- individual speaker WAV files
- `ab_compare.wav` (single file for quick back-to-back listening)
- `voice_ab_report.json`

Inspect local YouTube draft/auth readiness:

```bash
local-video-mvp youtube-auth
local-video-mvp youtube-auth --link
local-video-mvp youtube-auth --disconnect
local-video-mvp youtube-draft --project-dir ./projects/diffusion-v1
local-video-mvp youtube-publish --project-dir ./projects/diffusion-v1
```

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
- intro style: `corner-fade`
- outro style: `corner-fade` with two-step CTA

Storage behavior with TUI defaults:

- TUI auto-creates a package under `/Users/rafaelbm/Imagine/<prompt-slug>-<timestamp>/`
- the final uploadable video lives at `/Users/rafaelbm/Imagine/<prompt-slug>-<timestamp>/<prompt-slug>-<timestamp>.mp4`
- supporting artifacts live under `/Users/rafaelbm/Imagine/<prompt-slug>-<timestamp>/metadata/`
- when `projects/brand-kit/` is present, branded bookend assets are auto-discovered for optional use, but intro stays off unless you enable it

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

- `R`: open run preflight (`minutes -> resolution -> subtitles`) and then run/advance the staged review flow (`draft -> script review -> shot plan -> shot review -> preview -> finalize`) for the active package
- in `news` mode, `R` runs `sources -> source review -> draft -> script review -> shot plan -> shot review -> preview -> finalize`
- `Y`: open the YouTube publish flow for packaged videos in `/Users/rafaelbm/Imagine`, with inline auth status plus `S` switch-account and `D` disconnect shortcuts in the picker
- `T`: open the standalone thumbnail generator, where Ollama proposes two selectable image prompts plus a custom prompt field before the app renders/exports a 16:9 thumbnail image
- `P`: open the Prompt menu (`Video brief`, `Content mode`, `Script Profile`, `Asset keywords`, and `News sources` in `news` mode)
- `S`: open settings (`Intro title card`, `Narration voice policy`, and `Visual Assets`)
- `C`: clean old packaged videos in `/Users/rafaelbm/Imagine` (`Space` marks, `A` selects all, `Enter` deletes the whole package folder)
- `Q`: quit (stops active run first)

The TUI persists the last values set through the `Prompt` menu, `Settings`, and Run preflight in `~/.imagine/tui_settings.json`, and reuses them on next launch unless the saved file is missing or invalid. The dedicated `Prompt` shortcut now stays small: `Video brief`, `Content mode`, `Script Profile`, `Asset keywords`, plus `News sources` when `Content mode` is `news`. `Script Profile` is a bundled preset that writes the hidden audience/hook/narrative/example settings for generation. When you switch to `news`, the TUI pre-fills a short curated RSS feed set that you can inspect and edit. Run preflight owns the per-run controls for minutes, resolution, subtitles, and voice selection. Subtitle settings still include burned-subtitle on/off, a presentation preset (`regular`, `highlight-follow`, or `highlight-box-follow`), position (`bottom` or `mid-safe`), highlight text color selection from the built-in palette, a separate box background color selector for `highlight-box-follow`, stepped font-size presets, and bold/outline toggles. Asset policy still includes provider toggles for `Pexels` / `Pixabay`, experimental `Coverr` / `Vecteezy` fallbacks, still-image fallback, `asset mode` (`prefer video` through `images only`), `image motion style`, and whether attribution-required sources stay eligible. Settings now focus on `Intro title card`, fast-mode behavior, the read-only narration policy view, and visual-asset policy.

YouTube caption upload now follows the subtitle policy more closely:

- if `Burned subtitles` is `Off`, new YouTube drafts default `Upload YouTube captions` to `On`
- if `Burned subtitles` is `On`, new YouTube drafts default `Upload YouTube captions` to `Off`
- caption upload requires the OAuth token to include `youtube.force-ssl`
- Shot Review now shows a miniature preview for the currently highlighted shot in the blocked-shot list, using the selected image directly or a still frame from the selected video before you open the shot

The TUI uses checkpoint modals between stages. In `news` mode it starts with `Source Review`, where each candidate can be marked `Approve facts only`, `Approve facts + screenshot`, or `Reject`, and `Continue to draft` stays blocked until the source/domain gate is satisfied. After draft, it opens a script review checkpoint first so you can read the whole script, edit scene text, approve it, or regenerate only the script. Once the script is approved, the pipeline runs `shot-plan` and opens `Shot Review`, where the blocked-shot list now shows a miniature preview for the currently highlighted shot before you open it, and the per-shot screen still shows the rendered visual, final narration audio, and burned subtitles together. MVP shot review keeps the controls intentionally small:

- `Play` the shot preview
- `Approve` the shot
- `Regenerate` tries the next unused video clip
- `Try still image` tries the next unused still image
- `Override keywords` refreshes the shot pool from only the manual shot keywords and demotes previously rejected assets for that shot
- supported channel profiles now also show matched channel terms and effective stock queries in Shot Review so off-theme search behavior is easier to spot
- use `Esc` to leave the shot modal and return to the blocked queue

When a manifest is written, the pipeline also exports `publish/youtube_description_credits.txt`. That file contains a YouTube-description-ready credits block plus optional provenance lines, and new YouTube drafts append any required credits block automatically.

After a successful finalize/full/replacement export from TUI, the package root gets the canonical video file (`/Users/rafaelbm/Imagine/<video-id>/<video-id>.mp4`).

Preview is prompted only after shot approval. After preview rendering, TUI leaves the workflow at the finalize checkpoint and shows the `review/preview.mp4` path so you can inspect it in your preferred player before pressing `R` to continue.

Install `mpv` for in-terminal scene playback:

```bash
brew install mpv
```

Ghostty works with the kitty graphics path (`mpv --vo=kitty`) on compatible setups for shot-level terminal previews.

Current MVP voice picker scope:

- language picker supports `EN` only for now
- Melo speaker picker is loaded from available EN speakers on your machine
- Kokoro picker includes English Kokoro voices in TUI voice selection and Settings preview
- voice picker entries now show strict-safe badges (`safe`, `blocked`, `review`) plus per-entry policy details in the picker
- Piper picker uses the curated built-in catalog in TUI voice selection, but strict-safe mode blocks attribution-required presets like LibriTTS and any custom-weight URLs

If Ollama is not already running, the TUI tries to start `ollama serve` automatically and stops it on exit when it was started by the TUI. If Ollama still cannot be reached, TUI now shows an `Ollama unavailable` modal and blocks the draft run before any placeholder script is generated.

Stock asset keys are auto-discovered by TUI (in this order):

1. current process env (`PEXELS_API_KEY`, `PIXABAY_API_KEY`, `COVERR_APP_ID`, `COVERR_API_KEY`, `VECTEEZY_ACCOUNT_ID`, `VECTEEZY_API_KEY`)
2. repo `.env`
3. `~/.config/imagine/stock_api_keys.json` (or `IMAGINE_STOCK_KEYS_FILE`)

Example key file:

```json
{
  "PEXELS_API_KEY": "your_pexels_key",
  "PIXABAY_API_KEY": "your_pixabay_key",
  "COVERR_APP_ID": "your_coverr_app_id",
  "COVERR_API_KEY": "your_coverr_api_key",
  "VECTEEZY_ACCOUNT_ID": "your_vecteezy_account_id",
  "VECTEEZY_API_KEY": "your_vecteezy_api_key"
}
```

`Coverr` and `Vecteezy` stay off by default and are treated as experimental fallbacks. `Coverr` is video-only, uses local hourly request tracking (demo assumption: `50` requests/hour), and marks selected assets as attribution-required with provider-branding reminders. `Vecteezy` contributes both video and still-image fallback candidates, defers licensed downloads until selection, and records its tracked quota snapshot into `run_report.json` / `rights_manifest.json`.

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
- `review/clip_catalog.json` (human-readable clip names, chosen asset metadata, and stored ranked candidates for review)

`run_report.json` also includes `caption_stats`, `duration_stats`, `pacing_stats`, and asset-provider usage snapshots for quick quality checks.

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
# 1a) draft only (script.json + narration.txt, no narration wav yet)
local-video-mvp run --workflow-stage draft --prompt "Your topic" --project-dir ./projects/demo

# 1b) draft + review prep (also resolves assets, narration, captions, and timeline inputs ahead of shot planning / preview)
local-video-mvp run --workflow-stage draft --prepare-scene-review --prompt "Your topic" --project-dir ./projects/demo

# 2) review (optional: approve current script.json, or pass reviewed JSON)
local-video-mvp run --workflow-stage review --prompt "Your topic" --project-dir ./projects/demo

# 3) shot plan + shot previews
local-video-mvp run --workflow-stage shot-plan --prompt "Your topic" --project-dir ./projects/demo

# optional: refresh shortlist candidates and local previews for one shot before regenerating it
local-video-mvp prepare-shot-candidates --project-dir ./projects/demo --shot-id scene_001_shot_01 --key-info "Updated shot direction"

# 4) preview (renders review/preview.mp4 from approved shots)
local-video-mvp run --workflow-stage preview --prompt "Your topic" --project-dir ./projects/demo

# 5) finalize (promotes preview to output/final.mp4 when inputs are unchanged; otherwise re-renders)
local-video-mvp run --workflow-stage finalize --prompt "Your topic" --project-dir ./projects/demo
```

TUI uses the same stage primitives but now follows: `draft -> script review -> shot plan -> shot review -> preview -> finalize`. In `news` mode, the equivalent staged flow is `sources -> source review -> draft -> script review -> shot plan -> shot review -> preview -> finalize`. The parameter editor also exposes audience, tone, hook style, narrative mode, example density, content mode, and news-source feeds.

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
    news_source_candidates.json
    news_review_state.json
    news_brief.json
    script_approved.json
    shot_plan.json
    shot_review_state.json
    clip_catalog.json
    scene_review_state.json
    narration_state.json
    captions_state.json
    timeline_state.json
    preview_render_state.json
    preview.mp4
    preview.srt
    shots/
      <shot-id>/
        preview.mp4
  rights_manifest.json
  assets/
    cache/
  output/
    final.mp4
    final.srt
  publish/
    youtube_description_credits.txt
```

`review/script_approved.json` is created by the `review` stage. `review/shot_plan.json` stores the planned 1-2 shots per scene, and `review/shot_review_state.json` tracks which shots are blocked, pending, or approved. In `news` mode, `review/news_source_candidates.json`, `review/news_review_state.json`, and `review/news_brief.json` hold the editorial intake, approval decisions, and approved-source fact brief used for script generation. `narration.wav` and the review-ready timeline are created during `preview`, `finalize`, or `draft --prepare-scene-review`.

## Commercial-safe notes

- Strict mode is enabled by default and intended to avoid risky defaults.
- Rights metadata is recorded for downloaded assets when available.
- You should still review platform/model licenses before distribution.
- Keep `ffmpeg` distribution/license requirements in mind for shipping apps.

## Current scope and next steps

This is a local-first MVP with TUI as the primary user surface. Suggested next milestones:

1. Add local queue worker and resumable jobs
2. Add SwiftUI wrapper app
3. Upgrade caption pass to `faster-whisper` by default
4. Improve scene-aware pacing and transitions
