from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-video-mvp",
        description="Local-first long-form explainer video generator MVP",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the full generation pipeline")
    run.add_argument("--prompt", required=True, help="Video prompt/topic")
    run.add_argument("--project-dir", required=True, help="Output project directory")
    run.add_argument("--minutes", type=int, default=5, help="Target duration in minutes (default: 5)")
    run.add_argument("--resolution", default="1280x720", help="Output resolution, default 1280x720")
    run.add_argument("--fps", type=int, default=30, help="Output frame rate")

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
        help="Fail the run if Ollama server/model is unavailable (no template fallback)",
    )

    run.add_argument(
        "--tts-engine",
        choices=["melo", "say"],
        default="melo",
        help="Narration engine",
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

    run.add_argument(
        "--strict-commercial-safe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable strict commercial-safe policy checks (default: true)",
    )
    run.add_argument(
        "--allow-system-tts",
        action="store_true",
        help="Allow macOS say fallback when strict mode is enabled",
    )

    run.add_argument("--pexels-api-key", default=os.environ.get("PEXELS_API_KEY"), help="Pexels API key")
    run.add_argument("--pixabay-api-key", default=os.environ.get("PIXABAY_API_KEY"), help="Pixabay API key")
    run.add_argument("--max-scenes", type=int, default=40, help="Max number of scenes")
    run.add_argument("--min-scene-seconds", type=float, default=5.0, help="Minimum seconds per scene")
    run.add_argument("--verbose", action="store_true", help="Verbose pipeline logs")

    inspect = subparsers.add_parser("inspect", help="Inspect latest run logs/report")
    inspect.add_argument("--project-dir", required=True, help="Project directory to inspect")

    return parser


def run_command(args: argparse.Namespace) -> int:
    width, height = _parse_resolution(args.resolution)
    caption_words_min = max(1, int(args.caption_words_min))
    caption_words_max = max(caption_words_min, int(args.caption_words_max))
    caption_min_seconds = max(0.2, float(args.caption_min_seconds))
    caption_max_seconds = max(caption_min_seconds, float(args.caption_max_seconds))

    config = PipelineConfig(
        prompt=args.prompt.strip(),
        project_dir=Path(args.project_dir).expanduser().resolve(),
        minutes=max(1, args.minutes),
        width=width,
        height=height,
        fps=max(1, args.fps),
        script_engine=args.script_engine,
        ollama_model=args.ollama_model,
        require_ollama=bool(args.require_ollama),
        tts_engine=args.tts_engine,
        caption_engine=args.caption_engine,
        caption_style=args.caption_style,
        burn_subtitles=bool(args.burn_subtitles),
        caption_words_min=caption_words_min,
        caption_words_max=caption_words_max,
        caption_max_chars=max(8, int(args.caption_max_chars)),
        caption_min_seconds=caption_min_seconds,
        caption_max_seconds=caption_max_seconds,
        strict_commercial_safe=bool(args.strict_commercial_safe),
        allow_system_tts=bool(args.allow_system_tts),
        pexels_api_key=args.pexels_api_key,
        pixabay_api_key=args.pixabay_api_key,
        voice_speed=max(0.5, min(2.0, float(args.voice_speed))),
        melo_language=args.melo_language,
        melo_speaker=args.melo_speaker,
        max_scenes=max(4, args.max_scenes),
        min_scene_seconds=max(1.0, float(args.min_scene_seconds)),
        verbose=bool(args.verbose),
    )

    pipeline = VideoPipeline(config)
    outputs = pipeline.run()

    print("\nPipeline completed successfully:\n")
    for key in (
        "project_dir",
        "script",
        "timeline",
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

    outputs = payload.get("outputs", {})
    if isinstance(outputs, dict) and outputs:
        print("- outputs:")
        for key, value in outputs.items():
            print(f"  - {key}: {value}")

    print(f"- report_file: {report_path}")
    print(f"- log_file: {log_path}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "run":
            return run_command(args)
        if args.command == "inspect":
            return inspect_command(args)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
