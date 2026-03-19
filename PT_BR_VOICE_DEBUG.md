# PT-BR Kokoro Voice Preview — Debug Stack

## Status: BROKEN in TUI

---

## What was tried

### Attempt 1 — Better error messages
- Wrapped `KPipeline(lang_code=...)` in try/except in `pipeline.py`
- Surfaced first line of exception in TUI status bar instead of "See logs"
- **User tested in TUI: still failed silently**

### Attempt 2 — Install espeak-ng + phonemizer
- `brew install espeak-ng`
- `pip install phonemizer`
- `pip install "misaki>=0.7.16,<0.9.0"` (misaki 0.9.4 broke `EspeakWrapper.set_data_path`)
- CLI `voice-ab` command succeeded after setting `PHONEMIZER_ESPEAK_LIBRARY=/opt/homebrew/lib/libespeak-ng.dylib`
- **User tested in TUI: preview silently quits — barely hears first word then stops**

### Attempt 3 — Persist env var + load .env in TUI/CLI
- Added `PHONEMIZER_ESPEAK_LIBRARY=/opt/homebrew/lib/libespeak-ng.dylib` to `.env`
- Added `_load_dotenv_into_environ()` to `tui.py __init__` — loads all `.env` keys into `os.environ`
- Added same to `cli.py main()`
- CLI `voice-ab` confirmed working without pre-sourcing `.env`
- **User tested in TUI: preview silently quits — barely hears first word then stops**

---

## What is NOT yet understood

- Why TUI preview plays a fragment then dies — this is a different failure from "no audio at all"
- Whether the audio generation succeeds but playback crashes, or generation itself crashes mid-stream
- The TUI log file at `~/.imagine/logs/latest.log` has not yet been inspected during a failed preview attempt
- The TUI voice preview code path (`_preview_debug_voice_entry` in `tui.py`) may have a different failure mode than the `voice-ab` CLI path

---

## Next steps (not yet done)

1. Read `~/.imagine/logs/latest.log` immediately after a failed TUI preview to see the actual error
2. Add explicit logging at every step of `_preview_debug_voice_entry` to isolate where it dies
3. Confirm whether the issue is in audio generation or in playback (`mpv` / `afplay`)
