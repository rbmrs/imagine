from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .models import PipelineConfig
from .pipeline import VideoPipeline


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
    config.include_intro = True
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
        choices=["full", "draft", "review", "preview", "finalize"],
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
        "--asset-keywords",
        default="",
        help="Comma-separated keywords to constrain stock footage search queries",
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
        "--tts-engine",
        choices=["melo", "piper"],
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
        "--include-intro",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render intro card before main content (default: true)",
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
        choices=["minimal-clean", "cinematic-subtle", "brand-image-motion"],
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
        "--caption-engine",
        choices=["heuristic", "faster-whisper"],
        default="heuristic",
        help="Caption generation backend",
    )
    run.add_argument(
        "--caption-style",
        choices=["engagement", "line"],
        default="engagement",
        help="Subtitle style preset",
    )
    run.add_argument(
        "--burn-subtitles",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Burn subtitles into final MP4 (default: true)",
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
    run.add_argument("--pexels-api-key", default=os.environ.get("PEXELS_API_KEY"), help="Pexels API key")
    run.add_argument("--pixabay-api-key", default=os.environ.get("PIXABAY_API_KEY"), help="Pixabay API key")
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
    replace.add_argument("--pexels-api-key", default=os.environ.get("PEXELS_API_KEY"), help="Pexels API key")
    replace.add_argument("--pixabay-api-key", default=os.environ.get("PIXABAY_API_KEY"), help="Pixabay API key")
    replace.add_argument(
        "--require-external-assets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail replacement if any selected scene cannot resolve external stock assets",
    )
    replace.add_argument("--verbose", action="store_true", help="Verbose pipeline logs")

    inspect = subparsers.add_parser("inspect", help="Inspect latest run logs/report")
    inspect.add_argument("--project-dir", required=True, help="Project directory to inspect")

    voices = subparsers.add_parser("voices", help="List available local Melo voices")
    voices.add_argument("--melo-language", default="EN", help="Melo language code")

    voice_ab = subparsers.add_parser("voice-ab", help="Generate local A/B voice samples")
    voice_ab.add_argument("--project-dir", required=True, help="Project directory used as source and output root")
    voice_ab.add_argument("--source-file", help="Optional text file used as sample source")
    voice_ab.add_argument(
        "--speakers",
        nargs="+",
        default=["EN-US", "EN-Default", "EN-AU"],
        help="Speaker ids to compare",
    )
    voice_ab.add_argument("--sample-words", type=int, default=130, help="Approximate words used per sample")
    voice_ab.add_argument(
        "--voice-profile",
        choices=["calm-documentary", "balanced", "energetic-explainer"],
        default="calm-documentary",
        help="Narration pacing profile for all samples",
    )
    voice_ab.add_argument("--voice-speed", type=float, default=1.0, help="Base voice speed multiplier")
    voice_ab.add_argument("--melo-language", default="EN", help="Melo language code")
    voice_ab.add_argument("--output-dir", help="Optional custom output directory")
    voice_ab.add_argument("--verbose", action="store_true", help="Verbose logs")

    tui = subparsers.add_parser("tui", help="Open terminal UI for common workflows")
    tui.add_argument("--prompt", default="Autonomous cars", help="Initial prompt shown in the TUI")
    tui.add_argument(
        "--asset-keywords",
        default="cars, roads",
        help="Initial comma-separated asset keywords",
    )
    tui.add_argument(
        "--project-dir",
        default=str((Path.home() / ".imagine" / "projects").resolve()),
        help="Projects root directory used for auto-managed run workspaces",
    )
    tui.add_argument("--minutes", type=int, default=2, help="Initial target duration in minutes")
    tui.add_argument(
        "--voice-profile",
        choices=["calm-documentary", "balanced", "energetic-explainer"],
        default="calm-documentary",
        help="Initial voice profile",
    )
    tui.add_argument("--voice-speed", type=float, default=1.0, help="Initial voice speed")
    tui.add_argument("--melo-language", choices=["EN"], default="EN", help="Initial Melo language")
    tui.add_argument("--melo-speaker", default="EN-US", help="Initial Melo speaker")
    tui.add_argument("--tts-engine", choices=["melo", "piper"], default="melo", help="Initial TTS engine")
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
    caption_font_scale = max(0.65, min(1.4, float(args.caption_font_scale)))
    caption_bottom_ratio = max(0.02, min(0.2, float(args.caption_bottom_ratio)))
    duration_tolerance = max(0.05, min(0.6, float(args.duration_tolerance)))
    intro_seconds = max(0.0, float(args.intro_seconds))
    outro_seconds = max(0.0, float(args.outro_seconds))

    config = PipelineConfig(
        prompt=args.prompt.strip(),
        project_dir=Path(args.project_dir).expanduser().resolve(),
        asset_keywords=_parse_asset_keywords(str(args.asset_keywords)),
        fast_mode=bool(args.fast_mode),
        minutes=max(1, args.minutes),
        width=width,
        height=height,
        fps=max(1, args.fps),
        script_engine=args.script_engine,
        ollama_model=args.ollama_model,
        require_ollama=bool(args.require_ollama),
        tts_engine=args.tts_engine,
        piper_voice_id=(str(args.piper_voice_id).strip() or None),
        piper_speaker_id=args.piper_speaker_id,
        piper_model_url=(str(args.piper_model_url).strip() or None),
        piper_config_url=(str(args.piper_config_url).strip() or None),
        caption_engine=args.caption_engine,
        caption_style=args.caption_style,
        burn_subtitles=bool(args.burn_subtitles),
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
        pexels_api_key=args.pexels_api_key,
        pixabay_api_key=args.pixabay_api_key,
        require_external_assets=bool(args.require_external_assets),
        video_effects=args.video_effects,
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
    elif workflow_stage == "draft":
        outputs = pipeline.run_draft(prepare_scene_review=bool(args.prepare_scene_review))
        heading = "Draft stage completed successfully"
    elif workflow_stage == "review":
        review_json_path = Path(args.review_script_json).expanduser().resolve() if args.review_script_json else None
        outputs = pipeline.run_review(review_script_path=review_json_path)
        heading = "Review stage completed successfully"
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
        tts_engine="melo",
        caption_engine=_coerce_str(manifest_config.get("caption_engine"), "heuristic"),
        caption_style=_coerce_str(manifest_config.get("caption_style"), "engagement"),
        burn_subtitles=_coerce_bool(manifest_config.get("burn_subtitles"), True),
        strict_commercial_safe=_coerce_bool(manifest_config.get("strict_commercial_safe"), True),
        pexels_api_key=args.pexels_api_key,
        pixabay_api_key=args.pixabay_api_key,
        require_external_assets=bool(args.require_external_assets),
        video_effects=_coerce_str(manifest_config.get("video_effects"), "clean"),
        include_intro=_coerce_bool(manifest_config.get("include_intro"), True),
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
        verbose=bool(args.verbose),
    )
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
        "manifest",
        "run_log",
        "run_report",
    ):
        print(f"- {key}: {outputs[key]}")
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


