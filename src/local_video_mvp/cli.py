from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .models import (
    CONTENT_MODE_CHOICES,
    SUBTITLE_BOX_COLOR_CHOICES,
    SUBTITLE_TEXT_COLOR_CHOICES,
    SUBTITLE_POSITION_CHOICES,
    SUBTITLE_PRESET_CHOICES,
    PipelineConfig,
    normalize_caption_font_scale,
    normalize_content_mode,
    normalize_subtitle_accent_color,
    normalize_subtitle_box_color,
    normalize_subtitle_position,
    normalize_subtitle_preset,
)
from .pipeline import (
    KOKORO_LANG_CHOICES,
    VideoPipeline,
    default_kokoro_voice,
    kokoro_voice_choices_for_lang,
    normalize_kokoro_lang_code,
)
from .youtube import (
    build_youtube_publish_draft,
    detect_youtube_auth_state,
    disconnect_youtube_auth,
    draft_review_text,
    ensure_youtube_publish_draft,
    ensure_youtube_token,
    load_youtube_token,
    load_youtube_publish_draft,
    publish_youtube_draft,
    save_youtube_publish_draft,
    youtube_auth_client_secrets_path,
    youtube_auth_token_path,
    youtube_token_is_expired,
)

IMAGE_MOTION_STYLE_CHOICES = ("static", "slow", "balanced", "fast")
IMAGE_MOTION_STYLE_ALIASES = {
    "subtle": "slow",
    "documentary": "balanced",
    "dynamic": "fast",
}
IMAGE_MOTION_STYLE_ARGUMENT_CHOICES = IMAGE_MOTION_STYLE_CHOICES + tuple(IMAGE_MOTION_STYLE_ALIASES.keys())


def _parse_resolution(raw: str) -> tuple[int, int]:
    if "x" not in raw:
        raise ValueError("Resolution must be formatted as WIDTHxHEIGHT, for example 1280x720")
    width_s, height_s = raw.lower().split("x", maxsplit=1)
    width = int(width_s)
    height = int(height_s)
    if width <= 0 or height <= 0:
        raise ValueError("Resolution values must be positive integers")
    return width, height