def _load_melo_speakers(language: str) -> dict[str, int]:
    from melo.api import TTS  # type: ignore

    tts = TTS(language=language, device="auto")
    hps = getattr(tts, "hps", None)
    hps_data = getattr(hps, "data", None)
    return dict(getattr(hps_data, "spk2id", {}) or {})


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
    try:
        spk2id = _load_melo_speakers(args.melo_language)
    except Exception as exc:  # noqa: BLE001
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
        tts_engine="melo",
        caption_engine="heuristic",
        caption_style="engagement",
        burn_subtitles=True,
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
        melo_speaker=valid[0],
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
        print(f"  - {item['speaker']}: {item['file']} ({item['duration_seconds']}s)")
    return 0


def tui_command(args: argparse.Namespace) -> int:
    from .tui import run_tui

    prompt = str(args.prompt).strip() or "Your topic"
    asset_keywords = _parse_asset_keywords(str(args.asset_keywords))
    project_dir = Path(args.project_dir).expanduser().resolve()
    minutes = max(1, int(args.minutes))
    voice_profile = str(args.voice_profile).strip() or "calm-documentary"
    voice_speed = max(0.5, min(2.0, float(args.voice_speed)))
    melo_language = str(args.melo_language).strip().upper() or "EN"
    melo_speaker = str(args.melo_speaker).strip() or "EN-US"
    tts_engine = str(args.tts_engine).strip().lower() or "melo"
    if tts_engine not in {"melo", "piper"}:
        tts_engine = "melo"
    piper_voice_id = str(args.piper_voice_id).strip()
    piper_speaker_id = args.piper_speaker_id
    fast_mode = bool(args.fast_mode)
    return run_tui(
        prompt=prompt,
        asset_keywords=asset_keywords,
        project_dir=project_dir,
        minutes=minutes,
        voice_profile=voice_profile,
        voice_speed=voice_speed,
        melo_language=melo_language,
        melo_speaker=melo_speaker,
        tts_engine=tts_engine,
        piper_voice_id=piper_voice_id,
        piper_speaker_id=piper_speaker_id,
        fast_mode=fast_mode,
    )


def imagine_entry() -> int:
    from .tui import run_tui

    return run_tui(
        prompt="Autonomous cars",
        asset_keywords=["cars", "roads"],
        project_dir=(Path.home() / ".imagine" / "projects").resolve(),
        minutes=2,
        voice_profile="calm-documentary",
        voice_speed=1.0,
        melo_language="EN",
        melo_speaker="EN-US",
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
        if args.command == "inspect":
            return inspect_command(args)
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