def _parse_asset_keywords(raw: str) -> list[str]:
    if not raw:
        return []

    parts = [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        lowered = part.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(part)

    return out[:8]


def _parse_repeatable_values(raw_items: list[str] | tuple[str, ...] | None) -> list[str]:
    if not raw_items:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        for part in re.split(r"[,;\n]+", str(raw)):
            value = part.strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(value)
    return out


def _parse_clip_names(raw_items: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        for part in re.split(r"[,;\n]+", str(raw)):
            value = part.strip().lower()
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            names.append(value)
    return names


def _normalize_image_motion_style(raw_value: Any, default: str = "slow") -> str:
    value = str(raw_value or default).strip().lower()
    value = IMAGE_MOTION_STYLE_ALIASES.get(value, value)
    if value not in IMAGE_MOTION_STYLE_CHOICES:
        return default
    return value


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_str(value: Any, default: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return default


def _scaled_fast_mode_resolution(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return (640, 360)

    scale = min(1.0, 640.0 / float(width), 360.0 / float(height))
    if scale >= 1.0:
        return (width, height)

    scaled_width = max(2, int(width * scale))
    scaled_height = max(2, int(height * scale))
    if scaled_width % 2 != 0:
        scaled_width -= 1
    if scaled_height % 2 != 0:
        scaled_height -= 1
    return (max(2, scaled_width), max(2, scaled_height))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _discover_default_brand_bookend_assets() -> dict[str, str] | None:
    candidate_roots = [
        _repo_root(),
        Path.cwd().resolve(),
    ]
    seen: set[Path] = set()

    for root in candidate_roots:
        if root in seen:
            continue
        seen.add(root)
        brand_dir = root / "projects" / "brand-kit"
        logo_path = brand_dir / "logo-option-3-geometric.png"
        intro_path = brand_dir / "channel-bg-intro.jpg"
        outro_path = brand_dir / "channel-bg-outro.jpg"
        if not logo_path.exists():
            continue
        if not intro_path.exists() and not outro_path.exists():
            continue
        return {
            "brand_logo_path": str(logo_path.resolve()),
            "brand_intro_image_path": str(intro_path.resolve()) if intro_path.exists() else "",
            "brand_outro_image_path": str(outro_path.resolve()) if outro_path.exists() else "",
        }

    return None


def _apply_default_brand_bookends(config: PipelineConfig) -> None:
    if str(config.bookend_style or "").strip().lower() not in {"", "minimal-clean"}:
        return
    if config.brand_logo_path or config.brand_intro_image_path or config.brand_outro_image_path:
        return

    assets = _discover_default_brand_bookend_assets()
    if assets is None:
        return

    config.bookend_style = "brand-image-motion"
    config.brand_logo_path = assets.get("brand_logo_path") or None
    config.brand_intro_image_path = assets.get("brand_intro_image_path") or None
    config.brand_outro_image_path = assets.get("brand_outro_image_path") or None
    config.brand_use_scene_fallback = False


def _apply_fast_mode_profile(config: PipelineConfig) -> None:
    config.fast_mode = True
    config.minutes = min(config.minutes, 1)
    config.width, config.height = _scaled_fast_mode_resolution(config.width, config.height)
    config.fps = min(config.fps, 24)
    config.video_effects = "clean"
    config.include_outro = True
    config.intro_seconds = min(max(0.8, float(config.intro_seconds)), 1.2)
    config.outro_seconds = min(max(1.0, float(config.outro_seconds)), 1.4)
    config.caption_engine = "heuristic"
    config.burn_subtitles = True
    config.max_duration_adjust_passes = 0
    config.require_external_assets = False
    config.max_scenes = min(config.max_scenes, 6)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-video-mvp",
        description="Local-first long-form explainer video generator MVP",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full generation pipeline")
    run.add_argument("--prompt", required=True, help="Video prompt/topic")
    run.add_argument(
        "--workflow-stage",
        choices=["full", "sources", "draft", "review", "shot-plan", "preview", "finalize"],
        default="full",
        help="Workflow stage to execute (default: full)",
    )
    run.add_argument(
        "--review-script-json",
        help="Optional JSON file used when --workflow-stage review",
    )
    run.add_argument(
        "--prepare-scene-review",
        action="store_true",
        help="When workflow-stage=draft, also prepare clip catalog/timeline for immediate scene review",
    )
    run.add_argument(
        "--content-mode",
        choices=list(CONTENT_MODE_CHOICES),
        default="explainer",
        help="Prompting and review workflow mode",
    )
    run.add_argument(
        "--asset-keywords",
        default="",
        help="Comma-separated keywords to constrain stock footage search queries",
    )
    run.add_argument(
        "--news-feed-url",
        action="append",
        default=[],
        help="Repeatable RSS/Atom feed URL for news mode",
    )
    run.add_argument(
        "--news-max-age-hours",
        type=int,
        default=72,
        help="Maximum source recency window for news mode",
    )
    run.add_argument(
        "--news-max-candidates",
        type=int,
        default=20,
        help="Maximum number of source candidates to retain in news mode",
    )
    run.add_argument(
        "--news-min-approved-sources",
        type=int,
        default=2,
        help="Minimum approved editorial sources required in news mode",
    )
    run.add_argument("--project-dir", required=True, help="Output project directory")
    run.add_argument("--minutes", type=int, default=5, help="Target duration in minutes (default: 5)")
    run.add_argument("--resolution", default="1280x720", help="Output resolution, default 1280x720")
    run.add_argument("--fps", type=int, default=30, help="Output frame rate")
    run.add_argument(
        "--fast-mode",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use a faster debug profile (caps duration, lowers render cost, shortens bookends, keeps burned subtitles)",
    )

    run.add_argument(
        "--script-engine",
        choices=["ollama", "template"],
        default="ollama",
        help="Script generator backend",
    )
    run.add_argument("--ollama-model", default="qwen2.5:14b", help="Ollama model for script planning")
    run.add_argument(
        "--require-ollama",
        action="store_true",
        help="Deprecated compatibility flag; --script-engine ollama already fails if Ollama is unavailable",
    )
    run.add_argument(
        "--script-tone",
        choices=["conversational", "documentary", "curiosity-driven", "analytical"],
        default="conversational",
        help="Editorial tone target for script generation",
    )
    run.add_argument(
        "--target-audience",
        default="curious general audience",
        help="Audience description used when shaping script wording",
    )
    run.add_argument(
        "--hook-style",
        choices=["surprising-fact", "question", "problem-first", "story-first"],
        default="surprising-fact",
        help="Opening-hook pattern for the script planner",
    )
    run.add_argument(
        "--narrative-mode",
        choices=["story-led", "explainer", "argument-led"],
        default="story-led",
        help="Narrative structure bias for script generation",
    )
    run.add_argument(
        "--example-density",
        choices=["light", "balanced", "heavy"],
        default="balanced",
        help="How aggressively the script should use concrete examples",
    )

    run.add_argument(
        "--tts-engine",
        choices=["melo", "piper", "kokoro"],
        default="melo",
        help="Narration engine",
    )
    run.add_argument("--piper-voice-id", help="Piper voice id when --tts-engine piper")
    run.add_argument("--piper-speaker-id", type=int, help="Optional Piper speaker id")
    run.add_argument("--piper-model-url", help="Optional Piper model URL override")
    run.add_argument("--piper-config-url", help="Optional Piper config URL override")
    run.add_argument(
        "--video-effects",
        choices=["clean", "subtle-motion", "dynamic"],
        default="clean",
        help="Visual effects preset for rendered clips",
    )
    run.add_argument(
        "--asset-mode",
        choices=["prefer-video", "balanced", "prefer-images", "images-only"],
        default="prefer-video",
        help="Bias asset resolution toward video or still images",
    )
    run.add_argument(
        "--image-motion-style",
        choices=list(IMAGE_MOTION_STYLE_ARGUMENT_CHOICES),
        default="slow",
        help="Motion style for rendered still-image clips",
    )
    run.add_argument(
        "--include-intro",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Render intro card before main content (default: false)",
    )
    run.add_argument(
        "--include-outro",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render outro card after main content (default: true)",
    )
    run.add_argument("--intro-seconds", type=float, default=2.8, help="Intro card duration in seconds")
    run.add_argument("--outro-seconds", type=float, default=3.0, help="Outro card duration in seconds")
    run.add_argument("--outro-text", default="Thanks for watching", help="Outro card text")
    run.add_argument("--channel-name", default="IMAGINE", help="Brand/channel name used in intro/outro")
    run.add_argument("--intro-tagline", default="", help="Optional small intro tagline")
    run.add_argument("--outro-tagline", default="Watch next", help="Optional small outro tagline")
    run.add_argument(
        "--bookend-style",
        choices=["minimal-clean", "cinematic-subtle", "brand-image-motion", "corner-fade"],
        default="minimal-clean",
        help="Visual style preset for intro/outro cards",
    )
    run.add_argument("--brand-logo-path", help="Optional brand logo image path for intro/outro overlays")
    run.add_argument("--brand-intro-image-path", help="Optional intro background image path")
    run.add_argument("--brand-outro-image-path", help="Optional outro background image path")
    run.add_argument(
        "--brand-use-scene-fallback",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow intro/outro to fallback to scene-derived backgrounds when brand backgrounds are missing",
    )
    run.add_argument(
        "--voice-profile",
        choices=["calm-documentary", "balanced", "energetic-explainer"],
        default="calm-documentary",
        help="Narration pacing profile",
    )
    run.add_argument("--voice-speed", type=float, default=1.0, help="TTS speed multiplier")
    run.add_argument("--melo-language", default="EN", help="Melo language code")
    run.add_argument("--melo-speaker", default="EN-US", help="Preferred Melo speaker id")
    run.add_argument(
        "--kokoro-lang-code",
        choices=list(KOKORO_LANG_CHOICES),
        default="en-us",
        help="Kokoro language code",
    )
    run.add_argument("--kokoro-voice", default="af_heart", help="Kokoro voice id or comma-separated blend")

    run.add_argument(
        "--caption-engine",
        choices=["heuristic", "faster-whisper"],
        default="heuristic",
        help="Caption generation backend",
    )
    run.add_argument(
        "--caption-style",
        choices=["engagement", "line"],
        default="engagement",
        help="Subtitle chunking/timing preset",
    )
    run.add_argument(
        "--burn-subtitles",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Burn subtitles into final MP4 (default: true)",
    )
    run.add_argument(
        "--subtitle-preset",
        choices=list(SUBTITLE_PRESET_CHOICES),
        default="regular",
        help="Burned subtitle presentation preset",
    )
    run.add_argument(
        "--subtitle-position",
        choices=list(SUBTITLE_POSITION_CHOICES),
        default="bottom",
        help="Burned subtitle vertical position preset",
    )
    run.add_argument(
        "--subtitle-accent-color",
        choices=list(SUBTITLE_TEXT_COLOR_CHOICES),
        default="sunflower",
        help="Highlight text color used by follow-highlight subtitle presets",
    )
    run.add_argument(
        "--subtitle-box-color",
        choices=list(SUBTITLE_BOX_COLOR_CHOICES),
        default="sunflower",
        help="Box background color used by highlight-box-follow subtitles",
    )
    run.add_argument(
        "--subtitle-bold",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render burned subtitles in bold (default: true)",
    )
    run.add_argument(
        "--subtitle-outline",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render burned subtitles with an outline (default: true)",
    )
    run.add_argument("--caption-words-min", type=int, default=2, help="Minimum words per subtitle chunk")
    run.add_argument("--caption-words-max", type=int, default=5, help="Maximum words per subtitle chunk")
    run.add_argument("--caption-max-chars", type=int, default=32, help="Maximum characters per subtitle chunk")
    run.add_argument("--caption-min-seconds", type=float, default=0.7, help="Minimum subtitle duration")
    run.add_argument("--caption-max-seconds", type=float, default=2.4, help="Maximum subtitle duration")
    run.add_argument("--caption-font-scale", type=float, default=0.9, help="Subtitle font size scale multiplier")
    run.add_argument(
        "--caption-bottom-ratio",
        type=float,
        default=0.055,
        help="Subtitle bottom margin ratio (smaller = lower on screen)",
    )

    run.add_argument(
        "--duration-tolerance",
        type=float,
        default=0.25,
        help="Allowed duration delta ratio. Example: 0.25 = +/-25%%",
    )
    run.add_argument("--target-speech-wpm", type=int, default=145, help="Target narration words-per-minute")
    run.add_argument(
        "--max-duration-adjust-passes",
        type=int,
        default=2,
        help="Maximum script auto-adjust passes for duration control",
    )

    run.add_argument(
        "--strict-commercial-safe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable strict commercial-safe policy checks (default: true)",
    )
    run.add_argument(
        "--enable-pexels-provider",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow Pexels assets during stock search (default: true)",
    )
    run.add_argument(
        "--enable-pixabay-provider",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow Pixabay assets during stock search (default: true)",
    )
    run.add_argument(
        "--enable-vecteezy-provider",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow Vecteezy as an experimental lower-priority fallback provider (default: false)",
    )
    run.add_argument(
        "--enable-coverr-provider",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow Coverr as an experimental lower-priority fallback provider (default: false)",
    )
    run.add_argument(
        "--allow-image-assets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow still-image fallback assets when no good video clip is found (default: true)",
    )
    run.add_argument(
        "--allow-attribution-required-assets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow providers/assets that require description credits or attribution (default: true)",
    )
    run.add_argument("--pexels-api-key", default=os.environ.get("PEXELS_API_KEY"), help="Pexels API key")
    run.add_argument("--pixabay-api-key", default=os.environ.get("PIXABAY_API_KEY"), help="Pixabay API key")
    run.add_argument("--coverr-api-key", default=os.environ.get("COVERR_API_KEY"), help="Coverr API key")
    run.add_argument("--coverr-app-id", default=os.environ.get("COVERR_APP_ID"), help="Coverr app id")
    run.add_argument(
        "--vecteezy-account-id",
        default=os.environ.get("VECTEEZY_ACCOUNT_ID"),
        help="Vecteezy account id",
    )
    run.add_argument(
        "--vecteezy-api-key",
        default=os.environ.get("VECTEEZY_API_KEY"),
        help="Vecteezy API key",
    )
    run.add_argument(
        "--require-external-assets",
        action="store_true",
        help="Fail the run if any scene cannot resolve to an external stock asset",
    )
    run.add_argument("--max-scenes", type=int, default=40, help="Max number of scenes")
    run.add_argument("--min-scene-seconds", type=float, default=5.0, help="Minimum seconds per scene")
    run.add_argument("--verbose", action="store_true", help="Verbose pipeline logs")

    replace = subparsers.add_parser("replace-clips", help="Replace selected scene clips and re-render outputs")
    replace.add_argument("--project-dir", required=True, help="Existing project directory")
    replace.add_argument(
        "--clip-names",
        nargs="+",
        required=True,
        help="Clip names (or scene ids) to replace; supports comma-separated values",
    )
    replace.add_argument(
        "--asset-keywords",
        default="",
        help="Optional comma-separated replacement keywords (defaults to previous run keywords)",
    )
    replace.add_argument(
        "--asset-mode",
        choices=["prefer-video", "balanced", "prefer-images", "images-only"],
        default=None,
        help="Override the stored asset-mode preference for this replacement run",
    )
    replace.add_argument(
        "--image-motion-style",
        choices=list(IMAGE_MOTION_STYLE_ARGUMENT_CHOICES),
        default=None,
        help="Override the stored still-image motion style for this replacement run",
    )
    replace.add_argument("--pexels-api-key", default=os.environ.get("PEXELS_API_KEY"), help="Pexels API key")
    replace.add_argument("--pixabay-api-key", default=os.environ.get("PIXABAY_API_KEY"), help="Pixabay API key")
    replace.add_argument("--coverr-api-key", default=os.environ.get("COVERR_API_KEY"), help="Coverr API key")
    replace.add_argument("--coverr-app-id", default=os.environ.get("COVERR_APP_ID"), help="Coverr app id")
    replace.add_argument(
        "--vecteezy-account-id",
        default=os.environ.get("VECTEEZY_ACCOUNT_ID"),
        help="Vecteezy account id",
    )
    replace.add_argument(
        "--vecteezy-api-key",
        default=os.environ.get("VECTEEZY_API_KEY"),
        help="Vecteezy API key",
    )
    replace.add_argument(
        "--enable-pexels-provider",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the stored Pexels-provider setting for this replacement run",
    )
    replace.add_argument(
        "--enable-pixabay-provider",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the stored Pixabay-provider setting for this replacement run",
    )
    replace.add_argument(
        "--enable-vecteezy-provider",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the stored Vecteezy-provider setting for this replacement run",
    )
    replace.add_argument(
        "--enable-coverr-provider",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the stored Coverr-provider setting for this replacement run",
    )
    replace.add_argument(
        "--allow-image-assets",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the stored image-fallback setting for this replacement run",
    )
    replace.add_argument(
        "--allow-attribution-required-assets",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the stored attribution-required asset policy for this replacement run",
    )
    replace.add_argument(
        "--require-external-assets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail replacement if any selected scene cannot resolve external stock assets",
    )
    replace.add_argument("--verbose", action="store_true", help="Verbose pipeline logs")

    regenerate_shot = subparsers.add_parser("regenerate-shot", help="Regenerate a single planned shot preview")
    regenerate_shot.add_argument("--project-dir", required=True, help="Existing project directory")
    regenerate_shot.add_argument("--shot-id", required=True, help="Shot id from review/shot_plan.json")
    regenerate_shot.add_argument("--key-info", default="", help="Optional replacement key info for the shot")
    regenerate_shot.add_argument(
        "--search-query",
        action="append",
        default=[],
        help="Optional repeatable search query override used during shot regeneration",
    )
    regenerate_shot.add_argument(
        "--candidate-index",
        type=int,
        help="Optional zero-based candidate index from the stored shot shortlist",
    )
    regenerate_shot.add_argument("--verbose", action="store_true", help="Verbose pipeline logs")

    prepare_shot_candidates = subparsers.add_parser(
        "prepare-shot-candidates",
        help="Refresh a planned shot shortlist and prefetch candidate previews without changing the selected shot asset",
    )
    prepare_shot_candidates.add_argument("--project-dir", required=True, help="Existing project directory")
    prepare_shot_candidates.add_argument("--shot-id", required=True, help="Shot id from review/shot_plan.json")
    prepare_shot_candidates.add_argument("--key-info", default="", help="Optional replacement key info used to refresh the shortlist")
    prepare_shot_candidates.add_argument(
        "--search-query",
        action="append",
        default=[],
        help="Optional repeatable search query override used to refresh the shortlist",
    )
    prepare_shot_candidates.add_argument("--verbose", action="store_true", help="Verbose pipeline logs")

    inspect = subparsers.add_parser("inspect", help="Inspect latest run logs/report")
    inspect.add_argument("--project-dir", required=True, help="Project directory to inspect")

    youtube_auth = subparsers.add_parser("youtube-auth", help="Inspect or manage local YouTube OAuth readiness")
    youtube_auth.add_argument(
        "--link",
        action="store_true",
        help="Launch the local Google OAuth browser flow if a valid token is not already saved",
    )
    youtube_auth.add_argument(
        "--relink",
        action="store_true",
        help="Force a fresh Google OAuth browser flow and replace the saved token",
    )
    youtube_auth.add_argument(
        "--disconnect",
        action="store_true",
        help="Remove the saved YouTube token and revoke it remotely when possible",
    )
    youtube_auth.add_argument("--json", action="store_true", help="Print raw auth status JSON")

    youtube_draft = subparsers.add_parser("youtube-draft", help="Create or inspect a local YouTube publish draft")
    youtube_draft.add_argument("--project-dir", required=True, help="Existing project directory with final outputs")
    youtube_draft.add_argument("--prompt", default="", help="Fallback prompt used when project metadata is sparse")
    youtube_draft.add_argument(
        "--refresh",
        action="store_true",
        help="Rebuild the draft suggestions from project files before saving",
    )
    youtube_draft.add_argument("--json", action="store_true", help="Print the draft as JSON")

    youtube_publish = subparsers.add_parser("youtube-publish", help="Upload a finalized project to YouTube")
    youtube_publish.add_argument("--project-dir", required=True, help="Existing project directory with final outputs")
    youtube_publish.add_argument("--prompt", default="", help="Fallback prompt used if the draft must be rebuilt")
    youtube_publish.add_argument(
        "--refresh-draft",
        action="store_true",
        help="Rebuild the local publish draft before uploading",
    )
    youtube_publish.add_argument(
        "--relink",
        action="store_true",
        help="Force a fresh Google OAuth browser flow before uploading",
    )
    youtube_publish.add_argument("--json", action="store_true", help="Print the upload report as JSON")

    voices = subparsers.add_parser("voices", help="List available local voices")
    voices.add_argument("--tts-engine", choices=["melo", "kokoro"], default="melo", help="Voice backend to inspect")
    voices.add_argument("--melo-language", default="EN", help="Melo language code")
    voices.add_argument(
        "--kokoro-lang-code",
        choices=list(KOKORO_LANG_CHOICES),
        default="en-us",
        help="Kokoro language code",
    )

    voice_ab = subparsers.add_parser("voice-ab", help="Generate local A/B voice samples")
    voice_ab.add_argument("--project-dir", required=True, help="Project directory used as source and output root")
    voice_ab.add_argument("--source-file", help="Optional text file used as sample source")
    voice_ab.add_argument("--tts-engine", choices=["melo", "kokoro"], default="melo", help="Voice backend to compare")
    voice_ab.add_argument(
        "--speakers",
        nargs="+",
        default=["EN-US", "EN-Default", "EN-AU"],
        help="Melo speaker ids to compare",
    )
    voice_ab.add_argument("--voices", nargs="+", default=[], help="Kokoro voice ids to compare")
    voice_ab.add_argument("--sample-words", type=int, default=130, help="Approximate words used per sample")
    voice_ab.add_argument(
        "--voice-profile",
        choices=["calm-documentary", "balanced", "energetic-explainer"],
        default="calm-documentary",
        help="Narration pacing profile for all samples",
    )
    voice_ab.add_argument("--voice-speed", type=float, default=1.0, help="Base voice speed multiplier")
    voice_ab.add_argument("--melo-language", default="EN", help="Melo language code")
    voice_ab.add_argument(
        "--kokoro-lang-code",
        choices=list(KOKORO_LANG_CHOICES),
        default="en-us",
        help="Kokoro language code",
    )
    voice_ab.add_argument("--output-dir", help="Optional custom output directory")
    voice_ab.add_argument("--verbose", action="store_true", help="Verbose logs")

    tui = subparsers.add_parser("tui", help="Open terminal UI for common workflows")
    tui.add_argument("--prompt", default="Autonomous cars", help="Initial prompt shown in the TUI")
    tui.add_argument(
        "--content-mode",
        choices=list(CONTENT_MODE_CHOICES),
        default="explainer",
        help="Initial content mode",
    )
    tui.add_argument(
        "--asset-keywords",
        default="cars, roads",
        help="Initial comma-separated asset keywords",
    )
    tui.add_argument(
        "--news-feed-url",
        action="append",
        default=[],
        help="Repeatable initial RSS/Atom feed URL for news mode",
    )
    tui.add_argument(
        "--project-dir",
        default=str((Path.home() / ".imagine" / "projects").resolve()),
        help="Projects root directory used for auto-managed run workspaces",
    )
    tui.add_argument("--minutes", type=int, default=2, help="Initial target duration in minutes")
    tui.add_argument("--resolution", default="1280x720", help="Initial render resolution")
    tui.add_argument(
        "--voice-profile",
        choices=["calm-documentary", "balanced", "energetic-explainer"],
        default="calm-documentary",
        help="Initial voice profile",
    )
    tui.add_argument("--voice-speed", type=float, default=1.0, help="Initial voice speed")
    tui.add_argument(
        "--script-tone",
        choices=["conversational", "documentary", "curiosity-driven", "analytical"],
        default="conversational",
        help="Initial script tone",
    )
    tui.add_argument("--target-audience", default="curious general audience", help="Initial target audience")
    tui.add_argument(
        "--hook-style",
        choices=["surprising-fact", "question", "problem-first", "story-first"],
        default="surprising-fact",
        help="Initial opening hook style",
    )
    tui.add_argument(
        "--narrative-mode",
        choices=["story-led", "explainer", "argument-led"],
        default="story-led",
        help="Initial script narrative mode",
    )
    tui.add_argument(
        "--example-density",
        choices=["light", "balanced", "heavy"],
        default="balanced",
        help="Initial example density",
    )
    tui.add_argument(
        "--asset-mode",
        choices=["prefer-video", "balanced", "prefer-images", "images-only"],
        default="prefer-video",
        help="Initial asset-mode preference",
    )
    tui.add_argument(
        "--image-motion-style",
        choices=list(IMAGE_MOTION_STYLE_ARGUMENT_CHOICES),
        default="slow",
        help="Initial still-image motion style",
    )
    tui.add_argument(
        "--subtitle-preset",
        choices=list(SUBTITLE_PRESET_CHOICES),
        default="regular",
        help="Initial burned subtitle presentation preset",
    )
    tui.add_argument(
        "--subtitle-position",
        choices=list(SUBTITLE_POSITION_CHOICES),
        default="bottom",
        help="Initial burned subtitle position preset",
    )
    tui.add_argument(
        "--subtitle-accent-color",
        choices=list(SUBTITLE_TEXT_COLOR_CHOICES),
        default="sunflower",
        help="Initial subtitle highlight text color",
    )
    tui.add_argument(
        "--subtitle-box-color",
        choices=list(SUBTITLE_BOX_COLOR_CHOICES),
        default="sunflower",
        help="Initial subtitle box background color",
    )
    tui.add_argument("--caption-font-scale", type=float, default=0.9, help="Initial subtitle font size scale")
    tui.add_argument(
        "--subtitle-bold",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Initial subtitle bold state",
    )
    tui.add_argument(
        "--subtitle-outline",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Initial subtitle outline state",
    )
    tui.add_argument("--melo-language", choices=["EN"], default="EN", help="Initial Melo language")
    tui.add_argument("--melo-speaker", default="EN-US", help="Initial Melo speaker")
    tui.add_argument(
        "--kokoro-lang-code",
        choices=list(KOKORO_LANG_CHOICES),
        default="en-us",
        help="Initial Kokoro language code",
    )
    tui.add_argument("--kokoro-voice", default="af_heart", help="Initial Kokoro voice")
    tui.add_argument("--tts-engine", choices=["melo", "piper", "kokoro"], default="melo", help="Initial TTS engine")
    tui.add_argument("--piper-voice-id", default="", help="Initial Piper voice id")
    tui.add_argument("--piper-speaker-id", type=int, help="Initial Piper speaker id")
    tui.add_argument(
        "--fast-mode",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Initial TUI fast/debug mode state",
    )

    return parser


def run_command(args: argparse.Namespace) -> int:
    width, height = _parse_resolution(args.resolution)
    caption_words_min = max(1, int(args.caption_words_min))
    caption_words_max = max(caption_words_min, int(args.caption_words_max))
    caption_min_seconds = max(0.2, float(args.caption_min_seconds))
    caption_max_seconds = max(caption_min_seconds, float(args.caption_max_seconds))
    caption_font_scale = normalize_caption_font_scale(args.caption_font_scale, 0.9)
    caption_bottom_ratio = max(0.02, min(0.2, float(args.caption_bottom_ratio)))
    subtitle_preset = normalize_subtitle_preset(args.subtitle_preset)
    subtitle_position = normalize_subtitle_position(args.subtitle_position)
    subtitle_accent_color = normalize_subtitle_accent_color(args.subtitle_accent_color)
    subtitle_box_color = normalize_subtitle_box_color(args.subtitle_box_color, subtitle_accent_color)
    subtitle_bold = bool(args.subtitle_bold)
    subtitle_outline = bool(args.subtitle_outline)
    duration_tolerance = max(0.05, min(0.6, float(args.duration_tolerance)))
    intro_seconds = max(0.0, float(args.intro_seconds))
    outro_seconds = max(0.0, float(args.outro_seconds))

    config = PipelineConfig(
        prompt=args.prompt.strip(),
        project_dir=Path(args.project_dir).expanduser().resolve(),
        content_mode=normalize_content_mode(args.content_mode, "explainer"),
        asset_keywords=_parse_asset_keywords(str(args.asset_keywords)),
        news_feed_urls=_parse_repeatable_values(args.news_feed_url),
        news_max_age_hours=max(1, int(args.news_max_age_hours)),
        news_max_candidates=max(1, int(args.news_max_candidates)),
        news_min_approved_sources=max(1, int(args.news_min_approved_sources)),
        news_jurisdiction="us",
        news_require_manual_source_approval=normalize_content_mode(args.content_mode, "explainer") == "news",
        fast_mode=bool(args.fast_mode),
        minutes=max(1, args.minutes),
        width=width,
        height=height,
        fps=max(1, args.fps),
        script_engine=args.script_engine,
        ollama_model=args.ollama_model,
        require_ollama=bool(args.require_ollama),
        script_tone=args.script_tone,
        target_audience=str(args.target_audience).strip() or "curious general audience",
        hook_style=args.hook_style,
        narrative_mode=args.narrative_mode,
        example_density=args.example_density,
        tts_engine=args.tts_engine,
        piper_voice_id=(str(args.piper_voice_id).strip() or None),
        piper_speaker_id=args.piper_speaker_id,
        piper_model_url=(str(args.piper_model_url).strip() or None),
        piper_config_url=(str(args.piper_config_url).strip() or None),
        caption_engine=args.caption_engine,
        caption_style=args.caption_style,
        burn_subtitles=bool(args.burn_subtitles),
        subtitle_preset=subtitle_preset,
        subtitle_position=subtitle_position,
        subtitle_accent_color=subtitle_accent_color,
        subtitle_box_color=subtitle_box_color,
        subtitle_bold=subtitle_bold,
        subtitle_outline=subtitle_outline,
        caption_words_min=caption_words_min,
        caption_words_max=caption_words_max,
        caption_max_chars=max(8, int(args.caption_max_chars)),
        caption_min_seconds=caption_min_seconds,
        caption_max_seconds=caption_max_seconds,
        caption_font_scale=caption_font_scale,
        caption_bottom_ratio=caption_bottom_ratio,
        duration_tolerance_ratio=duration_tolerance,
        target_speech_wpm=max(90, min(220, int(args.target_speech_wpm))),
        max_duration_adjust_passes=max(0, int(args.max_duration_adjust_passes)),
        strict_commercial_safe=bool(args.strict_commercial_safe),
        enable_pexels_provider=bool(args.enable_pexels_provider),
        enable_pixabay_provider=bool(args.enable_pixabay_provider),
        enable_coverr_provider=bool(args.enable_coverr_provider),
        enable_vecteezy_provider=bool(args.enable_vecteezy_provider),
        pexels_api_key=args.pexels_api_key,
        pixabay_api_key=args.pixabay_api_key,
        coverr_api_key=args.coverr_api_key,
        coverr_app_id=args.coverr_app_id,
        vecteezy_account_id=args.vecteezy_account_id,
        vecteezy_api_key=args.vecteezy_api_key,
        require_external_assets=bool(args.require_external_assets),
        allow_image_assets=bool(args.allow_image_assets),
        allow_attribution_required_assets=bool(args.allow_attribution_required_assets),
        asset_mode=args.asset_mode,
        video_effects=args.video_effects,
        image_motion_style=args.image_motion_style,
        include_intro=bool(args.include_intro),
        include_outro=bool(args.include_outro),
        intro_seconds=intro_seconds,
        outro_seconds=outro_seconds,
        outro_text=str(args.outro_text).strip() or "Thanks for watching",
        channel_name=str(args.channel_name).strip() or "IMAGINE",
        intro_tagline=str(args.intro_tagline).strip(),
        outro_tagline=str(args.outro_tagline).strip() or "Watch next",
        bookend_style=args.bookend_style,
        brand_logo_path=str(args.brand_logo_path).strip() if args.brand_logo_path else None,
        brand_intro_image_path=str(args.brand_intro_image_path).strip() if args.brand_intro_image_path else None,
        brand_outro_image_path=str(args.brand_outro_image_path).strip() if args.brand_outro_image_path else None,
        brand_use_scene_fallback=bool(args.brand_use_scene_fallback),
        voice_profile=args.voice_profile,
        voice_speed=max(0.5, min(2.0, float(args.voice_speed))),
        melo_language=args.melo_language,
        melo_speaker=args.melo_speaker,
        kokoro_lang_code=normalize_kokoro_lang_code(args.kokoro_lang_code),
        kokoro_voice=(str(args.kokoro_voice).strip() or default_kokoro_voice(args.kokoro_lang_code)),
        max_scenes=max(4, args.max_scenes),
        min_scene_seconds=max(1.0, float(args.min_scene_seconds)),
        verbose=bool(args.verbose),
    )
    if config.fast_mode:
        _apply_fast_mode_profile(config)
    _apply_default_brand_bookends(config)

    pipeline = VideoPipeline(config)
    workflow_stage = str(args.workflow_stage).strip().lower()
    if workflow_stage == "full":
        outputs = pipeline.run()
        heading = "Pipeline completed successfully"
    elif workflow_stage == "sources":
        outputs = pipeline.run_sources()
        heading = "Sources stage completed successfully"
    elif workflow_stage == "draft":
        outputs = pipeline.run_draft(prepare_scene_review=bool(args.prepare_scene_review))
        heading = "Draft stage completed successfully"
    elif workflow_stage == "review":
        review_json_path = Path(args.review_script_json).expanduser().resolve() if args.review_script_json else None
        outputs = pipeline.run_review(review_script_path=review_json_path)
        heading = "Review stage completed successfully"
    elif workflow_stage == "shot-plan":
        outputs = pipeline.run_shot_plan()
        heading = "Shot-plan stage completed successfully"
    elif workflow_stage == "preview":
        outputs = pipeline.run_preview()
        heading = "Preview stage completed successfully"
    elif workflow_stage == "finalize":
        outputs = pipeline.run_finalize()
        heading = "Finalize stage completed successfully"
    else:
        raise RuntimeError(f"Unsupported workflow stage: {workflow_stage}")

    print(f"\n{heading}:\n")
    preferred_keys = (
        "project_dir",
        "script",
        "approved_script",
        "timeline",
        "clip_catalog",
        "narration_txt",
        "narration",
        "captions",
        "captions_ass",
        "preview_mp4",
        "preview_srt",
        "final_mp4",
        "final_srt",
        "youtube_credits",
        "manifest",
        "run_log",
        "run_report",
    )
    printed: set[str] = set()
    for key in preferred_keys:
        value = outputs.get(key)
        if value:
            print(f"- {key}: {value}")
            printed.add(key)

    for key in sorted(outputs.keys()):
        if key in printed:
            continue
        value = outputs.get(key)
        if value:
            print(f"- {key}: {value}")
    return 0


def replace_clips_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        raise RuntimeError(f"Project directory not found: {project_dir}")

    clip_names = _parse_clip_names(list(args.clip_names))
    if not clip_names:
        raise RuntimeError("No clip names were provided. Use --clip-names with one or more entries.")

    manifest_config: dict[str, object] = {}
    manifest_path = project_dir / "rights_manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("config"), dict):
                manifest_config = dict(payload["config"])
        except Exception:
            manifest_config = {}

    prompt_path = project_dir / "prompt.txt"
    prompt = project_dir.name
    if prompt_path.exists():
        raw_prompt = prompt_path.read_text(encoding="utf-8").strip()
        if raw_prompt:
            prompt = raw_prompt

    keywords = _parse_asset_keywords(str(args.asset_keywords))
    if not keywords:
        raw_keywords = manifest_config.get("asset_keywords")
        if isinstance(raw_keywords, list):
            keywords = _parse_asset_keywords(",".join(str(item) for item in raw_keywords if str(item).strip()))

    resolution = str(manifest_config.get("resolution") or "1280x720")
    try:
        width, height = _parse_resolution(resolution)
    except Exception:
        width, height = (1280, 720)

    config = PipelineConfig(
        prompt=prompt,
        project_dir=project_dir,
        asset_keywords=keywords,
        fast_mode=_coerce_bool(manifest_config.get("fast_mode"), False),
        minutes=max(1, _coerce_int(manifest_config.get("minutes"), 1)),
        width=width,
        height=height,
        fps=max(1, _coerce_int(manifest_config.get("fps"), 30)),
        script_engine="template",
        ollama_model="qwen2.5:14b",
        require_ollama=False,
        script_tone=_coerce_str(manifest_config.get("script_tone"), "conversational"),
        target_audience=_coerce_str(manifest_config.get("target_audience"), "curious general audience"),
        hook_style=_coerce_str(manifest_config.get("hook_style"), "surprising-fact"),
        narrative_mode=_coerce_str(manifest_config.get("narrative_mode"), "story-led"),
        example_density=_coerce_str(manifest_config.get("example_density"), "balanced"),
        tts_engine=_coerce_str(manifest_config.get("tts_engine"), "melo"),
        caption_engine=_coerce_str(manifest_config.get("caption_engine"), "heuristic"),
        caption_style=_coerce_str(manifest_config.get("caption_style"), "engagement"),
        burn_subtitles=_coerce_bool(manifest_config.get("burn_subtitles"), True),
        subtitle_preset=normalize_subtitle_preset(manifest_config.get("subtitle_preset"), "regular"),
        subtitle_position=normalize_subtitle_position(manifest_config.get("subtitle_position"), "bottom"),
        subtitle_accent_color=normalize_subtitle_accent_color(
            manifest_config.get("subtitle_accent_color"),
            "sunflower",
        ),
        subtitle_box_color=normalize_subtitle_box_color(
            manifest_config.get("subtitle_box_color"),
            normalize_subtitle_accent_color(manifest_config.get("subtitle_accent_color"), "sunflower"),
        ),
        subtitle_bold=_coerce_bool(manifest_config.get("subtitle_bold"), True),
        subtitle_outline=_coerce_bool(manifest_config.get("subtitle_outline"), True),
        strict_commercial_safe=_coerce_bool(manifest_config.get("strict_commercial_safe"), True),
        enable_pexels_provider=(
            _coerce_bool(args.enable_pexels_provider, True)
            if args.enable_pexels_provider is not None
            else _coerce_bool(manifest_config.get("enable_pexels_provider"), True)
        ),
        enable_pixabay_provider=(
            _coerce_bool(args.enable_pixabay_provider, True)
            if args.enable_pixabay_provider is not None
            else _coerce_bool(manifest_config.get("enable_pixabay_provider"), True)
        ),
        enable_coverr_provider=(
            _coerce_bool(args.enable_coverr_provider, False)
            if args.enable_coverr_provider is not None
            else _coerce_bool(manifest_config.get("enable_coverr_provider"), False)
        ),
        enable_vecteezy_provider=(
            _coerce_bool(args.enable_vecteezy_provider, False)
            if args.enable_vecteezy_provider is not None
            else _coerce_bool(manifest_config.get("enable_vecteezy_provider"), False)
        ),
        pexels_api_key=args.pexels_api_key,
        pixabay_api_key=args.pixabay_api_key,
        coverr_api_key=args.coverr_api_key,
        coverr_app_id=args.coverr_app_id,
        vecteezy_account_id=args.vecteezy_account_id,
        vecteezy_api_key=args.vecteezy_api_key,
        require_external_assets=bool(args.require_external_assets),
        allow_image_assets=(
            _coerce_bool(args.allow_image_assets, True)
            if args.allow_image_assets is not None
            else _coerce_bool(manifest_config.get("allow_image_assets"), True)
        ),
        allow_attribution_required_assets=(
            _coerce_bool(args.allow_attribution_required_assets, True)
            if args.allow_attribution_required_assets is not None
            else _coerce_bool(manifest_config.get("allow_attribution_required_assets"), True)
        ),
        asset_mode=(
            str(args.asset_mode).strip().lower()
            if args.asset_mode is not None
            else _coerce_str(manifest_config.get("asset_mode"), "prefer-video")
        ),
        video_effects=_coerce_str(manifest_config.get("video_effects"), "clean"),
        image_motion_style=(
            _normalize_image_motion_style(args.image_motion_style)
            if args.image_motion_style is not None
            else _normalize_image_motion_style(_coerce_str(manifest_config.get("image_motion_style"), "slow"))
        ),
        include_intro=_coerce_bool(manifest_config.get("include_intro"), False),
        include_outro=_coerce_bool(manifest_config.get("include_outro"), True),
        intro_seconds=max(0.0, _coerce_float(manifest_config.get("intro_seconds"), 0.0)),
        outro_seconds=max(0.0, _coerce_float(manifest_config.get("outro_seconds"), 0.0)),
        outro_text=_coerce_str(manifest_config.get("outro_text"), "Thanks for watching"),
        channel_name=_coerce_str(manifest_config.get("channel_name"), "IMAGINE"),
        intro_tagline=_coerce_str(manifest_config.get("intro_tagline"), ""),
        outro_tagline=_coerce_str(manifest_config.get("outro_tagline"), "Watch next"),
        bookend_style=_coerce_str(manifest_config.get("bookend_style"), "minimal-clean"),
        brand_logo_path=_coerce_str(manifest_config.get("brand_logo_path"), "") or None,
        brand_intro_image_path=_coerce_str(manifest_config.get("brand_intro_image_path"), "") or None,
        brand_outro_image_path=_coerce_str(manifest_config.get("brand_outro_image_path"), "") or None,
        brand_use_scene_fallback=_coerce_bool(manifest_config.get("brand_use_scene_fallback"), False),
        voice_profile=_coerce_str(manifest_config.get("voice_profile"), "calm-documentary"),
        voice_speed=max(0.5, min(2.0, _coerce_float(manifest_config.get("voice_speed"), 1.0))),
        caption_font_scale=normalize_caption_font_scale(
            _coerce_float(manifest_config.get("caption_font_scale"), 0.9),
            0.9,
        ),
        caption_bottom_ratio=max(0.02, min(0.2, _coerce_float(manifest_config.get("caption_bottom_ratio"), 0.055))),
        melo_language=_coerce_str(manifest_config.get("melo_language"), "EN"),
        melo_speaker=_coerce_str(manifest_config.get("melo_speaker"), "EN-US"),
        kokoro_lang_code=normalize_kokoro_lang_code(_coerce_str(manifest_config.get("kokoro_lang_code"), "en-us")),
        kokoro_voice=(
            _coerce_str(
                manifest_config.get("kokoro_voice"),
                default_kokoro_voice(manifest_config.get("kokoro_lang_code")),
            )
        ),
        piper_voice_id=_coerce_str(manifest_config.get("piper_voice_id"), "") or None,
        piper_speaker_id=(
            _coerce_int(manifest_config.get("piper_speaker_id"), 0)
            if manifest_config.get("piper_speaker_id") is not None
            else None
        ),
        verbose=bool(args.verbose),
    )
    if config.tts_engine not in {"melo", "piper", "kokoro"}:
        config.tts_engine = "melo"
    _apply_default_brand_bookends(config)

    pipeline = VideoPipeline(config)
    outputs = pipeline.replace_clips_by_name(clip_names)

    print("\nClip replacement completed successfully:\n")
    for key in (
        "project_dir",
        "script",
        "timeline",
        "clip_catalog",
        "narration",
        "captions",
        "captions_ass",
        "final_mp4",
        "final_srt",
        "youtube_credits",
        "manifest",
        "run_log",
        "run_report",
    ):
        print(f"- {key}: {outputs[key]}")
    return 0


def regenerate_shot_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        raise RuntimeError(f"Project directory not found: {project_dir}")

    config = _pipeline_config_from_existing_project(project_dir, verbose=bool(args.verbose))
    pipeline = VideoPipeline(config)
    outputs = pipeline.regenerate_shot(
        str(args.shot_id).strip(),
        key_info=(str(args.key_info).strip() or None),
        search_queries=[str(item).strip() for item in args.search_query or [] if str(item).strip()],
        candidate_index=args.candidate_index,
    )
    print("\nShot regenerated successfully:\n")
    for key in sorted(outputs):
        value = outputs.get(key)
        if value:
            print(f"- {key}: {value}")
    return 0


def _pipeline_config_from_existing_project(project_dir: Path, *, verbose: bool) -> PipelineConfig:
    manifest_config: dict[str, object] = {}
    manifest_path = project_dir / "rights_manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("config"), dict):
                manifest_config = dict(payload["config"])
        except Exception:
            manifest_config = {}

    prompt_path = project_dir / "prompt.txt"
    prompt = project_dir.name
    if prompt_path.exists():
        raw_prompt = prompt_path.read_text(encoding="utf-8").strip()
        if raw_prompt:
            prompt = raw_prompt

    resolution = str(manifest_config.get("resolution") or "1280x720")
    try:
        width, height = _parse_resolution(resolution)
    except Exception:
        width, height = (1280, 720)

    asset_keywords_raw = manifest_config.get("asset_keywords")
    asset_keywords = (
        [str(item).strip() for item in asset_keywords_raw if str(item).strip()]
        if isinstance(asset_keywords_raw, list)
        else []
    )

    return PipelineConfig(
        prompt=prompt,
        project_dir=project_dir,
        content_mode=normalize_content_mode(manifest_config.get("content_mode"), "explainer"),
        asset_keywords=asset_keywords,
        fast_mode=_coerce_bool(manifest_config.get("fast_mode"), False),
        minutes=max(1, _coerce_int(manifest_config.get("minutes"), 1)),
        width=width,
        height=height,
        fps=max(1, _coerce_int(manifest_config.get("fps"), 30)),
        script_engine="template",
        pexels_api_key=os.environ.get("PEXELS_API_KEY"),
        pixabay_api_key=os.environ.get("PIXABAY_API_KEY"),
        coverr_api_key=os.environ.get("COVERR_API_KEY"),
        coverr_app_id=os.environ.get("COVERR_APP_ID"),
        vecteezy_account_id=os.environ.get("VECTEEZY_ACCOUNT_ID"),
        vecteezy_api_key=os.environ.get("VECTEEZY_API_KEY"),
        enable_pexels_provider=_coerce_bool(manifest_config.get("enable_pexels_provider"), True),
        enable_pixabay_provider=_coerce_bool(manifest_config.get("enable_pixabay_provider"), True),
        enable_coverr_provider=_coerce_bool(manifest_config.get("enable_coverr_provider"), False),
        enable_vecteezy_provider=_coerce_bool(manifest_config.get("enable_vecteezy_provider"), False),
        allow_image_assets=_coerce_bool(manifest_config.get("allow_image_assets"), True),
        allow_attribution_required_assets=_coerce_bool(manifest_config.get("allow_attribution_required_assets"), True),
        asset_mode=_coerce_str(manifest_config.get("asset_mode"), "prefer-video"),
        image_motion_style=_normalize_image_motion_style(manifest_config.get("image_motion_style"), "slow"),
        tts_engine=_coerce_str(manifest_config.get("tts_engine"), "melo"),
        caption_engine=_coerce_str(manifest_config.get("caption_engine"), "heuristic"),
        caption_style=_coerce_str(manifest_config.get("caption_style"), "engagement"),
        burn_subtitles=_coerce_bool(manifest_config.get("burn_subtitles"), True),
        subtitle_preset=normalize_subtitle_preset(manifest_config.get("subtitle_preset"), "regular"),
        subtitle_position=normalize_subtitle_position(manifest_config.get("subtitle_position"), "bottom"),
        subtitle_accent_color=normalize_subtitle_accent_color(manifest_config.get("subtitle_accent_color"), "sunflower"),
        subtitle_box_color=normalize_subtitle_box_color(
            manifest_config.get("subtitle_box_color"),
            normalize_subtitle_accent_color(manifest_config.get("subtitle_accent_color"), "sunflower"),
        ),
        subtitle_bold=_coerce_bool(manifest_config.get("subtitle_bold"), True),
        subtitle_outline=_coerce_bool(manifest_config.get("subtitle_outline"), True),
        voice_profile=_coerce_str(manifest_config.get("voice_profile"), "calm-documentary"),
        voice_speed=max(0.5, min(2.0, _coerce_float(manifest_config.get("voice_speed"), 1.0))),
        caption_font_scale=normalize_caption_font_scale(
            _coerce_float(manifest_config.get("caption_font_scale"), 0.9),
            0.9,
        ),
        caption_bottom_ratio=max(0.02, min(0.2, _coerce_float(manifest_config.get("caption_bottom_ratio"), 0.055))),
        melo_language=_coerce_str(manifest_config.get("melo_language"), "EN"),
        melo_speaker=_coerce_str(manifest_config.get("melo_speaker"), "EN-US"),
        kokoro_lang_code=normalize_kokoro_lang_code(_coerce_str(manifest_config.get("kokoro_lang_code"), "en-us")),
        kokoro_voice=_coerce_str(
            manifest_config.get("kokoro_voice"),
            default_kokoro_voice(manifest_config.get("kokoro_lang_code")),
        ),
        piper_voice_id=_coerce_str(manifest_config.get("piper_voice_id"), "") or None,
        piper_speaker_id=(
            _coerce_int(manifest_config.get("piper_speaker_id"), 0)
            if manifest_config.get("piper_speaker_id") is not None
            else None
        ),
        verbose=bool(verbose),
    )


def prepare_shot_candidates_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        raise RuntimeError(f"Project directory not found: {project_dir}")

    config = _pipeline_config_from_existing_project(project_dir, verbose=bool(args.verbose))
    pipeline = VideoPipeline(config)
    outputs = pipeline.prepare_shot_candidates(
        str(args.shot_id).strip(),
        key_info=(str(args.key_info).strip() or None),
        search_queries=[str(item).strip() for item in args.search_query or [] if str(item).strip()],
    )
    print("\nShot candidates prepared successfully:\n")
    for key in sorted(outputs):
        value = outputs.get(key)
        if value:
            print(f"- {key}: {value}")
    return 0


def inspect_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    report_path = project_dir / "run_report.json"
    log_path = project_dir / "run.log"

    if not report_path.exists():
        print(f"No run report found at: {report_path}", file=sys.stderr)
        if log_path.exists():
            print(f"Log file exists at: {log_path}")
        return 2

    payload = json.loads(report_path.read_text(encoding="utf-8"))

    print("\nRun inspection:\n")
    print(f"- project: {project_dir}")
    print(f"- status: {payload.get('status', 'unknown')}")
    print(f"- started_at: {payload.get('started_at')}")
    print(f"- finished_at: {payload.get('finished_at')}")
    print(f"- total_seconds: {payload.get('total_seconds')}")

    stage_times = payload.get("stage_times", {})
    if isinstance(stage_times, dict) and stage_times:
        print("- stage_times:")
        for key, value in stage_times.items():
            print(f"  - {key}: {value}s")

    warnings = payload.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        print("- warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("- warnings: none")

    caption_stats = payload.get("caption_stats", {})
    if isinstance(caption_stats, dict) and caption_stats:
        print("- caption_stats:")
        for key, value in caption_stats.items():
            print(f"  - {key}: {value}")

    duration_stats = payload.get("duration_stats", {})
    if isinstance(duration_stats, dict) and duration_stats:
        print("- duration_stats:")
        for key, value in duration_stats.items():
            print(f"  - {key}: {value}")

    pacing_stats = payload.get("pacing_stats", {})
    if isinstance(pacing_stats, dict) and pacing_stats:
        print("- pacing_stats:")
        for key, value in pacing_stats.items():
            print(f"  - {key}: {value}")

    asset_stats = payload.get("asset_stats", {})
    if isinstance(asset_stats, dict) and asset_stats:
        print("- asset_stats:")
        for key, value in asset_stats.items():
            print(f"  - {key}: {value}")

    optimization_stats = payload.get("optimization_stats", {})
    if isinstance(optimization_stats, dict) and optimization_stats:
        print("- optimization_stats:")
        for key, value in optimization_stats.items():
            print(f"  - {key}: {value}")

    outputs = payload.get("outputs", {})
    if isinstance(outputs, dict) and outputs:
        print("- outputs:")
        for key, value in outputs.items():
            print(f"  - {key}: {value}")

    print(f"- report_file: {report_path}")
    print(f"- log_file: {log_path}")
    return 0


def youtube_auth_command(args: argparse.Namespace) -> int:
    if bool(args.disconnect):
        disconnect_payload = disconnect_youtube_auth(notify=print)
        auth_state = detect_youtube_auth_state()
        payload = {
            "linked": auth_state.linked,
            "reason": auth_state.reason,
            "client_secrets_path": auth_state.client_secrets_path or str(youtube_auth_client_secrets_path()),
            "token_path": auth_state.token_path or str(youtube_auth_token_path()),
            "revoked_remote_token": disconnect_payload.get("revoked_remote_token"),
            "removed_local_token": disconnect_payload.get("removed_local_token"),
            "warning": disconnect_payload.get("warning"),
        }
        if bool(args.link) or bool(args.relink):
            token_payload = ensure_youtube_token(force_relink=True, notify=print)
            auth_state = detect_youtube_auth_state()
            payload.update(
                {
                    "linked": auth_state.linked,
                    "reason": auth_state.reason,
                    "client_secrets_path": auth_state.client_secrets_path or str(youtube_auth_client_secrets_path()),
                    "token_path": auth_state.token_path or str(youtube_auth_token_path()),
                    "scope": token_payload.get("scope"),
                    "expires_in": token_payload.get("expires_in"),
                    "token_expired": youtube_token_is_expired(token_payload),
                }
            )
            if bool(args.json):
                print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
                return 0

            print("\nYouTube auth relinked successfully:\n")
            print(f"- linked: {'yes' if auth_state.linked else 'no'}")
            print(f"- token: {payload['token_path']}")
            print(f"- scope: {payload.get('scope')}")
            print(f"- expires_in: {payload.get('expires_in')}")
            if payload.get("warning"):
                print(f"- warning: {payload['warning']}")
            return 0

        if bool(args.json):
            print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
            return 0

        print("\nYouTube auth disconnected:\n")
        print(f"- linked: {'yes' if auth_state.linked else 'no'}")
        print(f"- token_removed: {'yes' if payload['removed_local_token'] else 'no'}")
        print(f"- remote_revoke: {'yes' if payload['revoked_remote_token'] else 'no'}")
        if payload.get("warning"):
            print(f"- warning: {payload['warning']}")
        return 0

    if bool(args.link) or bool(args.relink):
        token_payload = ensure_youtube_token(force_relink=bool(args.relink), notify=print)
        auth_state = detect_youtube_auth_state()
        payload = {
            "linked": auth_state.linked,
            "reason": auth_state.reason,
            "client_secrets_path": auth_state.client_secrets_path or str(youtube_auth_client_secrets_path()),
            "token_path": auth_state.token_path or str(youtube_auth_token_path()),
            "scope": token_payload.get("scope"),
            "expires_in": token_payload.get("expires_in"),
            "token_expired": youtube_token_is_expired(token_payload),
        }
        if bool(args.json):
            print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
            return 0

        print("\nYouTube auth linked successfully:\n")
        print(f"- linked: {'yes' if auth_state.linked else 'no'}")
        print(f"- token: {payload['token_path']}")
        print(f"- scope: {payload['scope']}")
        print(f"- expires_in: {payload['expires_in']}")
        return 0

    auth_state = detect_youtube_auth_state()
    token_payload = load_youtube_token() if auth_state.linked else None
    payload = {
        "linked": auth_state.linked,
        "reason": auth_state.reason,
        "client_secrets_path": auth_state.client_secrets_path or str(youtube_auth_client_secrets_path()),
        "token_path": auth_state.token_path or str(youtube_auth_token_path()),
        "scope": token_payload.get("scope") if isinstance(token_payload, dict) else None,
        "token_expired": youtube_token_is_expired(token_payload) if isinstance(token_payload, dict) else None,
    }

    if bool(args.json):
        print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
        return 0

    print("\nYouTube auth status:\n")
    print(f"- linked: {'yes' if auth_state.linked else 'no'}")
    print(f"- reason: {auth_state.reason}")
    print(f"- client_secrets: {payload['client_secrets_path']}")
    print(f"- token: {payload['token_path']}")
    if payload["scope"]:
        print(f"- scope: {payload['scope']}")
    if payload["token_expired"] is not None:
        print(f"- token_expired: {'yes' if payload['token_expired'] else 'no'}")
    return 0


def youtube_draft_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        raise RuntimeError(f"Project directory not found: {project_dir}")

    final_mp4 = project_dir / "output" / "final.mp4"
    if not final_mp4.exists():
        raise RuntimeError(f"Final MP4 not found for YouTube draft: {final_mp4}")

    fallback_prompt = str(args.prompt or "").strip()
    if bool(args.refresh):
        draft = build_youtube_publish_draft(project_dir, fallback_prompt=fallback_prompt)
    else:
        draft = ensure_youtube_publish_draft(project_dir, fallback_prompt=fallback_prompt)

    draft_path = save_youtube_publish_draft(draft)

    if bool(args.json):
        print(json.dumps(draft.to_dict(), indent=2, ensure_ascii=True, sort_keys=True))
        return 0

    print("\nYouTube draft ready:\n")
    print(f"- draft_file: {draft_path}")
    print(f"- project_dir: {project_dir}")
    print("")
    print(draft_review_text(draft))
    return 0


def youtube_publish_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        raise RuntimeError(f"Project directory not found: {project_dir}")

    fallback_prompt = str(args.prompt or "").strip()
    if bool(args.refresh_draft):
        draft = build_youtube_publish_draft(project_dir, fallback_prompt=fallback_prompt)
        save_youtube_publish_draft(draft)
    else:
        draft = load_youtube_publish_draft(project_dir)
        if draft is None:
            draft = ensure_youtube_publish_draft(project_dir, fallback_prompt=fallback_prompt)

    report = publish_youtube_draft(draft, relink=bool(args.relink), notify=print)
    if bool(args.json):
        print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))
        return 0

    print("\nYouTube upload completed:\n")
    print(f"- report_file: {report.get('report_path')}")
    print(f"- video_id: {report.get('video_id')}")
    print(f"- video_url: {report.get('video_url')}")
    print(f"- visibility: {report.get('visibility')}")
    print(f"- thumbnail_uploaded: {'yes' if report.get('thumbnail_uploaded') else 'no'}")
    print(f"- captions_uploaded: {'yes' if report.get('captions_uploaded') else 'no'}")
    if report.get("publish_at"):
        print(f"- publish_at: {report.get('publish_at')}")
    warnings = report.get("warnings")
    if isinstance(warnings, list) and warnings:
        print("- warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


def _load_melo_speakers(language: str) -> dict[str, int]:
    from melo.api import TTS  # type: ignore

    tts = TTS(language=language, device="auto")
    hps = getattr(tts, "hps", None)
    hps_data = getattr(hps, "data", None)
    return dict(getattr(hps_data, "spk2id", {}) or {})


def _load_kokoro_voices(lang_code: str) -> list[str]:
    return list(kokoro_voice_choices_for_lang(normalize_kokoro_lang_code(lang_code)))


def _load_voice_sample_source(project_dir: Path, source_file: str | None) -> str:
    if source_file:
        source_path = Path(source_file).expanduser().resolve()
        if not source_path.exists():
            raise RuntimeError(f"Source file not found: {source_path}")
        return source_path.read_text(encoding="utf-8")

    candidates = [
        project_dir / "review" / "script_review.md",
        project_dir / "narration.txt",
        project_dir / "script.json",
        project_dir / "prompt.txt",
    ]

    for path in candidates:
        if not path.exists():
            continue

        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            scenes = payload.get("scenes", [])
            if isinstance(scenes, list):
                text = "\n\n".join(
                    str(scene.get("voiceover") or "").strip()
                    for scene in scenes
                    if isinstance(scene, dict) and str(scene.get("voiceover") or "").strip()
                )
                if text.strip():
                    return text
            continue

        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".md":
            lines = [line for line in raw.splitlines() if not line.strip().startswith("#")]
            raw = "\n".join(lines)
        if raw.strip():
            return raw

    raise RuntimeError(
        "Could not find source text. Provide --source-file or ensure narration/script files exist in the project."
    )


def voices_command(args: argparse.Namespace) -> int:
    if args.tts_engine == "kokoro":
        voices = _load_kokoro_voices(args.kokoro_lang_code)
        if not voices:
            print("No Kokoro voices are configured for this language.")
            return 0

        print("\nAvailable Kokoro voices:\n")
        for name in voices:
            print(f"- {name}")
        return 0

    try:
        spk2id = _load_melo_speakers(args.melo_language)
    except Exception:  # noqa: BLE001
        print(
            "MeloTTS not available. Install voice dependencies with: "
            "python -m pip install -e '.[voice]'",
            file=sys.stderr,
        )
        return 2

    if not spk2id:
        print("No Melo speakers reported for this language/model.")
        return 0

    print("\nAvailable Melo voices:\n")
    for name, speaker_id in sorted(spk2id.items(), key=lambda pair: str(pair[0])):
        print(f"- {name}: {speaker_id}")
    return 0


def voice_ab_command(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        raise RuntimeError(f"Project directory not found: {project_dir}")

    source_text = _load_voice_sample_source(project_dir, args.source_file)
    engine = str(args.tts_engine).strip().lower() or "melo"
    valid: list[str]

    if engine == "kokoro":
        available_kokoro = _load_kokoro_voices(args.kokoro_lang_code)
        requested_kokoro: list[str] = []
        seen_kokoro: set[str] = set()
        for voice in args.voices:
            key = str(voice).strip()
            if not key or key in seen_kokoro:
                continue
            seen_kokoro.add(key)
            requested_kokoro.append(key)

        valid = [voice for voice in requested_kokoro if voice in available_kokoro]
        invalid = [voice for voice in requested_kokoro if voice not in available_kokoro]
        if invalid:
            print(f"Skipping unknown Kokoro voices: {', '.join(invalid)}")

        if not valid:
            defaults = [
                default_kokoro_voice(args.kokoro_lang_code),
                *(voice for voice in ["af_bella", "am_michael", "bf_emma"] if voice in available_kokoro),
            ]
            dedup_defaults: list[str] = []
            for voice in defaults:
                if voice and voice not in dedup_defaults:
                    dedup_defaults.append(voice)
            valid = dedup_defaults[:3] or available_kokoro[:3]
    else:
        try:
            available = _load_melo_speakers(args.melo_language)
        except Exception as exc:  # noqa: BLE001
            print(f"Error loading Melo voices: {exc}", file=sys.stderr)
            return 2

        requested: list[str] = []
        seen: set[str] = set()
        for speaker in args.speakers:
            key = str(speaker).strip()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            requested.append(key)

        valid = [speaker for speaker in requested if speaker in available]
        invalid = [speaker for speaker in requested if speaker not in available]
        if invalid:
            print(f"Skipping unknown speakers: {', '.join(invalid)}")

        if not valid:
            fallback = [name for name in ["EN-US", "EN-Default", "EN-AU"] if name in available]
            valid = fallback or list(sorted(available.keys()))[:3]

    output_dir = (
        Path(args.output_dir).expanduser().resolve() if args.output_dir else (project_dir / "output" / "voice_ab")
    )

    config = PipelineConfig(
        prompt="voice-ab",
        project_dir=project_dir,
        minutes=1,
        width=1280,
        height=720,
        fps=30,
        script_engine="template",
        ollama_model="qwen2.5:14b",
        require_ollama=False,
        tts_engine=engine,
        caption_engine="heuristic",
        caption_style="engagement",
        burn_subtitles=True,
        subtitle_preset="regular",
        subtitle_position="bottom",
        subtitle_accent_color="sunflower",
        caption_words_min=2,
        caption_words_max=5,
        caption_max_chars=32,
        caption_min_seconds=0.7,
        caption_max_seconds=2.4,
        caption_font_scale=0.9,
        caption_bottom_ratio=0.055,
        duration_tolerance_ratio=0.25,
        target_speech_wpm=145,
        max_duration_adjust_passes=1,
        strict_commercial_safe=True,
        pexels_api_key=None,
        pixabay_api_key=None,
        voice_profile=args.voice_profile,
        voice_speed=max(0.5, min(2.0, float(args.voice_speed))),
        melo_language=args.melo_language,
        melo_speaker=(valid[0] if engine == "melo" else "EN-US"),
        kokoro_lang_code=normalize_kokoro_lang_code(args.kokoro_lang_code),
        kokoro_voice=(valid[0] if engine == "kokoro" else default_kokoro_voice(args.kokoro_lang_code)),
        max_scenes=40,
        min_scene_seconds=5.0,
        verbose=bool(args.verbose),
    )

    pipeline = VideoPipeline(config)
    report = pipeline.generate_voice_ab_samples(
        text=source_text,
        speakers=valid,
        output_dir=output_dir,
        sample_words=max(40, int(args.sample_words)),
    )

    print("\nVoice A/B samples generated:\n")
    print(f"- output_dir: {report['output_dir']}")
    print(f"- compare_mix: {report['compare_mix']}")
    print(f"- report_file: {report['report_file']}")
    print("- samples:")
    for item in report.get("samples", []):
        sample_label = str(item.get("voice") or item.get("speaker") or "voice").strip()
        print(f"  - {sample_label}: {item['file']} ({item['duration_seconds']}s)")
    return 0


def tui_command(args: argparse.Namespace) -> int:
    from .tui import run_tui

    prompt = str(args.prompt).strip() or "Your topic"
    content_mode = normalize_content_mode(args.content_mode, "explainer")
    asset_keywords = _parse_asset_keywords(str(args.asset_keywords))
    news_feed_urls = _parse_repeatable_values(args.news_feed_url)
    project_dir = Path(args.project_dir).expanduser().resolve()
    minutes = max(1, int(args.minutes))
    resolution = str(args.resolution).strip() or "1280x720"
    _parse_resolution(resolution)
    voice_profile = str(args.voice_profile).strip() or "calm-documentary"
    voice_speed = max(0.5, min(2.0, float(args.voice_speed)))
    script_tone = str(args.script_tone).strip() or "conversational"
    target_audience = str(args.target_audience).strip() or "curious general audience"
    hook_style = str(args.hook_style).strip() or "surprising-fact"
    narrative_mode = str(args.narrative_mode).strip() or "story-led"
    example_density = str(args.example_density).strip() or "balanced"
    asset_mode = str(args.asset_mode).strip().lower() or "prefer-video"
    image_motion_style = _normalize_image_motion_style(args.image_motion_style, "slow")
    melo_language = str(args.melo_language).strip().upper() or "EN"
    melo_speaker = str(args.melo_speaker).strip() or "EN-US"
    kokoro_lang_code = normalize_kokoro_lang_code(args.kokoro_lang_code)
    kokoro_voice = str(args.kokoro_voice).strip() or default_kokoro_voice(kokoro_lang_code)
    tts_engine = str(args.tts_engine).strip().lower() or "melo"
    if tts_engine not in {"melo", "piper", "kokoro"}:
        tts_engine = "melo"
    piper_voice_id = str(args.piper_voice_id).strip()
    piper_speaker_id = args.piper_speaker_id
    fast_mode = bool(args.fast_mode)
    subtitle_preset = normalize_subtitle_preset(args.subtitle_preset)
    subtitle_position = normalize_subtitle_position(args.subtitle_position)
    subtitle_accent_color = normalize_subtitle_accent_color(args.subtitle_accent_color)
    subtitle_box_color = normalize_subtitle_box_color(args.subtitle_box_color, subtitle_accent_color)
    caption_font_scale = normalize_caption_font_scale(args.caption_font_scale, 0.9)
    subtitle_bold = bool(args.subtitle_bold)
    subtitle_outline = bool(args.subtitle_outline)
    return run_tui(
        prompt=prompt,
        content_mode=content_mode,
        asset_keywords=asset_keywords,
        news_feed_urls=news_feed_urls,
        project_dir=project_dir,
        minutes=minutes,
        resolution=resolution,
        burn_subtitles=True,
        subtitle_preset=subtitle_preset,
        subtitle_position=subtitle_position,
        subtitle_accent_color=subtitle_accent_color,
        subtitle_box_color=subtitle_box_color,
        caption_font_scale=caption_font_scale,
        subtitle_bold=subtitle_bold,
        subtitle_outline=subtitle_outline,
        voice_profile=voice_profile,
        voice_speed=voice_speed,
        script_tone=script_tone,
        target_audience=target_audience,
        hook_style=hook_style,
        narrative_mode=narrative_mode,
        example_density=example_density,
        asset_mode=asset_mode,
        image_motion_style=image_motion_style,
        melo_language=melo_language,
        melo_speaker=melo_speaker,
        kokoro_lang_code=kokoro_lang_code,
        kokoro_voice=kokoro_voice,
        tts_engine=tts_engine,
        piper_voice_id=piper_voice_id,
        piper_speaker_id=piper_speaker_id,
        fast_mode=fast_mode,
    )


def imagine_entry() -> int:
    from .tui import run_tui

    return run_tui(
        prompt="Autonomous cars",
        content_mode="explainer",
        asset_keywords=["cars", "roads"],
        news_feed_urls=[],
        project_dir=(Path.home() / ".imagine" / "projects").resolve(),
        minutes=2,
        resolution="1280x720",
        burn_subtitles=True,
        subtitle_preset="regular",
        subtitle_position="bottom",
        subtitle_accent_color="sunflower",
        subtitle_box_color="sunflower",
        caption_font_scale=0.9,
        subtitle_bold=True,
        subtitle_outline=True,
        voice_profile="calm-documentary",
        voice_speed=1.0,
        script_tone="conversational",
        target_audience="curious general audience",
        hook_style="surprising-fact",
        narrative_mode="story-led",
        example_density="balanced",
        asset_mode="prefer-video",
        image_motion_style="slow",
        melo_language="EN",
        melo_speaker="EN-US",
        kokoro_lang_code="en-us",
        kokoro_voice="af_heart",
        tts_engine="melo",
        piper_voice_id="",
        piper_speaker_id=None,
        fast_mode=False,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "run":
            return run_command(args)
        if args.command == "replace-clips":
            return replace_clips_command(args)
        if args.command == "regenerate-shot":
            return regenerate_shot_command(args)
        if args.command == "prepare-shot-candidates":
            return prepare_shot_candidates_command(args)
        if args.command == "inspect":
            return inspect_command(args)
        if args.command == "youtube-auth":
            return youtube_auth_command(args)
        if args.command == "youtube-draft":
            return youtube_draft_command(args)
        if args.command == "youtube-publish":
            return youtube_publish_command(args)
        if args.command == "voices":
            return voices_command(args)
        if args.command == "voice-ab":
            return voice_ab_command(args)
        if args.command == "tui":
            return tui_command(args)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
