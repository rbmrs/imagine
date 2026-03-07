from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote_plus, urlparse

import requests

from .models import AssetRight, PipelineConfig, Scene, ScriptPlan, TimelineClip


FUNCTION_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "between",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "so",
    "than",
    "that",
    "the",
    "their",
    "there",
    "these",
    "this",
    "those",
    "to",
    "was",
    "were",
    "which",
    "while",
    "with",
    "without",
}

PIPER_VOICE_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "en_US-libritts-high",
        "speaker_id": 0,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
    },
    {
        "id": "en_US-libritts-high",
        "speaker_id": 120,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
    },
    {
        "id": "en_US-libritts-high",
        "speaker_id": 360,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
    },
    {
        "id": "en_US-libritts-high",
        "speaker_id": 700,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
    },
    {
        "id": "en_US-ljspeech-high",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high/en_US-ljspeech-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high/en_US-ljspeech-high.onnx.json?download=true",
    },
    {
        "id": "en_US-joe-medium",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx.json?download=true",
    },
    {
        "id": "en_US-john-medium",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/john/medium/en_US-john-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/john/medium/en_US-john-medium.onnx.json?download=true",
    },
    {
        "id": "en_US-norman-medium",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/norman/medium/en_US-norman-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/norman/medium/en_US-norman-medium.onnx.json?download=true",
    },
)


class AssetUniquenessError(RuntimeError):
    def __init__(self, message: str, *, shortfall_scene_ids: list[str], keywords: list[str]):
        super().__init__(message)
        self.shortfall_scene_ids = shortfall_scene_ids
        self.keywords = keywords


class VideoPipeline:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.paths = self._build_paths(config.project_dir)
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "local-video-mvp/0.1"})
        self.stage_times: dict[str, float] = {}
        self.warnings: list[str] = []
        self.caption_stats: dict[str, Any] = {}
        self.duration_stats: dict[str, Any] = {}
        self.pacing_stats: dict[str, Any] = {}
        self.asset_stats: dict[str, Any] = {}
        self.optimization_stats: dict[str, Any] = {}
        self.used_template_fallback = False
        self._intro_bookend_background: Path | None = None
        self._outro_bookend_background: Path | None = None
        self._bookend_logo_overlay: Path | None = None
        self._ffmpeg_drawtext_available: bool | None = None
        self._ffmpeg_subtitles_available: bool | None = None
        self._piper_command: list[str] | None = None
        self._ollama_ready = False
        self._started_at: dt.datetime | None = None
        self._finished_at: dt.datetime | None = None

    def _reset_run_state(self) -> None:
        self.stage_times = {}
        self.warnings = []
        self.caption_stats = {}
        self.duration_stats = {}
        self.pacing_stats = {}
        self.asset_stats = {}
        self.optimization_stats = {}
        self.optimization_stats["profile"] = {
            "fast_mode": bool(self.config.fast_mode),
            "minutes": self.config.minutes,
            "resolution": f"{self.config.width}x{self.config.height}",
            "fps": self.config.fps,
            "caption_engine": self.config.caption_engine,
            "burn_subtitles": self.config.burn_subtitles,
            "include_intro": self.config.include_intro,
            "include_outro": self.config.include_outro,
            "require_external_assets": self.config.require_external_assets,
            "video_effects": self.config.video_effects,
        }
        self.used_template_fallback = False
        self._piper_command = None

    def run(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")

            plan = self.run_draft_stage()
            reviewed_plan = self.run_review_stage(plan)
            preview_stage = self.run_preview_stage(reviewed_plan)
            outputs = self.run_finalize_stage(
                reviewed_plan,
                rights=preview_stage["rights"],
                timeline=preview_stage["timeline"],
            )

            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._write_run_report(status="success", outputs=outputs)
            self._log("Done")
            return outputs
        except Exception as exc:
            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._log_with_level(f"Pipeline failed: {exc}", level="ERROR")
            self._write_run_report(status="failed", outputs=outputs, error=str(exc))
            raise

    def run_draft_stage(self) -> ScriptPlan:
        plan = self._run_stage("script_plan", "Stage 1/7: Generating script plan", self._generate_script_plan)
        self._initialize_duration_stats(plan)
        plan = self._run_stage(
            "duration_preflight",
            "Stage 1.1/7: Aligning script length with target duration",
            lambda: self._ensure_minimum_script_length(plan),
        )
        self._write_json(self.paths["script"], plan.to_dict())

        adjust_passes = 0
        audio_duration = 0.0
        while True:
            narration_text = self._clean_narration_text(plan.narration_text())
            self._write_text(self.paths["narration_txt"], narration_text + "\n")

            def voice_stage() -> None:
                self._synthesize_narration(narration_text, self.paths["narration_raw"])
                self._normalize_audio(self.paths["narration_raw"], self.paths["narration_wav"])

            stage_key = "narration" if adjust_passes == 0 else f"narration_pass_{adjust_passes + 1}"
            stage_label = "Stage 2/7: Synthesizing narration audio"
            if adjust_passes > 0:
                stage_label = f"Stage 2/7: Re-synthesizing narration audio (pass {adjust_passes + 1})"
            self._run_stage(stage_key, stage_label, voice_stage)

            audio_duration = self._media_duration(self.paths["narration_wav"])
            self._rebalance_scene_durations(plan, audio_duration)
            self._update_duration_post_tts(plan, audio_duration, adjust_passes)
            self._update_pacing_post_tts(narration_text, audio_duration, adjust_passes)

            if self._duration_within_tolerance(audio_duration):
                break

            if self._duration_too_short(audio_duration) and adjust_passes < self.config.max_duration_adjust_passes:
                adjust_passes += 1
                self._warn(
                    f"Narration duration is {audio_duration:.1f}s, below tolerance floor. "
                    f"Expanding script and retrying (pass {adjust_passes})."
                )
                plan = self._run_stage(
                    f"duration_adjust_{adjust_passes}",
                    f"Stage 2/7: Expanding script for duration (pass {adjust_passes})",
                    lambda: self._expand_short_script(plan, audio_duration),
                )
                self._write_json(self.paths["script"], plan.to_dict())
                continue

            if self._duration_too_long(audio_duration) and adjust_passes < self.config.max_duration_adjust_passes:
                adjust_passes += 1
                self._warn(
                    f"Narration duration is {audio_duration:.1f}s, above tolerance ceiling. "
                    f"Compressing script and retrying (pass {adjust_passes})."
                )
                plan = self._run_stage(
                    f"duration_adjust_{adjust_passes}",
                    f"Stage 2/7: Compressing script for duration (pass {adjust_passes})",
                    lambda: self._compress_long_script(plan, audio_duration),
                )
                self._write_json(self.paths["script"], plan.to_dict())
                continue

            requested = self.duration_stats.get("requested_seconds", self.config.target_seconds())
            delta = audio_duration - float(requested)
            delta_pct = (delta / float(requested) * 100.0) if requested else 0.0
            self._warn(
                "Narration duration is outside tolerance but no more adjustment passes remain. "
                f"Proceeding with {audio_duration:.1f}s ({delta_pct:+.1f}%)."
            )
            break

        self._write_json(self.paths["script"], plan.to_dict())
        self._write_narration_state(plan)
        return plan

    def run_review_stage(self, plan: ScriptPlan) -> ScriptPlan:
        return plan

    def run_preview_stage(self, plan: ScriptPlan) -> dict[str, Any]:
        rights = self._run_stage("assets", "Stage 3/7: Resolving visual assets", lambda: self._resolve_assets(plan))
        self._write_json(self.paths["script"], plan.to_dict())
        self._prepare_bookend_backgrounds(plan)
        self._write_clip_catalog(plan, rights)

        self._ensure_captions(plan)
        timeline = self._ensure_timeline(plan)

        return {
            "rights": rights,
            "timeline": timeline,
        }

    def run_finalize_stage(self, plan: ScriptPlan, *, rights: list[AssetRight], timeline: list[TimelineClip]) -> dict[str, str]:
        def render_stage() -> None:
            if self._promote_preview_render_if_unchanged(timeline):
                return

            self._render_video(timeline, self.paths["narration_wav"], self.paths["final_mp4"])
            shutil.copy2(self.paths["captions"], self.paths["final_srt"])
            self.optimization_stats["render"] = {
                "mode": "rerendered-finalize",
                "reused_preview": False,
            }

        self._run_stage("render", "Stage 6/7: Rendering final video", render_stage)

        manifest = self._run_stage("manifest", "Stage 7/7: Writing rights manifest", lambda: self._build_manifest(plan, rights))
        self._write_json(self.paths["manifest"], manifest)

        return {
            "project_dir": str(self.config.project_dir.resolve()),
            "script": str(self.paths["script"].resolve()),
            "timeline": str(self.paths["timeline"].resolve()),
            "clip_catalog": str(self.paths["clip_catalog"].resolve()),
            "narration": str(self.paths["narration_wav"].resolve()),
            "captions": str(self.paths["captions"].resolve()),
            "captions_ass": str(self.paths["captions_ass"].resolve()),
            "final_mp4": str(self.paths["final_mp4"].resolve()),
            "final_srt": str(self.paths["final_srt"].resolve()),
            "manifest": str(self.paths["manifest"].resolve()),
            "run_log": str(self.paths["run_log"].resolve()),
            "run_report": str(self.paths["run_report"].resolve()),
        }

    def run_draft(self, *, prepare_scene_review: bool = False) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")
            plan = self.run_draft_stage()
            self._write_json(self.paths["approved_script"], plan.to_dict())

            if prepare_scene_review:
                stage = self.run_preview_stage(plan)
                rights = list(stage["rights"])
                self._write_json(self.paths["approved_script"], plan.to_dict())
                manifest = self._run_stage(
                    "manifest",
                    "Stage 6/7: Writing rights manifest",
                    lambda: self._build_manifest(plan, rights),
                )
                self._write_json(self.paths["manifest"], manifest)

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "approved_script": str(self.paths["approved_script"].resolve()),
                "narration_txt": str(self.paths["narration_txt"].resolve()),
                "narration": str(self.paths["narration_wav"].resolve()),
                "run_log": str(self.paths["run_log"].resolve()),
                "run_report": str(self.paths["run_report"].resolve()),
            }

            if prepare_scene_review:
                outputs["timeline"] = str(self.paths["timeline"].resolve())
                outputs["clip_catalog"] = str(self.paths["clip_catalog"].resolve())
                outputs["captions"] = str(self.paths["captions"].resolve())
                outputs["captions_ass"] = str(self.paths["captions_ass"].resolve())
                outputs["manifest"] = str(self.paths["manifest"].resolve())

            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._write_run_report(status="success", outputs=outputs)
            self._log("Done")
            return outputs
        except Exception as exc:
            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._log_with_level(f"Pipeline failed: {exc}", level="ERROR")
            self._write_run_report(status="failed", outputs=outputs, error=str(exc))
            raise

    def run_review(self, review_script_path: Path | None = None) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            plan = self._load_existing_script_plan()
            if review_script_path is not None:
                source_path = review_script_path.expanduser().resolve()
                if not source_path.exists():
                    raise RuntimeError(f"Review script file not found: {source_path}")
                payload = json.loads(source_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("Review script JSON must be an object")
                plan = self._normalize_script_plan(payload)

            self._write_json(self.paths["script"], plan.to_dict())
            self._write_json(self.paths["approved_script"], plan.to_dict())

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "approved_script": str(self.paths["approved_script"].resolve()),
                "run_log": str(self.paths["run_log"].resolve()),
                "run_report": str(self.paths["run_report"].resolve()),
            }

            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._write_run_report(status="success", outputs=outputs)
            self._log("Done")
            return outputs
        except Exception as exc:
            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._log_with_level(f"Pipeline failed: {exc}", level="ERROR")
            self._write_run_report(status="failed", outputs=outputs, error=str(exc))
            raise

    def run_preview(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")

            plan = self._load_preferred_script_plan()
            self._write_json(self.paths["script"], plan.to_dict())
            self._ensure_narration_for_plan(plan)

            rights = self._load_existing_rights()
            can_reuse_assets = bool(rights) and all(
                scene.asset_path and Path(scene.asset_path).exists() for scene in plan.scenes
            )

            if can_reuse_assets:
                self._prepare_bookend_backgrounds(plan)
                self._write_clip_catalog(plan, rights)

                self._ensure_captions(plan)
                timeline = self._ensure_timeline(plan)
            else:
                stage = self.run_preview_stage(plan)
                rights = list(stage["rights"])
                timeline = list(stage["timeline"])

            self._ensure_preview_render(timeline)
            manifest = self._run_stage(
                "manifest",
                "Stage 7/7: Writing rights manifest",
                lambda: self._build_manifest(plan, rights),
            )
            self._write_json(self.paths["manifest"], manifest)

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "approved_script": str(self.paths["approved_script"].resolve()) if self.paths["approved_script"].exists() else "",
                "timeline": str(self.paths["timeline"].resolve()),
                "clip_catalog": str(self.paths["clip_catalog"].resolve()),
                "narration": str(self.paths["narration_wav"].resolve()),
                "captions": str(self.paths["captions"].resolve()),
                "captions_ass": str(self.paths["captions_ass"].resolve()),
                "preview_mp4": str(self.paths["preview_mp4"].resolve()),
                "preview_srt": str(self.paths["preview_srt"].resolve()),
                "manifest": str(self.paths["manifest"].resolve()),
                "run_log": str(self.paths["run_log"].resolve()),
                "run_report": str(self.paths["run_report"].resolve()),
            }

            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._write_run_report(status="success", outputs=outputs)
            self._log("Done")
            return outputs
        except Exception as exc:
            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._log_with_level(f"Pipeline failed: {exc}", level="ERROR")
            self._write_run_report(status="failed", outputs=outputs, error=str(exc))
            raise

    def run_finalize(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")

            plan = self._load_preferred_script_plan()
            self._write_json(self.paths["script"], plan.to_dict())
            self._validate_scene_review_gate(plan)
            self._ensure_narration_for_plan(plan)

            rights = self._load_existing_rights()
            can_reuse_assets = bool(rights) and all(
                scene.asset_path and Path(scene.asset_path).exists() for scene in plan.scenes
            )

            if can_reuse_assets:
                self._prepare_bookend_backgrounds(plan)
                self._ensure_captions(plan)
                timeline = self._ensure_timeline(plan)
            else:
                stage = self.run_preview_stage(plan)
                rights = list(stage["rights"])
                timeline = list(stage["timeline"])

            outputs = self.run_finalize_stage(plan, rights=rights, timeline=timeline)

            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._write_run_report(status="success", outputs=outputs)
            self._log("Done")
            return outputs
        except Exception as exc:
            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._log_with_level(f"Pipeline failed: {exc}", level="ERROR")
            self._write_run_report(status="failed", outputs=outputs, error=str(exc))
            raise

    def _load_preferred_script_plan(self) -> ScriptPlan:
        approved_path = self.paths["approved_script"]
        if approved_path.exists():
            return self._load_existing_script_plan(approved_path)
        return self._load_existing_script_plan()

    def _ensure_narration_for_plan(self, plan: ScriptPlan) -> None:
        narration_wav = self.paths["narration_wav"]
        expected_hash = self._narration_text_hash(plan)
        if narration_wav.exists() and narration_wav.stat().st_size > 0:
            existing_hash = self._load_narration_state_hash()
            if existing_hash and existing_hash == expected_hash:
                return

            self._warn("Approved script changed since narration generation; re-synthesizing narration audio.")

        self._initialize_duration_stats(plan)
        narration_text = self._clean_narration_text(plan.narration_text())
        self._write_text(self.paths["narration_txt"], narration_text + "\n")

        def voice_stage() -> None:
            self._synthesize_narration(narration_text, self.paths["narration_raw"])
            self._normalize_audio(self.paths["narration_raw"], narration_wav)

        self._run_stage("narration", "Stage 2/7: Synthesizing narration audio", voice_stage)
        audio_duration = self._media_duration(narration_wav)
        self._rebalance_scene_durations(plan, audio_duration)
        self._update_duration_post_tts(plan, audio_duration, adjust_passes=0)
        self._update_pacing_post_tts(narration_text, audio_duration, adjust_passes=0)
        self._write_json(self.paths["script"], plan.to_dict())
        self._write_narration_state(plan)

    def _validate_scene_review_gate(self, plan: ScriptPlan) -> None:
        state_path = self.paths.get("scene_review_state")
        if not isinstance(state_path, Path) or not state_path.exists():
            return

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Scene review state is invalid: {exc}") from exc

        scenes_state = payload.get("scenes") if isinstance(payload, dict) else None
        if not isinstance(scenes_state, dict):
            raise RuntimeError("Scene review state is missing 'scenes' map.")

        blocked: list[str] = []
        for scene in plan.scenes:
            value = scenes_state.get(scene.scene_id)
            if not isinstance(value, dict):
                blocked.append(scene.scene_id)
                continue

            text_ok = bool(value.get("text_approved"))
            narration_ok = bool(value.get("narration_approved"))
            clip_ok = bool(value.get("clip_approved"))
            if not (text_ok and narration_ok and clip_ok):
                blocked.append(scene.scene_id)

        if blocked:
            hint = ", ".join(blocked[:8])
            raise RuntimeError(
                "Finalize blocked: not all scenes are approved in review state. "
                f"Pending scenes: {hint}"
            )

    def synthesize_scene_narration_preview(self, scene_id: str, text: str) -> dict[str, str]:
        cleaned_scene_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", scene_id.strip())
        if not cleaned_scene_id:
            cleaned_scene_id = "scene"

        narration_text = self._clean_narration_text(text)
        if not narration_text:
            raise RuntimeError("Scene narration text is empty after normalization.")

        self._prepare_dirs()
        self._require_binary("ffmpeg")

        scene_dir = self.paths["scene_narration_dir"]
        scene_dir.mkdir(parents=True, exist_ok=True)

        raw_path = scene_dir / f"{cleaned_scene_id}.raw.wav"
        wav_path = scene_dir / f"{cleaned_scene_id}.wav"

        self._synthesize_narration(narration_text, raw_path)
        self._normalize_audio(raw_path, wav_path)

        return {
            "scene_id": cleaned_scene_id,
            "raw_path": str(raw_path.resolve()),
            "wav_path": str(wav_path.resolve()),
        }

    def _narration_text_hash(self, plan: ScriptPlan) -> str:
        joined = self._clean_narration_text(plan.narration_text())
        digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        return digest

    def _load_narration_state_hash(self) -> str | None:
        state_path = self.paths.get("narration_state")
        if not isinstance(state_path, Path) or not state_path.exists():
            return None

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        value = payload.get("narration_text_sha256")
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        if not value:
            return None
        return value

    def _write_narration_state(self, plan: ScriptPlan) -> None:
        payload = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "narration_text_sha256": self._narration_text_hash(plan),
            "narration_file": str(self.paths["narration_wav"].resolve()),
            "script_file": str(self.paths["script"].resolve()),
        }
        self._write_json(self.paths["narration_state"], payload)

    def _ensure_captions(self, plan: ScriptPlan) -> None:
        signature = self._captions_signature()
        state = self._load_json_state(self.paths["captions_state"])
        captions_path = self.paths["captions"]
        captions_ass_path = self.paths["captions_ass"]

        can_reuse = (
            isinstance(state, dict)
            and str(state.get("captions_signature") or "") == signature
            and captions_path.exists()
            and captions_path.stat().st_size > 0
            and captions_ass_path.exists()
            and captions_ass_path.stat().st_size > 0
        )
        if can_reuse:
            self._log("Stage 4/7: Reusing captions")
            self.stage_times["captions"] = 0.0
            self._log("captions completed in 0.00s")
            cached_stats = state.get("caption_stats") if isinstance(state, dict) else None
            if isinstance(cached_stats, dict):
                self.caption_stats = dict(cached_stats)
            self.optimization_stats["captions"] = {
                "mode": "reused",
                "captions_signature": signature,
            }
            return

        captions = self._run_stage(
            "captions",
            "Stage 4/7: Building captions",
            lambda: self._generate_captions(plan, self.paths["narration_wav"]),
        )
        intro_shift = self._intro_bookend_seconds()
        if intro_shift > 0.0:
            captions = self._shift_captions(captions, intro_shift)
        self._write_srt(captions_path, captions)
        self._write_ass(captions_ass_path, captions)
        self._write_json(
            self.paths["captions_state"],
            {
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "captions_signature": signature,
                "captions_file": str(captions_path.resolve()),
                "captions_ass_file": str(captions_ass_path.resolve()),
                "caption_stats": dict(self.caption_stats),
            },
        )
        self.optimization_stats["captions"] = {
            "mode": "generated",
            "captions_signature": signature,
        }

    def _ensure_timeline(self, plan: ScriptPlan) -> list[TimelineClip]:
        signature = self._timeline_signature(plan)
        state = self._load_json_state(self.paths["timeline_state"])
        can_reuse = isinstance(state, dict) and str(state.get("timeline_signature") or "") == signature
        if can_reuse:
            timeline = self._load_timeline_from_json(self.paths["timeline"])
            if timeline:
                self._log("Stage 5/7: Reusing timeline")
                self.stage_times["timeline"] = 0.0
                self._log("timeline completed in 0.00s")
                self.optimization_stats["timeline"] = {
                    "mode": "reused",
                    "timeline_signature": signature,
                }
                return timeline

        timeline = self._run_stage("timeline", "Stage 5/7: Building timeline", lambda: self._build_timeline(plan))
        self._write_timeline_json(plan, timeline)
        self._write_json(
            self.paths["timeline_state"],
            {
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "timeline_signature": signature,
                "timeline_file": str(self.paths["timeline"].resolve()),
                "clip_count": len(timeline),
            },
        )
        self.optimization_stats["timeline"] = {
            "mode": "generated",
            "timeline_signature": signature,
        }
        return timeline

    def _ensure_preview_render(self, timeline: list[TimelineClip]) -> None:
        render_signature = self._render_signature(timeline)
        if self._preview_render_cache_valid(render_signature):
            preview_srt = self.paths["preview_srt"]
            if not preview_srt.exists() and self.paths["captions"].exists():
                shutil.copy2(self.paths["captions"], preview_srt)
            self._log("Stage 6/7: Reusing preview video")
            self.stage_times["preview_render"] = 0.0
            self._log("preview_render completed in 0.00s")
            self.optimization_stats["preview_render"] = {
                "mode": "reused",
                "render_signature": render_signature,
            }
            return

        def render_preview_stage() -> None:
            self._render_video(timeline, self.paths["narration_wav"], self.paths["preview_mp4"])
            shutil.copy2(self.paths["captions"], self.paths["preview_srt"])

        self._run_stage("preview_render", "Stage 6/7: Rendering preview video", render_preview_stage)
        self._write_json(
            self.paths["preview_render_state"],
            {
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "render_signature": render_signature,
                "preview_mp4": str(self.paths["preview_mp4"].resolve()),
                "preview_srt": str(self.paths["preview_srt"].resolve()),
            },
        )
        self.optimization_stats["preview_render"] = {
            "mode": "generated",
            "render_signature": render_signature,
        }

    def _promote_preview_render_if_unchanged(self, timeline: list[TimelineClip]) -> bool:
        render_signature = self._render_signature(timeline)
        if not self._preview_render_cache_valid(render_signature):
            return False

        preview_mp4 = self.paths["preview_mp4"]
        preview_srt = self.paths["preview_srt"]
        final_mp4 = self.paths["final_mp4"]
        final_srt = self.paths["final_srt"]
        captions = self.paths["captions"]

        if not preview_mp4.exists() or preview_mp4.stat().st_size == 0:
            return False

        srt_source: Path | None = None
        if preview_srt.exists() and preview_srt.stat().st_size > 0:
            srt_source = preview_srt
        elif captions.exists() and captions.stat().st_size > 0:
            srt_source = captions
        if srt_source is None:
            return False

        shutil.copy2(preview_mp4, final_mp4)
        shutil.copy2(srt_source, final_srt)

        self._log("Stage 6/7: Reusing approved preview as final output")
        self.optimization_stats["render"] = {
            "mode": "reused-preview",
            "reused_preview": True,
            "render_signature": render_signature,
        }
        return True

    def _captions_signature(self) -> str:
        payload = {
            "narration_sha256": self._safe_file_sha256(self.paths["narration_wav"]),
            "caption_engine": self.config.caption_engine,
            "caption_style": self.config.caption_style,
            "caption_words_min": self.config.caption_words_min,
            "caption_words_max": self.config.caption_words_max,
            "caption_max_chars": self.config.caption_max_chars,
            "caption_min_seconds": self.config.caption_min_seconds,
            "caption_max_seconds": self.config.caption_max_seconds,
            "caption_font_scale": self.config.caption_font_scale,
            "caption_bottom_ratio": self.config.caption_bottom_ratio,
            "intro_seconds": self._intro_bookend_seconds(),
        }
        return self._stable_payload_hash(payload)

    def _timeline_signature(self, plan: ScriptPlan) -> str:
        payload = {
            "plan": plan.to_dict(),
            "include_intro": self.config.include_intro,
            "include_outro": self.config.include_outro,
            "intro_seconds": self.config.intro_seconds,
            "outro_seconds": self.config.outro_seconds,
            "outro_text": self.config.outro_text,
        }
        return self._stable_payload_hash(payload)

    def _render_signature(self, timeline: list[TimelineClip]) -> str:
        payload = {
            "clips": [clip.to_dict() for clip in timeline],
            "narration_sha256": self._safe_file_sha256(self.paths["narration_wav"]),
            "captions_ass_sha256": self._safe_file_sha256(self.paths["captions_ass"]) if self.config.burn_subtitles else "",
            "render": {
                "fast_mode": self.config.fast_mode,
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "video_effects": self.config.video_effects,
                "burn_subtitles": self.config.burn_subtitles,
                "include_intro": self.config.include_intro,
                "include_outro": self.config.include_outro,
                "intro_seconds": self.config.intro_seconds,
                "outro_seconds": self.config.outro_seconds,
                "outro_text": self.config.outro_text,
                "channel_name": self.config.channel_name,
                "intro_tagline": self.config.intro_tagline,
                "outro_tagline": self.config.outro_tagline,
                "bookend_style": self.config.bookend_style,
                "brand_logo_path": self.config.brand_logo_path,
                "brand_intro_image_path": self.config.brand_intro_image_path,
                "brand_outro_image_path": self.config.brand_outro_image_path,
                "brand_use_scene_fallback": self.config.brand_use_scene_fallback,
            },
        }
        return self._stable_payload_hash(payload)

    def _preview_render_cache_valid(self, render_signature: str) -> bool:
        state = self._load_json_state(self.paths["preview_render_state"])
        if not isinstance(state, dict):
            return False
        if str(state.get("render_signature") or "") != render_signature:
            return False

        preview_mp4 = self.paths["preview_mp4"]
        return preview_mp4.exists() and preview_mp4.stat().st_size > 0

    def _load_timeline_from_json(self, timeline_path: Path) -> list[TimelineClip]:
        if not timeline_path.exists():
            return []

        try:
            payload = json.loads(timeline_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        raw_clips = payload.get("clips") if isinstance(payload, dict) else None
        if not isinstance(raw_clips, list):
            return []

        clips: list[TimelineClip] = []
        for item in raw_clips:
            if not isinstance(item, dict):
                continue
            scene_id = str(item.get("scene_id") or "").strip()
            clip_name = str(item.get("clip_name") or "").strip()
            heading = str(item.get("heading") or "").strip()
            if not scene_id or not clip_name:
                continue
            try:
                start = float(item.get("start") or 0.0)
                end = float(item.get("end") or 0.0)
                seconds = float(item.get("seconds") or max(0.0, end - start))
            except (TypeError, ValueError):
                continue
            source_raw = item.get("source_path")
            source_path = str(source_raw).strip() if source_raw is not None else None
            clips.append(
                TimelineClip(
                    scene_id=scene_id,
                    clip_name=clip_name,
                    start=start,
                    end=end,
                    seconds=seconds,
                    source_path=source_path or None,
                    heading=heading,
                )
            )
        return clips

    def _write_timeline_json(self, plan: ScriptPlan, timeline: list[TimelineClip]) -> None:
        self._write_json(
            self.paths["timeline"],
            {
                "title": plan.title,
                "summary": plan.summary,
                "clips": [clip.to_dict() for clip in timeline],
                "total_seconds": round(sum(clip.seconds for clip in timeline), 3),
            },
        )

    def _safe_file_sha256(self, path: Path) -> str:
        if not path.exists() or path.stat().st_size <= 0:
            return ""
        try:
            return self._file_sha256(path)
        except Exception:
            return ""

    def _stable_payload_hash(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_json_state(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def replace_clips_by_name(self, clip_names: list[str]) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._require_binary("ffmpeg")
            self._require_binary("ffprobe")
            self._validate_render_filter_requirements()

            plan = self._run_stage(
                "load_script",
                "Stage 1/5: Loading existing script",
                self._load_existing_script_plan,
            )

            requested_names = {str(name).strip().lower() for name in clip_names if str(name).strip()}
            if not requested_names:
                raise RuntimeError("No clip names were provided for replacement.")

            target_scenes: list[Scene] = []
            for scene in plan.scenes:
                if scene.clip_name.strip().lower() in requested_names or scene.scene_id.strip().lower() in requested_names:
                    target_scenes.append(scene)

            if not target_scenes:
                raise RuntimeError("None of the requested clip names were found in script.json.")

            existing_rights = self._load_existing_rights()
            target_scene_ids = {scene.scene_id for scene in target_scenes}
            keep_rights = [right for right in existing_rights if right.scene_id not in target_scene_ids]

            used_asset_keys = {self._asset_uniqueness_key_from_right(right) for right in keep_rights}
            rejected_keys = {self._asset_uniqueness_key_from_right(right) for right in existing_rights}
            used_asset_keys.update(rejected_keys)

            for scene in target_scenes:
                scene.asset_path = None
                scene.asset_provider = None

            self._log(
                "Replacing clips: "
                + ", ".join(scene.clip_name for scene in target_scenes)
            )

            replacement_rights = self._run_stage(
                "assets",
                "Stage 2/5: Resolving replacement assets",
                lambda: self._resolve_assets(
                    plan,
                    scenes=target_scenes,
                    preused_asset_keys=used_asset_keys,
                ),
            )

            rights_by_scene: dict[str, AssetRight] = {right.scene_id: right for right in keep_rights}
            for right in replacement_rights:
                rights_by_scene[right.scene_id] = right
            merged_rights = [rights_by_scene[scene.scene_id] for scene in plan.scenes if scene.scene_id in rights_by_scene]

            self._write_json(self.paths["script"], plan.to_dict())
            self._prepare_bookend_backgrounds(plan)
            self._write_clip_catalog(plan, merged_rights)

            narration_wav = self.paths["narration_wav"]
            if not narration_wav.exists():
                raise RuntimeError(
                    f"Missing narration audio: {narration_wav}. Run full generation first before replacing clips."
                )

            captions_srt = self.paths["captions"]
            if not captions_srt.exists():
                raise RuntimeError(
                    f"Missing captions file: {captions_srt}. Run full generation first before replacing clips."
                )

            timeline = self._run_stage("timeline", "Stage 3/5: Rebuilding timeline", lambda: self._build_timeline(plan))
            self._write_json(
                self.paths["timeline"],
                {
                    "title": plan.title,
                    "summary": plan.summary,
                    "clips": [clip.to_dict() for clip in timeline],
                    "total_seconds": round(sum(clip.seconds for clip in timeline), 3),
                },
            )

            def render_stage() -> None:
                self._render_video(timeline, narration_wav, self.paths["final_mp4"])
                shutil.copy2(captions_srt, self.paths["final_srt"])

            self._run_stage("render", "Stage 4/5: Rendering updated video", render_stage)

            manifest = self._run_stage(
                "manifest",
                "Stage 5/5: Writing rights manifest",
                lambda: self._build_manifest(plan, merged_rights),
            )
            self._write_json(self.paths["manifest"], manifest)

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "timeline": str(self.paths["timeline"].resolve()),
                "clip_catalog": str(self.paths["clip_catalog"].resolve()),
                "narration": str(self.paths["narration_wav"].resolve()),
                "captions": str(self.paths["captions"].resolve()),
                "captions_ass": str(self.paths["captions_ass"].resolve()),
                "final_mp4": str(self.paths["final_mp4"].resolve()),
                "final_srt": str(self.paths["final_srt"].resolve()),
                "manifest": str(self.paths["manifest"].resolve()),
                "run_log": str(self.paths["run_log"].resolve()),
                "run_report": str(self.paths["run_report"].resolve()),
            }

            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._write_run_report(status="success", outputs=outputs)
            self._log("Done")
            return outputs
        except Exception as exc:
            self._finished_at = dt.datetime.now(dt.timezone.utc)
            self._log_with_level(f"Pipeline failed: {exc}", level="ERROR")
            self._write_run_report(status="failed", outputs=outputs, error=str(exc))
            raise

    def _build_paths(self, project_dir: Path) -> dict[str, Path]:
        return {
            "root": project_dir,
            "assets_cache": project_dir / "assets" / "cache",
            "review": project_dir / "review",
            "tmp": project_dir / "tmp",
            "output": project_dir / "output",
            "prompt": project_dir / "prompt.txt",
            "script": project_dir / "script.json",
            "narration_txt": project_dir / "narration.txt",
            "narration_raw": project_dir / "narration.raw.wav",
            "narration_wav": project_dir / "narration.wav",
            "captions": project_dir / "captions.srt",
            "captions_ass": project_dir / "captions.ass",
            "timeline": project_dir / "timeline.json",
            "clip_catalog": project_dir / "review" / "clip_catalog.json",
            "approved_script": project_dir / "review" / "script_approved.json",
            "scene_review_state": project_dir / "review" / "scene_review_state.json",
            "narration_state": project_dir / "review" / "narration_state.json",
            "captions_state": project_dir / "review" / "captions_state.json",
            "timeline_state": project_dir / "review" / "timeline_state.json",
            "preview_render_state": project_dir / "review" / "preview_render_state.json",
            "preview_mp4": project_dir / "review" / "preview.mp4",
            "preview_srt": project_dir / "review" / "preview.srt",
            "scene_narration_dir": project_dir / "review" / "narration" / "scenes",
            "manifest": project_dir / "rights_manifest.json",
            "run_log": project_dir / "run.log",
            "run_report": project_dir / "run_report.json",
            "final_mp4": project_dir / "output" / "final.mp4",
            "final_srt": project_dir / "output" / "final.srt",
        }

    def _prepare_dirs(self) -> None:
        for key in ("root", "assets_cache", "review", "tmp", "output"):
            self.paths[key].mkdir(parents=True, exist_ok=True)

    def _check_dependencies(self) -> None:
        self._require_binary("ffmpeg")
        self._require_binary("ffprobe")
        self._validate_render_filter_requirements()
        if self.config.script_engine == "ollama":
            self._require_binary("ollama")
            self._ollama_ready = self._ollama_server_ready()
            if not self._ollama_ready:
                raise RuntimeError(
                    "Ollama is unavailable. Start it with 'ollama serve' or switch to --script-engine template."
                )
        if self.config.tts_engine == "piper":
            command = self._resolve_piper_command()
            if command is None:
                raise RuntimeError(
                    "Piper runtime not found. Install with: python -m pip install piper-tts"
                )

    def _require_binary(self, binary_name: str) -> None:
        if shutil.which(binary_name) is None:
            raise RuntimeError(f"Missing required binary: {binary_name}")

    def _validate_render_filter_requirements(self) -> None:
        requires_bookend_text = self._intro_bookend_seconds() > 0.0 or self._outro_bookend_seconds() > 0.0
        if requires_bookend_text and not self._ffmpeg_supports_drawtext():
            raise RuntimeError(
                "ffmpeg drawtext filter is required for intro/outro text rendering. "
                "Install an ffmpeg build with drawtext support or disable intro/outro bookends."
            )

        if self.config.burn_subtitles and not self._ffmpeg_supports_subtitles_filter():
            raise RuntimeError(
                "ffmpeg subtitles filter is required for burned subtitles. "
                "Install an ffmpeg build with libass/subtitles support or disable burned subtitles."
            )

    def _generate_script_plan(self) -> ScriptPlan:
        raw_plan: dict[str, Any] | None = None
        if self.config.script_engine == "ollama" and self._ollama_ready:
            raw_plan = self._generate_script_plan_ollama()
            if raw_plan is None:
                raise RuntimeError(
                    "Ollama script generation failed. Fix the Ollama model/server or switch to --script-engine template."
                )

        if raw_plan is None:
            self.used_template_fallback = True
            raw_plan = self._generate_script_plan_template()

        return self._normalize_script_plan(raw_plan)

    def _generate_script_plan_ollama(self) -> dict[str, Any] | None:
        scene_target = min(self.config.max_scenes, max(14, self.config.minutes * 5))
        prompt = textwrap.dedent(
            f"""
            You are a script planner for faceless YouTube explainers.
            Return JSON only. Do not include markdown.

            Requirements:
            - topic: {self.config.prompt}
            - output duration target: {self.config.minutes} minutes
            - style: clear, educational, retention-focused
            - audience: general technical audience
            - scene count target: around {scene_target}
            - no host or face references

            JSON schema:
            {{
              "title": "string",
              "summary": "1-2 sentence summary",
              "scenes": [
                {{
                  "heading": "short heading",
                  "voiceover": "narration for this scene",
                  "search_terms": ["keyword 1", "keyword 2", "keyword 3"]
                }}
              ]
            }}

            Ensure each scene voiceover is 1-3 sentences.
            Keep all claims practical and avoid speculative statements.
            """
        ).strip()

        result = self._run_command(
            ["ollama", "run", self.config.ollama_model, prompt],
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                self._warn(f"Ollama error: {stderr}")
            return None

        parsed = self._extract_json_object(result.stdout)
        if parsed is None:
            return None
        return parsed

    def _generate_script_plan_template(self) -> dict[str, Any]:
        scene_count = min(self.config.max_scenes, max(14, self.config.minutes * 5))
        section_heads = [
            "Hook",
            "What This Is",
            "Why It Matters",
            "Core Mechanic",
            "Detailed Breakdown",
            "Real-World Example",
            "Common Mistakes",
            "Practical Checklist",
            "Wrap Up",
        ]
        voice_templates = [
            "Open with a plain-language setup of {topic}. Clarify what the viewer should understand by the end of this section.",
            "Define the core idea behind {topic} with one concrete analogy. Keep the explanation practical and direct.",
            "Explain why {topic} matters in real projects, including one common scenario where this concept changes decisions.",
            "Break this part into steps: what happens first, what happens next, and what outcome the viewer should expect.",
            "Use a realistic example to show how {topic} works under constraints like budget, time, or data quality.",
            "Highlight the most frequent mistakes in this area and explain how to avoid them with a simple checklist.",
            "Compare two approaches to {topic}, then explain when each approach is a better fit.",
            "Translate this section into actionable guidance that a beginner can apply immediately after watching.",
            "Summarize the key takeaway and connect it to the next section so the narrative remains continuous.",
        ]

        scenes: list[dict[str, Any]] = []
        for idx in range(scene_count):
            head = section_heads[idx % len(section_heads)]
            heading = f"{head} {idx + 1}"
            voiceover = voice_templates[idx % len(voice_templates)].format(topic=self.config.prompt)
            search_terms = self._default_search_terms(heading)
            scenes.append(
                {
                    "heading": heading,
                    "voiceover": voiceover,
                    "search_terms": search_terms,
                }
            )

        return {
            "title": f"Explainer: {self.config.prompt}",
            "summary": f"A practical long-form explainer about {self.config.prompt}.",
            "scenes": scenes,
        }

    def _normalize_script_plan(self, raw_plan: dict[str, Any]) -> ScriptPlan:
        title = str(raw_plan.get("title") or f"Explainer: {self.config.prompt}").strip()
        summary = str(raw_plan.get("summary") or "").strip() or f"Long-form explainer on {self.config.prompt}."
        raw_scenes = raw_plan.get("scenes")
        if not isinstance(raw_scenes, list):
            raw_scenes = []

        scenes: list[Scene] = []
        scene_limit = max(1, self.config.max_scenes)
        used_clip_names: set[str] = set()

        for idx, item in enumerate(raw_scenes[:scene_limit]):
            if not isinstance(item, dict):
                continue

            heading = str(item.get("heading") or f"Scene {idx + 1}").strip()
            voiceover = str(item.get("voiceover") or "").strip()
            if not voiceover:
                continue

            raw_terms = item.get("search_terms")
            if isinstance(raw_terms, list):
                search_terms = [str(term).strip() for term in raw_terms if str(term).strip()]
            else:
                search_terms = []
            if not search_terms:
                search_terms = self._default_search_terms(heading)

            clip_name = self._build_scene_clip_name(idx=idx, heading=heading, used=used_clip_names)

            scenes.append(
                Scene(
                    scene_id=f"scene_{idx + 1:03d}",
                    clip_name=clip_name,
                    heading=heading,
                    voiceover=voiceover,
                    search_terms=search_terms[:4],
                    seconds=0.0,
                )
            )

        if not scenes:
            if self.config.script_engine == "ollama":
                raise RuntimeError("Ollama returned an invalid script plan with no usable scenes.")
            fallback = self._generate_script_plan_template()
            return self._normalize_script_plan(fallback)

        # Set initial equal durations before voiceover timing rebalance.
        per_scene = max(self.config.min_scene_seconds, self.config.target_seconds() / len(scenes))
        for scene in scenes:
            scene.seconds = per_scene

        return ScriptPlan(title=title, summary=summary, scenes=scenes)

    def _build_scene_clip_name(self, idx: int, heading: str, used: set[str]) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", heading.strip().lower())
        slug = slug.strip("-")
        if not slug:
            slug = "scene"

        parts = [part for part in slug.split("-") if part]
        base = "-".join(parts[:6]).strip("-")
        if not base:
            base = "scene"
        if len(base) > 48:
            base = base[:48].rstrip("-")
        if not base:
            base = "scene"

        prefix = f"{idx + 1:02d}"
        candidate = f"{prefix}-{base}"
        suffix = 2
        while candidate in used:
            candidate = f"{prefix}-{base}-{suffix}"
            suffix += 1

        used.add(candidate)
        return candidate

    def _load_existing_script_plan(self, script_path: Path | None = None) -> ScriptPlan:
        source_path = script_path.expanduser().resolve() if script_path is not None else self.paths["script"]
        if not source_path.exists():
            raise RuntimeError(f"script.json not found at {source_path}")

        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("script.json is invalid: expected top-level JSON object")

        title = str(payload.get("title") or f"Explainer: {self.config.prompt}").strip()
        summary = str(payload.get("summary") or "").strip() or f"Long-form explainer on {self.config.prompt}."

        raw_scenes = payload.get("scenes")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise RuntimeError("script.json has no scenes to review")

        scenes: list[Scene] = []
        used_clip_names: set[str] = set()
        for idx, item in enumerate(raw_scenes):
            if not isinstance(item, dict):
                continue

            scene_id = str(item.get("scene_id") or f"scene_{idx + 1:03d}").strip()
            if not scene_id:
                scene_id = f"scene_{idx + 1:03d}"

            heading = str(item.get("heading") or f"Scene {idx + 1}").strip()
            voiceover = str(item.get("voiceover") or "").strip()

            raw_terms = item.get("search_terms")
            if isinstance(raw_terms, list):
                search_terms = [str(term).strip() for term in raw_terms if str(term).strip()]
            else:
                search_terms = []
            if not search_terms:
                search_terms = self._default_search_terms(heading)

            try:
                seconds = float(item.get("seconds") or 0.0)
            except Exception:
                seconds = 0.0
            seconds = max(0.2, seconds)

            clip_name_raw = str(item.get("clip_name") or "").strip().lower()
            if not clip_name_raw:
                clip_name = self._build_scene_clip_name(idx=idx, heading=heading, used=used_clip_names)
            else:
                clip_name = clip_name_raw
                suffix = 2
                while clip_name in used_clip_names:
                    clip_name = f"{clip_name_raw}-{suffix}"
                    suffix += 1
                used_clip_names.add(clip_name)

            asset_path_raw = str(item.get("asset_path") or "").strip()
            asset_provider_raw = str(item.get("asset_provider") or "").strip()

            scenes.append(
                Scene(
                    scene_id=scene_id,
                    clip_name=clip_name,
                    heading=heading,
                    voiceover=voiceover,
                    search_terms=search_terms[:4],
                    seconds=seconds,
                    asset_path=asset_path_raw or None,
                    asset_provider=asset_provider_raw or None,
                )
            )

        if not scenes:
            raise RuntimeError("script.json has no valid scenes to process")

        return ScriptPlan(title=title, summary=summary, scenes=scenes)

    def _load_existing_rights(self) -> list[AssetRight]:
        manifest_path = self.paths["manifest"]
        if not manifest_path.exists():
            return []

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._warn(f"Could not parse existing rights manifest: {exc}")
            return []

        assets = payload.get("assets") if isinstance(payload, dict) else None
        if not isinstance(assets, list):
            return []

        rights: list[AssetRight] = []
        for item in assets:
            if not isinstance(item, dict):
                continue

            scene_id = str(item.get("scene_id") or "").strip()
            source_platform = str(item.get("source_platform") or "").strip()
            source_url = str(item.get("source_url") or "").strip()
            local_path = str(item.get("local_path") or "").strip()
            sha256 = str(item.get("sha256") or "").strip()
            downloaded_at = str(item.get("downloaded_at") or "").strip()
            if not (scene_id and source_platform and source_url and local_path and sha256):
                continue

            restriction_flags_raw = item.get("restriction_flags")
            if isinstance(restriction_flags_raw, list):
                restriction_flags = [str(flag) for flag in restriction_flags_raw]
            else:
                restriction_flags = []

            rights.append(
                AssetRight(
                    scene_id=scene_id,
                    source_platform=source_platform,
                    source_asset_id=(str(item.get("source_asset_id") or "").strip() or None),
                    source_url=source_url,
                    creator_name=(str(item.get("creator_name") or "").strip() or None),
                    creator_profile_url=(str(item.get("creator_profile_url") or "").strip() or None),
                    license_name=(str(item.get("license_name") or "").strip() or None),
                    license_url=(str(item.get("license_url") or "").strip() or None),
                    downloaded_at=downloaded_at,
                    local_path=local_path,
                    sha256=sha256,
                    restriction_flags=restriction_flags,
                    attribution_required=bool(item.get("attribution_required", False)),
                    attribution_text=(str(item.get("attribution_text") or "").strip() or None),
                )
            )

        return rights

    def _duration_bounds(self) -> tuple[float, float, float]:
        target_seconds = float(self.config.target_seconds())
        tolerance = max(0.0, float(self.config.duration_tolerance_ratio))
        min_seconds = max(1.0, target_seconds * (1.0 - tolerance))
        max_seconds = max(min_seconds, target_seconds * (1.0 + tolerance))
        return target_seconds, min_seconds, max_seconds

    def _word_count_plan(self, plan: ScriptPlan) -> int:
        total = 0
        for scene in plan.scenes:
            total += len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", scene.voiceover or ""))
        return total

    def _estimate_seconds_from_words(self, words: int) -> float:
        wpm = max(1, int(self.config.target_speech_wpm))
        return (float(words) / float(wpm)) * 60.0

    def _initialize_duration_stats(self, plan: ScriptPlan) -> None:
        target_seconds, min_seconds, max_seconds = self._duration_bounds()
        words = self._word_count_plan(plan)
        self.duration_stats = {
            "requested_seconds": round(target_seconds, 3),
            "min_seconds": round(min_seconds, 3),
            "max_seconds": round(max_seconds, 3),
            "tolerance_ratio": round(float(self.config.duration_tolerance_ratio), 4),
            "target_wpm": int(self.config.target_speech_wpm),
            "initial_word_count": words,
            "estimated_seconds_pre_tts": round(self._estimate_seconds_from_words(words), 3),
            "preflight_adjustments": 0,
            "post_tts_adjustments": 0,
        }

    def _ensure_minimum_script_length(self, plan: ScriptPlan) -> ScriptPlan:
        target_seconds, min_seconds, _ = self._duration_bounds()
        current_words = self._word_count_plan(plan)
        min_words = int(math.ceil((min_seconds / 60.0) * max(1, self.config.target_speech_wpm)))
        target_words = int(math.ceil((target_seconds / 60.0) * max(1, self.config.target_speech_wpm)))

        if current_words >= min_words:
            self.duration_stats["word_count_after_preflight"] = current_words
            self.duration_stats["estimated_seconds_after_preflight"] = round(
                self._estimate_seconds_from_words(current_words),
                3,
            )
            return plan

        self._warn(
            f"Script is short ({current_words} words) for requested duration. "
            f"Expanding to roughly {target_words} words."
        )
        expanded = self._expand_plan_to_target_words(plan, target_words)
        final_words = self._word_count_plan(expanded)
        self.duration_stats["preflight_adjustments"] = int(self.duration_stats.get("preflight_adjustments", 0)) + 1
        self.duration_stats["word_count_after_preflight"] = final_words
        self.duration_stats["estimated_seconds_after_preflight"] = round(
            self._estimate_seconds_from_words(final_words),
            3,
        )
        return expanded

    def _expand_short_script(self, plan: ScriptPlan, audio_duration: float) -> ScriptPlan:
        requested_seconds, min_seconds, _ = self._duration_bounds()
        current_words = self._word_count_plan(plan)

        if audio_duration <= 0.1:
            target_words = int(math.ceil((requested_seconds / 60.0) * max(1, self.config.target_speech_wpm)))
        else:
            scale = requested_seconds / max(1.0, audio_duration)
            target_words = int(math.ceil(current_words * max(1.12, scale * 0.93)))

        floor_words = int(math.ceil((min_seconds / 60.0) * max(1, self.config.target_speech_wpm)))
        target_words = max(target_words, floor_words)
        target_words = min(target_words, 3200)

        expanded = self._expand_plan_to_target_words(plan, target_words)
        self.duration_stats["post_tts_adjustments"] = int(self.duration_stats.get("post_tts_adjustments", 0)) + 1
        return expanded

    def _compress_long_script(self, plan: ScriptPlan, audio_duration: float) -> ScriptPlan:
        requested_seconds, _, max_seconds = self._duration_bounds()
        current_words = self._word_count_plan(plan)

        if audio_duration <= 0.1:
            target_words = int(math.ceil((requested_seconds / 60.0) * max(1, self.config.target_speech_wpm)))
        else:
            scale = requested_seconds / max(1.0, audio_duration)
            target_words = int(math.ceil(current_words * min(0.96, scale * 1.08)))

        ceiling_words = int(math.ceil((max_seconds / 60.0) * max(1, self.config.target_speech_wpm)))
        floor_words = max(110, len(plan.scenes) * 8)
        target_words = max(floor_words, min(target_words, ceiling_words))

        compressed = self._compress_plan_to_target_words(plan, target_words)
        self.duration_stats["post_tts_adjustments"] = int(self.duration_stats.get("post_tts_adjustments", 0)) + 1
        return compressed

    def _expand_plan_to_target_words(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        current_words = self._word_count_plan(plan)
        if current_words >= target_words:
            return plan

        if self.config.script_engine == "ollama" and self._ollama_ready:
            expanded = self._expand_script_plan_ollama(plan, target_words)
            if expanded is not None and self._word_count_plan(expanded) > current_words:
                return expanded
            self._warn("Ollama script expansion underperformed; using local expansion fallback.")

        return self._expand_script_plan_template(plan, target_words)

    def _compress_plan_to_target_words(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        current_words = self._word_count_plan(plan)
        if current_words <= target_words:
            return plan

        if self.config.script_engine == "ollama" and self._ollama_ready:
            compressed = self._compress_script_plan_ollama(plan, target_words)
            if compressed is not None and self._word_count_plan(compressed) < current_words:
                return compressed
            self._warn("Ollama script compression underperformed; using local compression fallback.")

        return self._compress_script_plan_template(plan, target_words)

    def _expand_script_plan_ollama(self, plan: ScriptPlan, target_words: int) -> ScriptPlan | None:
        current_words = self._word_count_plan(plan)
        scene_target = min(self.config.max_scenes, max(len(plan.scenes), int(target_words / 28)))
        current_json = json.dumps(plan.to_dict(), ensure_ascii=True)
        prompt = textwrap.dedent(
            f"""
            You are expanding an existing faceless explainer script.
            Return JSON only. Do not include markdown.

            Current total words: {current_words}
            Target total words: at least {target_words}
            Keep style educational, practical, and coherent.
            Keep topic: {self.config.prompt}
            Scene count target: around {scene_target}

            Input JSON:
            {current_json}

            Output schema:
            {{
              "title": "string",
              "summary": "string",
              "scenes": [
                {{
                  "heading": "short heading",
                  "voiceover": "2-4 sentences",
                  "search_terms": ["keyword1", "keyword2", "keyword3"]
                }}
              ]
            }}
            """
        ).strip()

        result = self._run_command(["ollama", "run", self.config.ollama_model, prompt], timeout=900, check=False)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                self._warn(f"Ollama expansion error: {stderr}")
            return None

        parsed = self._extract_json_object(result.stdout)
        if parsed is None:
            return None
        return self._normalize_script_plan(parsed)

    def _compress_script_plan_ollama(self, plan: ScriptPlan, target_words: int) -> ScriptPlan | None:
        current_words = self._word_count_plan(plan)
        scene_target = min(self.config.max_scenes, max(8, len(plan.scenes)))
        current_json = json.dumps(plan.to_dict(), ensure_ascii=True)
        prompt = textwrap.dedent(
            f"""
            You are compressing an existing faceless explainer script.
            Return JSON only. Do not include markdown.

            Current total words: {current_words}
            Target total words: around {target_words}
            Keep the same core meaning and topic: {self.config.prompt}
            Keep scene count around {scene_target}, and keep sections coherent.
            Use concise language and remove redundancy.

            Input JSON:
            {current_json}

            Output schema:
            {{
              "title": "string",
              "summary": "string",
              "scenes": [
                {{
                  "heading": "short heading",
                  "voiceover": "1-2 sentences",
                  "search_terms": ["keyword1", "keyword2", "keyword3"]
                }}
              ]
            }}
            """
        ).strip()

        result = self._run_command(["ollama", "run", self.config.ollama_model, prompt], timeout=900, check=False)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                self._warn(f"Ollama compression error: {stderr}")
            return None

        parsed = self._extract_json_object(result.stdout)
        if parsed is None:
            return None
        return self._normalize_script_plan(parsed)

    def _expand_script_plan_template(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        templates = [
            "In practical terms, this means teams should test assumptions early and validate outcomes with real measurements before scaling.",
            "A helpful way to evaluate this step is to compare expected benefits, trade-offs, and operational constraints in a simple checklist.",
            "When applied carefully, this idea improves reliability and makes future maintenance easier for both beginners and advanced teams.",
            "To make this actionable, focus on one measurable improvement at a time and verify results before moving to the next decision.",
        ]

        expanded_scenes: list[dict[str, Any]] = []
        for idx, scene in enumerate(plan.scenes):
            voiceover = scene.voiceover.strip()
            if voiceover and voiceover[-1] not in ".!?":
                voiceover += "."
            voiceover = (voiceover + " " + templates[idx % len(templates)]).strip()
            expanded_scenes.append(
                {
                    "heading": scene.heading,
                    "voiceover": voiceover,
                    "search_terms": scene.search_terms[:4],
                }
            )

        fallback_plan = {
            "title": plan.title,
            "summary": plan.summary,
            "scenes": expanded_scenes,
        }

        normalized = self._normalize_script_plan(fallback_plan)
        words = self._word_count_plan(normalized)
        extra_index = 1
        while words < target_words and len(normalized.scenes) < self.config.max_scenes:
            heading = f"Additional Context {extra_index}"
            voiceover = (
                f"This additional segment reinforces {self.config.prompt} with a concrete scenario, "
                "a practical constraint, and a clear recommendation for implementation choices."
            )
            expanded_scenes.append(
                {
                    "heading": heading,
                    "voiceover": voiceover,
                    "search_terms": self._default_search_terms(heading),
                }
            )
            fallback_plan["scenes"] = expanded_scenes
            normalized = self._normalize_script_plan(fallback_plan)
            words = self._word_count_plan(normalized)
            extra_index += 1

        return normalized

    def _compress_script_plan_template(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        def shorten_text(text: str, cap_words: int) -> str:
            sentence = text.strip().split(".")[0].strip()
            tokens = sentence.split()
            if len(tokens) > cap_words:
                sentence = " ".join(tokens[:cap_words]).rstrip(",;:-")
            if sentence and sentence[-1] not in ".!?":
                sentence += "."
            return sentence

        reduced_scenes: list[dict[str, Any]] = []
        for scene in plan.scenes:
            reduced_scenes.append(
                {
                    "heading": scene.heading,
                    "voiceover": shorten_text(scene.voiceover, cap_words=18),
                    "search_terms": scene.search_terms[:4],
                }
            )

        compressed_plan = {
            "title": plan.title,
            "summary": plan.summary,
            "scenes": reduced_scenes,
        }
        normalized = self._normalize_script_plan(compressed_plan)

        while self._word_count_plan(normalized) > target_words and len(reduced_scenes) > 8:
            drop_index = max(1, len(reduced_scenes) - 2)
            reduced_scenes.pop(drop_index)
            compressed_plan["scenes"] = reduced_scenes
            normalized = self._normalize_script_plan(compressed_plan)

        return normalized

    def _duration_within_tolerance(self, actual_seconds: float) -> bool:
        _, min_seconds, max_seconds = self._duration_bounds()
        return min_seconds <= actual_seconds <= max_seconds

    def _duration_too_short(self, actual_seconds: float) -> bool:
        _, min_seconds, _ = self._duration_bounds()
        return actual_seconds < min_seconds

    def _duration_too_long(self, actual_seconds: float) -> bool:
        _, _, max_seconds = self._duration_bounds()
        return actual_seconds > max_seconds

    def _update_duration_post_tts(self, plan: ScriptPlan, audio_duration: float, adjust_passes: int) -> None:
        requested, min_seconds, max_seconds = self._duration_bounds()
        words = self._word_count_plan(plan)
        delta_seconds = audio_duration - requested
        delta_percent = (delta_seconds / requested * 100.0) if requested else 0.0
        intro_seconds = self._intro_bookend_seconds()
        outro_seconds = self._outro_bookend_seconds()
        total_with_bookends = audio_duration + intro_seconds + outro_seconds

        self.duration_stats["word_count_final"] = words
        self.duration_stats["audio_seconds"] = round(audio_duration, 3)
        self.duration_stats["intro_seconds"] = round(intro_seconds, 3)
        self.duration_stats["outro_seconds"] = round(outro_seconds, 3)
        self.duration_stats["audio_seconds_with_bookends"] = round(total_with_bookends, 3)
        self.duration_stats["delta_seconds"] = round(delta_seconds, 3)
        self.duration_stats["delta_percent"] = round(delta_percent, 3)
        self.duration_stats["within_tolerance"] = bool(min_seconds <= audio_duration <= max_seconds)
        self.duration_stats["post_tts_adjustments"] = adjust_passes

    def _default_search_terms(self, heading: str) -> list[str]:
        terms = [part.strip() for part in re.split(r"[^a-zA-Z0-9]+", heading) if part.strip()]
        out = []
        for term in terms:
            if len(term) > 2:
                out.append(term)
            if len(out) >= 3:
                break
        out.append(self.config.prompt)
        dedup: list[str] = []
        seen: set[str] = set()
        for term in out:
            low = term.lower()
            if low not in seen:
                seen.add(low)
                dedup.append(term)
        return dedup[:4]

    def _synthesize_narration(self, text: str, output_raw_wav: Path) -> None:
        chunks = self._build_narration_chunks(text)
        if not chunks:
            raise RuntimeError("Narration text produced no speakable chunks")

        if self.config.tts_engine == "melo":
            self._tts_with_melo(chunks, output_raw_wav)
            return

        if self.config.tts_engine == "piper":
            self._tts_with_piper(chunks, output_raw_wav)
            return

        raise RuntimeError(f"Unsupported TTS engine: {self.config.tts_engine}")

    def _voice_profile_settings(self) -> dict[str, float]:
        profile = self.config.voice_profile
        defaults: dict[str, dict[str, float]] = {
            "calm-documentary": {
                "speed_multiplier": 0.93,
                "clause_pause": 0.14,
                "sentence_pause": 0.34,
                "paragraph_pause": 0.82,
                "max_words_per_chunk": 18,
            },
            "balanced": {
                "speed_multiplier": 1.0,
                "clause_pause": 0.11,
                "sentence_pause": 0.26,
                "paragraph_pause": 0.65,
                "max_words_per_chunk": 20,
            },
            "energetic-explainer": {
                "speed_multiplier": 1.07,
                "clause_pause": 0.07,
                "sentence_pause": 0.17,
                "paragraph_pause": 0.5,
                "max_words_per_chunk": 24,
            },
        }
        return defaults.get(profile, defaults["calm-documentary"])

    def _build_narration_chunks(self, text: str) -> list[dict[str, Any]]:
        settings = self._voice_profile_settings()
        max_words = int(settings["max_words_per_chunk"])
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        chunks: list[dict[str, Any]] = []
        merge_summary = {
            "merged_boundaries": 0,
            "function_word_avoids": 0,
            "tiny_tail_avoids": 0,
            "short_lead_avoids": 0,
        }

        for p_idx, paragraph in enumerate(paragraphs):
            sentences = self._split_sentences(paragraph)
            for s_idx, sentence in enumerate(sentences):
                fragments = self._split_long_sentence(sentence, max_words=max_words)
                fragments, merge_counts = self._merge_awkward_fragment_boundaries(fragments, max_words=max_words)
                for key in merge_summary:
                    merge_summary[key] += int(merge_counts.get(key, 0))

                for f_idx, fragment in enumerate(fragments):
                    fragment_text = fragment.strip()
                    if not fragment_text:
                        continue

                    if f_idx == len(fragments) - 1:
                        pause = float(settings["sentence_pause"])
                    else:
                        pause = self._clause_pause_for_fragment(fragment_text, settings)

                    if s_idx == len(sentences) - 1 and f_idx == len(fragments) - 1 and p_idx < len(paragraphs) - 1:
                        pause = float(settings["paragraph_pause"])

                    chunks.append(
                        {
                            "text": fragment_text,
                            "pause_after": pause,
                        }
                    )

        if chunks:
            chunks[-1]["pause_after"] = 0.0

        effective_speed = max(0.5, min(2.0, float(self.config.voice_speed) * float(settings["speed_multiplier"])))
        word_count = sum(self._word_count_text(str(item.get("text") or "")) for item in chunks)
        pause_total = sum(float(item.get("pause_after") or 0.0) for item in chunks)
        self.pacing_stats = {
            "voice_profile": self.config.voice_profile,
            "configured_voice_speed": round(float(self.config.voice_speed), 3),
            "profile_speed_multiplier": round(float(settings["speed_multiplier"]), 3),
            "effective_voice_speed": round(effective_speed, 3),
            "planned_chunks": len(chunks),
            "planned_words": word_count,
            "planned_pause_seconds": round(pause_total, 3),
            "merged_boundaries": merge_summary["merged_boundaries"],
            "function_word_avoids": merge_summary["function_word_avoids"],
            "tiny_tail_avoids": merge_summary["tiny_tail_avoids"],
            "short_lead_avoids": merge_summary["short_lead_avoids"],
        }
        return chunks

    def _split_sentences(self, paragraph: str) -> list[str]:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", paragraph.strip()) if part.strip()]
        if not parts and paragraph.strip():
            return [paragraph.strip()]
        return parts

    def _split_long_sentence(self, sentence: str, max_words: int) -> list[str]:
        words = self._word_count_text(sentence)
        if words <= max_words:
            return [sentence.strip()]

        candidates = [part.strip() for part in re.split(r"(?<=[,;:])\s+", sentence) if part.strip()]
        if len(candidates) <= 1:
            candidates = [sentence.strip()]

        chunks: list[str] = []
        current = ""
        current_words = 0

        for part in candidates:
            part_words = self._word_count_text(part)
            if part_words > max_words and not current:
                tokens = part.split()
                for i in range(0, len(tokens), max_words):
                    chunk = " ".join(tokens[i : i + max_words]).strip()
                    if chunk:
                        chunks.append(chunk)
                continue

            if current and (current_words + part_words) > max_words:
                chunks.append(current.strip())
                current = part
                current_words = part_words
            else:
                current = f"{current} {part}".strip() if current else part
                current_words += part_words

        if current:
            chunks.append(current.strip())

        return chunks or [sentence.strip()]

    def _clause_pause_for_fragment(self, fragment: str, settings: dict[str, float]) -> float:
        pause = float(settings["clause_pause"])
        words = self._word_count_text(fragment)

        # If a boundary came from hard splitting (no comma/semicolon), keep this micro-pause subtle.
        if not self._has_clause_terminal_punctuation(fragment):
            pause = min(pause, 0.08)

        # Do not over-emphasize very short fragments.
        if words <= 3:
            pause = min(pause, 0.06)
        elif words <= 5:
            pause = min(pause, 0.09)

        return max(0.02, pause)

    def _merge_awkward_fragment_boundaries(
        self,
        fragments: list[str],
        max_words: int,
    ) -> tuple[list[str], dict[str, int]]:
        counts = {
            "merged_boundaries": 0,
            "function_word_avoids": 0,
            "tiny_tail_avoids": 0,
            "short_lead_avoids": 0,
        }
        cleaned = [part.strip() for part in fragments if part.strip()]
        if len(cleaned) <= 1:
            return cleaned, counts

        merged: list[str] = [cleaned[0]]
        for right in cleaned[1:]:
            left = merged[-1]
            reason = self._boundary_merge_reason(left, right, max_words=max_words)
            if reason is None:
                merged.append(right)
                continue

            merged[-1] = f"{left} {right}".strip()
            counts["merged_boundaries"] += 1
            if reason == "function_word":
                counts["function_word_avoids"] += 1
            elif reason == "tiny_tail":
                counts["tiny_tail_avoids"] += 1
            elif reason == "short_lead":
                counts["short_lead_avoids"] += 1

        # Final guard: avoid ending with a tiny trailing chunk.
        while len(merged) > 1 and self._word_count_text(merged[-1]) <= 2:
            prev_words = self._word_count_text(merged[-2])
            tail_words = self._word_count_text(merged[-1])
            if prev_words + tail_words > (max_words + 4):
                break
            merged[-2] = f"{merged[-2]} {merged[-1]}".strip()
            merged.pop()
            counts["merged_boundaries"] += 1
            counts["tiny_tail_avoids"] += 1

        return merged, counts

    def _boundary_merge_reason(self, left: str, right: str, max_words: int) -> str | None:
        left_words = self._word_count_text(left)
        right_words = self._word_count_text(right)
        last_token = self._last_word_token(left)

        if last_token and self._is_function_word(last_token):
            return "function_word"

        if right_words <= 2 and (left_words + right_words) <= (max_words + 4):
            return "tiny_tail"

        if left_words <= 3 and (left_words + right_words) <= (max_words + 2):
            return "short_lead"

        return None

    def _last_word_token(self, text: str) -> str | None:
        tokens = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text or "")
        if not tokens:
            return None
        return tokens[-1].lower()

    def _is_function_word(self, token: str | None) -> bool:
        if not token:
            return False
        return token.lower() in FUNCTION_WORDS

    def _has_clause_terminal_punctuation(self, text: str) -> bool:
        value = (text or "").strip()
        return value.endswith((",", ";", ":"))

    def _word_count_text(self, text: str) -> int:
        return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text or ""))

    def _tts_with_melo(self, chunks: list[dict[str, Any]], output_raw_wav: Path) -> None:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            from melo.api import TTS  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "MeloTTS not available. Install voice deps with: python -m pip install -e '.[voice]'"
            ) from exc

        settings = self._voice_profile_settings()
        effective_speed = max(0.5, min(2.0, float(self.config.voice_speed) * float(settings["speed_multiplier"])))
        parts_dir = self.paths["tmp"] / "tts_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        part_files: list[Path] = []

        try:
            tts = TTS(language=self.config.melo_language, device="auto")
            speaker_id = 0
            spk2id = {}
            hps = getattr(tts, "hps", None)
            hps_data = getattr(hps, "data", None)
            if hps_data is not None and hasattr(hps_data, "spk2id"):
                spk2id = dict(getattr(hps_data, "spk2id") or {})

            if self.config.melo_speaker in spk2id:
                speaker_id = int(spk2id[self.config.melo_speaker])
            elif spk2id:
                speaker_id = int(next(iter(spk2id.values())))

            for idx, chunk in enumerate(chunks):
                text = str(chunk.get("text") or "").strip()
                if not text:
                    continue

                raw_path = parts_dir / f"chunk_{idx:04d}.raw.wav"
                wav_path = parts_dir / f"chunk_{idx:04d}.wav"
                tts.tts_to_file(text, speaker_id, str(raw_path), speed=effective_speed)
                self._standardize_wav(raw_path, wav_path)
                part_files.append(wav_path)

                pause_seconds = float(chunk.get("pause_after") or 0.0)
                if pause_seconds > 0.0:
                    pause_path = parts_dir / f"pause_{idx:04d}.wav"
                    self._generate_silence_wav(pause_path, pause_seconds)
                    part_files.append(pause_path)
        except Exception as exc:
            raise RuntimeError(f"MeloTTS synthesis failed: {exc}") from exc

        if not part_files:
            raise RuntimeError("MeloTTS did not produce any audio parts")

        self._concat_wav_parts(part_files, output_raw_wav)
        if not output_raw_wav.exists() or output_raw_wav.stat().st_size == 0:
            raise RuntimeError("MeloTTS did not produce audio output")

    def _tts_with_piper(self, chunks: list[dict[str, Any]], output_raw_wav: Path) -> None:
        piper_command = self._piper_command or self._resolve_piper_command()
        if piper_command is None:
            raise RuntimeError("Piper runtime not found. Install with: python -m pip install piper-tts")

        voice_meta = self._resolve_piper_voice_meta()
        model_path, config_path = self._ensure_piper_voice_assets(voice_meta)
        parts_dir = self.paths["tmp"] / "tts_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        part_files: list[Path] = []
        settings = self._voice_profile_settings()
        effective_speed = max(0.5, min(2.0, float(self.config.voice_speed) * float(settings["speed_multiplier"])))
        length_scale = max(0.1, min(2.5, 1.0 / effective_speed))
        speaker_id = voice_meta.get("speaker_id")

        for idx, chunk in enumerate(chunks):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue

            raw_wav = parts_dir / f"piper_{idx:04d}.raw.wav"
            wav_path = parts_dir / f"piper_{idx:04d}.wav"
            command = list(piper_command)
            command.extend(
                [
                    "--model",
                    str(model_path),
                    "--config",
                    str(config_path),
                    "--output_file",
                    str(raw_wav),
                    "--length_scale",
                    f"{length_scale:.4f}",
                ]
            )
            if speaker_id is not None:
                command.extend(["--speaker", str(int(speaker_id))])

            result = subprocess.run(
                command,
                check=False,
                input=text,
                text=True,
                capture_output=True,
                timeout=180,
            )
            if int(result.returncode or 0) != 0:
                stderr_text = str(result.stderr or "").strip()
                if "No module named 'pathvalidate'" in stderr_text:
                    raise RuntimeError(
                        "Piper dependency missing. Install with: "
                        f"{sys.executable} -m pip install pathvalidate"
                    )
                raise RuntimeError(f"Piper synthesis failed: {stderr_text}")

            self._standardize_wav(raw_wav, wav_path)
            part_files.append(wav_path)

            pause_seconds = float(chunk.get("pause_after") or 0.0)
            if pause_seconds > 0.0:
                pause_path = parts_dir / f"pause_{idx:04d}.wav"
                self._generate_silence_wav(pause_path, pause_seconds)
                part_files.append(pause_path)

        if not part_files:
            raise RuntimeError("Piper TTS did not produce any audio parts")

        self._concat_wav_parts(part_files, output_raw_wav)

    def _resolve_piper_command(self) -> list[str] | None:
        direct_candidates: list[Path] = []
        from_path = shutil.which("piper")
        if from_path:
            direct_candidates.append(Path(from_path).expanduser().resolve())

        venv_piper = (Path(__file__).resolve().parents[2] / ".venv" / "bin" / "piper").resolve()
        if venv_piper.exists() and os.access(str(venv_piper), os.X_OK):
            direct_candidates.append(venv_piper)

        seen: set[str] = set()
        for candidate in direct_candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            try:
                probe = subprocess.run(
                    [key, "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if int(probe.returncode or 0) == 0:
                    self._piper_command = [key]
                    return [key]
            except Exception:
                continue

        python_candidates: list[Path] = []
        venv_python = os.environ.get("VIRTUAL_ENV")
        if venv_python:
            python_candidates.append((Path(venv_python) / "bin" / "python").resolve())
        python_candidates.append(Path(sys.executable).resolve())

        seen_python: set[str] = set()
        for py in python_candidates:
            py_str = str(py)
            if py_str in seen_python:
                continue
            seen_python.add(py_str)
            if not py.exists() or not os.access(py_str, os.X_OK):
                continue
            try:
                probe = subprocess.run(
                    [py_str, "-m", "piper", "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if int(probe.returncode or 0) == 0:
                    self._piper_command = [py_str, "-m", "piper"]
                    return [py_str, "-m", "piper"]
            except Exception:
                continue

        return None

    def _resolve_piper_voice_meta(self) -> dict[str, Any]:
        speaker_id = self.config.piper_speaker_id
        if self.config.piper_model_url and self.config.piper_config_url:
            voice_id = str(self.config.piper_voice_id or "custom-piper").strip() or "custom-piper"
            return {
                "id": voice_id,
                "speaker_id": speaker_id,
                "model_url": str(self.config.piper_model_url).strip(),
                "config_url": str(self.config.piper_config_url).strip(),
            }

        voice_id = str(self.config.piper_voice_id or "").strip()
        if not voice_id:
            raise RuntimeError("Piper voice id is required when --tts-engine piper is selected")

        matches = [item for item in PIPER_VOICE_PRESETS if str(item.get("id") or "") == voice_id]
        if not matches:
            available_ids = sorted({str(item.get("id") or "").strip() for item in PIPER_VOICE_PRESETS if item.get("id")})
            raise RuntimeError(
                "Unknown Piper voice id: "
                f"{voice_id}. Available ids: {', '.join(available_ids)}"
            )

        if speaker_id is not None:
            for item in matches:
                item_speaker = item.get("speaker_id")
                if item_speaker is not None and int(item_speaker) == int(speaker_id):
                    return dict(item)

        return dict(matches[0])

    def _ensure_piper_voice_assets(self, voice_meta: dict[str, Any]) -> tuple[Path, Path]:
        voice_id = str(voice_meta.get("id") or "voice").strip() or "voice"
        model_url = str(voice_meta.get("model_url") or "").strip()
        config_url = str(voice_meta.get("config_url") or "").strip()
        if not model_url or not config_url:
            raise RuntimeError(f"Piper voice `{voice_id}` missing model/config URL")

        safe_voice_id = self._safe_filename_token(voice_id)
        voice_dir = (Path.home() / ".imagine" / "models" / "piper" / safe_voice_id).resolve()
        voice_dir.mkdir(parents=True, exist_ok=True)
        model_path = voice_dir / f"{safe_voice_id}.onnx"
        config_path = voice_dir / f"{safe_voice_id}.onnx.json"

        if not model_path.exists() or model_path.stat().st_size <= 0:
            self._download_url_to_file(model_url, model_path)
        if not config_path.exists() or config_path.stat().st_size <= 0:
            self._download_url_to_file(config_url, config_path)

        return model_path, config_path

    def _download_url_to_file(self, url: str, destination: Path) -> None:
        try:
            with urlrequest.urlopen(url, timeout=120) as response:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
        except urlerror.URLError as exc:
            raise RuntimeError(f"Failed to download Piper asset {url}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to download Piper asset {url}: {exc}") from exc

    def _safe_filename_token(self, value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("_")
        return token or "sample"

    def _standardize_wav(self, input_audio: Path, output_wav: Path) -> None:
        convert = self._run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_audio),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(output_wav),
            ],
            timeout=300,
            check=False,
        )
        if convert.returncode != 0:
            raise RuntimeError(f"Failed to standardize audio: {convert.stderr.strip()}")

    def _generate_silence_wav(self, output_wav: Path, seconds: float, sample_rate: int = 24000) -> None:
        duration = max(0.03, float(seconds))
        rate = max(8000, int(sample_rate))
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={rate}:cl=mono",
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
        result = self._run_command(command, timeout=120, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to generate silence audio: {result.stderr.strip()}")

    def _concat_wav_parts(
        self,
        part_files: list[Path],
        output_wav: Path,
        sample_rate: int = 24000,
        concat_list_path: Path | None = None,
    ) -> None:
        if len(part_files) == 1:
            shutil.copy2(part_files[0], output_wav)
            return

        concat_list = concat_list_path or (self.paths["tmp"] / "tts_parts" / "concat_audio.txt")
        concat_list.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"file '{path.resolve()}'" for path in part_files]
        self._write_text(concat_list, "\n".join(lines) + "\n")

        rate = max(8000, int(sample_rate))

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
        result = self._run_command(command, timeout=900, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to concatenate narration chunks: {result.stderr.strip()}")

    def _build_render_audio_track(
        self,
        narration_wav: Path,
        output_audio: Path,
        intro_seconds: float,
        outro_seconds: float,
    ) -> None:
        lead = max(0.0, float(intro_seconds))
        tail = max(0.0, float(outro_seconds))
        if lead <= 0.0 and tail <= 0.0:
            shutil.copy2(narration_wav, output_audio)
            return

        parts_dir = self.paths["tmp"] / "audio_bookends"
        parts_dir.mkdir(parents=True, exist_ok=True)

        part_files: list[Path] = []
        if lead > 0.0:
            lead_path = parts_dir / "intro_silence.wav"
            self._generate_silence_wav(lead_path, lead, sample_rate=48000)
            part_files.append(lead_path)

        part_files.append(narration_wav)

        if tail > 0.0:
            tail_path = parts_dir / "outro_silence.wav"
            self._generate_silence_wav(tail_path, tail, sample_rate=48000)
            part_files.append(tail_path)

        self._concat_wav_parts(
            part_files=part_files,
            output_wav=output_audio,
            sample_rate=48000,
            concat_list_path=parts_dir / "concat_bookends.txt",
        )

    def _select_voice_sample_excerpt(self, text: str, sample_words: int) -> str:
        target = max(40, int(sample_words))
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return ""

        sentences = self._split_sentences(normalized)
        if not sentences:
            return " ".join(normalized.split()[:target]).strip()

        selected: list[str] = []
        words = 0
        for sentence in sentences:
            clean = sentence.strip()
            if not clean:
                continue
            selected.append(clean)
            words += self._word_count_text(clean)
            if words >= target:
                break

        excerpt = " ".join(selected).strip()
        if not excerpt:
            excerpt = " ".join(normalized.split()[:target]).strip()
        return excerpt

    def generate_voice_ab_samples(
        self,
        text: str,
        speakers: list[str],
        output_dir: Path,
        sample_words: int = 130,
    ) -> dict[str, Any]:
        if self.config.tts_engine != "melo":
            raise RuntimeError("Voice A/B samples currently support only Melo TTS")

        self._require_binary("ffmpeg")
        self._prepare_dirs()
        output_dir.mkdir(parents=True, exist_ok=True)

        excerpt = self._select_voice_sample_excerpt(text, sample_words)
        if not excerpt:
            raise RuntimeError("No text content available for voice sample generation")

        unique_speakers: list[str] = []
        seen: set[str] = set()
        for speaker in speakers:
            value = str(speaker).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            unique_speakers.append(value)

        if not unique_speakers:
            raise RuntimeError("No valid speakers were provided")

        original_speaker = self.config.melo_speaker
        sample_entries: list[dict[str, Any]] = []
        comparison_parts: list[Path] = []

        try:
            for idx, speaker in enumerate(unique_speakers, start=1):
                self.config.melo_speaker = speaker
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", speaker).strip("_") or f"speaker_{idx}"
                raw_path = output_dir / f"{idx:02d}_{safe_name}.raw.wav"
                final_path = output_dir / f"{idx:02d}_{safe_name}.wav"

                self._synthesize_narration(excerpt, raw_path)
                self._normalize_audio(raw_path, final_path)
                duration = self._media_duration(final_path)

                sample_entries.append(
                    {
                        "speaker": speaker,
                        "file": str(final_path.resolve()),
                        "duration_seconds": round(duration, 3),
                    }
                )

                comparison_parts.append(final_path)
                if idx < len(unique_speakers):
                    gap_path = output_dir / f"gap_{idx:02d}.wav"
                    self._generate_silence_wav(gap_path, 0.7)
                    comparison_parts.append(gap_path)
        finally:
            self.config.melo_speaker = original_speaker

        compare_mix = output_dir / "ab_compare.wav"
        self._concat_wav_parts(comparison_parts, compare_mix)

        report = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "voice_profile": self.config.voice_profile,
            "voice_speed": self.config.voice_speed,
            "melo_language": self.config.melo_language,
            "sample_words": self._word_count_text(excerpt),
            "sample_text": excerpt,
            "compare_mix": str(compare_mix.resolve()),
            "samples": sample_entries,
            "output_dir": str(output_dir.resolve()),
        }
        report_path = output_dir / "voice_ab_report.json"
        self._write_json(report_path, report)
        report["report_file"] = str(report_path.resolve())
        return report

    def _normalize_audio(self, input_wav: Path, output_wav: Path) -> None:
        result = self._run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_wav),
                "-af",
                "loudnorm=I=-16:TP=-1.0:LRA=11",
                "-ac",
                "1",
                "-ar",
                "48000",
                str(output_wav),
            ],
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Audio normalization failed: {result.stderr.strip()}")

    def _rebalance_scene_durations(self, plan: ScriptPlan, audio_duration: float) -> None:
        if audio_duration <= 0.01:
            return

        weights = [max(1, len(scene.voiceover.split())) for scene in plan.scenes]
        weight_sum = float(sum(weights))
        if weight_sum <= 0:
            per_scene = audio_duration / max(1, len(plan.scenes))
            for scene in plan.scenes:
                scene.seconds = max(self.config.min_scene_seconds, per_scene)
            return

        min_total = self.config.min_scene_seconds * len(plan.scenes)
        if min_total > audio_duration:
            per_scene = audio_duration / max(1, len(plan.scenes))
            for scene in plan.scenes:
                scene.seconds = per_scene
            return

        remaining = audio_duration - min_total
        for scene, weight in zip(plan.scenes, weights):
            variable = remaining * (weight / weight_sum)
            scene.seconds = self.config.min_scene_seconds + variable

        # Fix tiny floating error.
        current_total = sum(scene.seconds for scene in plan.scenes)
        if current_total > 0:
            scale = audio_duration / current_total
            for scene in plan.scenes:
                scene.seconds *= scale

    def _resolve_assets(
        self,
        plan: ScriptPlan,
        *,
        scenes: list[Scene] | None = None,
        preused_asset_keys: set[str] | None = None,
    ) -> list[AssetRight]:
        rights: list[AssetRight] = []
        query_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        download_failures = 0
        placeholder_scenes = 0
        duplicate_candidate_rejections = 0
        unique_shortfall_scene_ids: list[str] = []
        unique_shortfall_clip_names: list[str] = []
        unresolved_scene_ids: list[str] = []
        used_asset_keys: set[str] = set(preused_asset_keys or set())
        target_scenes = list(scenes) if scenes is not None else list(plan.scenes)

        provider_order = [
            ("pexels", self.config.pexels_api_key),
            ("pixabay", self.config.pixabay_api_key),
        ]

        if not any(key for _, key in provider_order):
            message = "No stock API keys configured (PEXELS_API_KEY / PIXABAY_API_KEY)."
            if self.config.require_external_assets:
                raise RuntimeError(message + " --require-external-assets is enabled.")
            self._warn(message + " Using generated placeholders.")

        if self.config.asset_keywords:
            self._log(f"Asset keyword constraint enabled: {', '.join(self.config.asset_keywords)}")

        for scene in target_scenes:
            queries = self._queries_for_scene(scene)
            resolved = False
            saw_candidates = False
            scene_duplicate_rejections = 0
            for provider_name, api_key in provider_order:
                if not api_key:
                    continue

                for query in queries:
                    cache_key = (provider_name, query.lower())
                    candidates = query_cache.get(cache_key)
                    if cache_key not in query_cache:
                        candidates = []
                        try:
                            if provider_name == "pexels":
                                candidates = self._search_pexels_videos(api_key, query=query)
                            elif provider_name == "pixabay":
                                candidates = self._search_pixabay_videos(api_key, query=query)
                        except Exception as exc:
                            self._log(f"{provider_name} search failed for {scene.scene_id} ({query}): {exc}")
                            candidates = []

                        query_cache[cache_key] = list(candidates)

                    if not candidates:
                        continue
                    saw_candidates = True

                    for candidate in candidates:
                        unique_key = self._asset_uniqueness_key(candidate)
                        if unique_key in used_asset_keys:
                            duplicate_candidate_rejections += 1
                            scene_duplicate_rejections += 1
                            continue

                        try:
                            local_path = self._download_asset(str(candidate["download_url"]))

                            scene.asset_path = str(local_path)
                            scene.asset_provider = str(candidate.get("source_platform") or "unknown")
                            used_asset_keys.add(unique_key)

                            right = AssetRight(
                                scene_id=scene.scene_id,
                                source_platform=str(candidate.get("source_platform") or "unknown"),
                                source_asset_id=str(candidate.get("source_asset_id") or "") or None,
                                source_url=str(candidate.get("source_url") or candidate.get("download_url") or ""),
                                creator_name=(str(candidate.get("creator_name")) if candidate.get("creator_name") else None),
                                creator_profile_url=(
                                    str(candidate.get("creator_profile_url")) if candidate.get("creator_profile_url") else None
                                ),
                                license_name=(str(candidate.get("license_name")) if candidate.get("license_name") else None),
                                license_url=(str(candidate.get("license_url")) if candidate.get("license_url") else None),
                                downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                local_path=str(local_path.resolve()),
                                sha256=self._file_sha256(local_path),
                                restriction_flags=list(candidate.get("restriction_flags") or []),
                                attribution_required=bool(candidate.get("attribution_required", False)),
                                attribution_text=(
                                    str(candidate.get("attribution_text")) if candidate.get("attribution_text") else None
                                ),
                            )
                            rights.append(right)
                            resolved = True
                            break
                        except Exception as exc:
                            download_failures += 1
                            self._log(f"Asset download failed for {scene.scene_id} ({provider_name}, {query}): {exc}")

                    if resolved:
                        break

                if resolved:
                    break

            if not resolved:
                placeholder_scenes += 1
                unresolved_scene_ids.append(scene.scene_id)
                if saw_candidates and scene_duplicate_rejections > 0:
                    unique_shortfall_scene_ids.append(scene.scene_id)
                    unique_shortfall_clip_names.append(scene.clip_name)

        self.asset_stats = {
            "resolved_scene_count": len(rights),
            "placeholder_scene_count": placeholder_scenes,
            "download_failures": download_failures,
            "duplicate_candidate_rejections": duplicate_candidate_rejections,
            "unique_shortfall_count": len(unique_shortfall_scene_ids),
            "unique_shortfall_scene_ids": unique_shortfall_scene_ids,
            "unique_shortfall_clip_names": unique_shortfall_clip_names,
            "unresolved_scene_ids": unresolved_scene_ids,
            "asset_keywords": list(self.config.asset_keywords),
        }

        if download_failures > 0:
            self._warn(
                f"{download_failures} asset downloads failed (network or provider timeout). "
                "Placeholders were used for affected scenes."
            )

        if self.config.require_external_assets and placeholder_scenes > 0:
            if unique_shortfall_scene_ids:
                keyword_hint = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(none)"
                clip_hint = ", ".join(unique_shortfall_clip_names[:8])
                raise AssetUniquenessError(
                    (
                        "Unique asset shortfall: could not resolve unique stock clips for "
                        f"{len(unique_shortfall_scene_ids)} scene(s). Broaden --asset-keywords and retry. "
                        f"Current keywords: {keyword_hint}. "
                        f"Affected clips: {clip_hint}"
                    ),
                    shortfall_scene_ids=list(unique_shortfall_scene_ids),
                    keywords=list(self.config.asset_keywords),
                )
            raise RuntimeError(
                f"{placeholder_scenes} scenes could not resolve an external stock asset while "
                "--require-external-assets is enabled."
            )

        if placeholder_scenes > 0 and rights:
            self._warn(f"{placeholder_scenes} scenes used placeholder visuals because no stock asset was resolved.")

        if unique_shortfall_scene_ids and not self.config.require_external_assets:
            keyword_hint = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(none)"
            self._warn(
                "Unique asset shortfall detected. Some scenes would require repeated clips. "
                f"Broaden asset keywords and retry. Current keywords: {keyword_hint}."
            )

        if not rights:
            self._warn("No external assets resolved for this run. Final video uses generated placeholders only.")
        return rights

    def _asset_uniqueness_key(self, candidate: dict[str, Any]) -> str:
        platform = str(candidate.get("source_platform") or "unknown").strip().lower()
        source_asset_id = str(candidate.get("source_asset_id") or "").strip()
        if source_asset_id:
            return f"{platform}:id:{source_asset_id}"

        source_url = str(candidate.get("source_url") or "").strip().lower()
        if source_url:
            return f"{platform}:url:{source_url}"

        download_url = str(candidate.get("download_url") or "").strip().lower()
        if download_url:
            return f"{platform}:download:{download_url}"

        fallback = json.dumps(candidate, sort_keys=True, ensure_ascii=True)
        digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()
        return f"{platform}:digest:{digest}"

    def _asset_uniqueness_key_from_right(self, right: AssetRight) -> str:
        return self._asset_uniqueness_key(
            {
                "source_platform": right.source_platform,
                "source_asset_id": right.source_asset_id or "",
                "source_url": right.source_url,
                "download_url": right.source_url,
            }
        )

    def _stable_rotate_candidates(self, candidates: list[dict[str, Any]], seed: str) -> list[dict[str, Any]]:
        if not candidates:
            return []

        ordered = sorted(
            candidates,
            key=lambda item: (
                str(item.get("source_asset_id") or ""),
                str(item.get("download_url") or ""),
            ),
        )

        if len(ordered) <= 1:
            return ordered

        pivot = self._stable_pivot(seed, len(ordered))
        return ordered[pivot:] + ordered[:pivot]

    def _search_pexels_videos(self, api_key: str, query: str) -> list[dict[str, Any]]:
        url = f"https://api.pexels.com/videos/search?query={quote_plus(query)}&orientation=landscape&per_page=20"
        response = self.http.get(url, headers={"Authorization": api_key}, timeout=(5, 15))
        if response.status_code != 200:
            return []

        payload = response.json()
        videos = payload.get("videos")
        if not isinstance(videos, list) or not videos:
            return []

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for best in videos:
            if not isinstance(best, dict):
                continue

            files = best.get("video_files")
            if not isinstance(files, list) or not files:
                continue

            files_sorted = sorted(
                [f for f in files if isinstance(f, dict) and f.get("link")],
                key=lambda f: int(f.get("width") or 0),
                reverse=True,
            )
            if not files_sorted:
                continue

            selected = files_sorted[0]
            user = best.get("user") if isinstance(best, dict) else {}
            if not isinstance(user, dict):
                user = {}

            source_asset_id = str(best.get("id") or "")
            if source_asset_id and source_asset_id in seen:
                continue
            if source_asset_id:
                seen.add(source_asset_id)

            candidates.append(
                {
                    "source_platform": "pexels",
                    "source_asset_id": source_asset_id,
                    "download_url": str(selected.get("link")),
                    "source_url": f"https://www.pexels.com/video/{best.get('id')}/",
                    "creator_name": user.get("name"),
                    "creator_profile_url": user.get("url"),
                    "license_name": "Pexels License",
                    "license_url": "https://www.pexels.com/license/",
                    "restriction_flags": [],
                    "attribution_required": False,
                }
            )

        return self._stable_rotate_candidates(candidates, seed=f"pexels:{query.lower()}")

    def _search_pixabay_videos(self, api_key: str, query: str) -> list[dict[str, Any]]:
        url = (
            "https://pixabay.com/api/videos/"
            f"?key={quote_plus(api_key)}&q={quote_plus(query)}&safesearch=true&per_page=20"
        )
        response = self.http.get(url, timeout=(5, 15))
        if response.status_code != 200:
            return []

        payload = response.json()
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            return []

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        quality_order = ["large", "medium", "small", "tiny"]

        for best in hits:
            if not isinstance(best, dict):
                continue

            videos = best.get("videos")
            if not isinstance(videos, dict):
                continue

            selected_url = ""
            for key in quality_order:
                block = videos.get(key)
                if isinstance(block, dict) and block.get("url"):
                    selected_url = str(block["url"])
                    break
            if not selected_url:
                continue

            source_asset_id = str(best.get("id") or "")
            if source_asset_id and source_asset_id in seen:
                continue
            if source_asset_id:
                seen.add(source_asset_id)

            candidates.append(
                {
                    "source_platform": "pixabay",
                    "source_asset_id": source_asset_id,
                    "download_url": selected_url,
                    "source_url": str(best.get("pageURL") or ""),
                    "creator_name": str(best.get("user") or "") or None,
                    "creator_profile_url": None,
                    "license_name": "Pixabay License",
                    "license_url": "https://pixabay.com/service/license/",
                    "restriction_flags": [],
                    "attribution_required": False,
                }
            )

        return self._stable_rotate_candidates(candidates, seed=f"pixabay:{query.lower()}")

    def _query_for_scene(self, scene: Scene) -> str:
        return self._queries_for_scene(scene)[0]

    def _stable_pivot(self, seed: str, upper_bound: int) -> int:
        limit = max(1, int(upper_bound))
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
        return int(digest[:12], 16) % limit

    def _queries_for_scene(self, scene: Scene) -> list[str]:
        scene_terms = [term.strip() for term in scene.search_terms if term.strip()]

        keywords: list[str] = []
        seen_keywords: set[str] = set()
        for item in self.config.asset_keywords:
            value = str(item).strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen_keywords:
                continue
            seen_keywords.add(lowered)
            keywords.append(value)

        if keywords:
            combined: list[str] = list(keywords)
            for term in scene_terms:
                lowered = term.lower()
                if lowered in seen_keywords:
                    continue
                combined.append(term)

            primary = " ".join(combined[:6]).strip()
            fallback = " ".join(keywords[:4]).strip()

            queries: list[str] = []
            if primary:
                queries.append(primary)
            if fallback and fallback.lower() not in {item.lower() for item in queries}:
                queries.append(fallback)
            if queries:
                return queries

        if scene_terms:
            return [" ".join(scene_terms[:3])]

        heading = str(scene.heading).strip()
        if heading:
            return [heading]
        return [self.config.prompt]

    def _download_asset(self, url: str) -> Path:
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()
        if ext not in {".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png"}:
            ext = ".mp4"

        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        output = self.paths["assets_cache"] / f"{digest}{ext}"
        if output.exists() and output.stat().st_size > 0:
            return output

        with self.http.get(url, stream=True, timeout=(8, 20)) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Failed to download asset, status={response.status_code}")

            with output.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)

        if output.stat().st_size == 0:
            raise RuntimeError("Downloaded asset is empty")
        return output

    def _generate_captions(self, plan: ScriptPlan, narration_wav: Path) -> list[tuple[float, float, str]]:
        captions: list[tuple[float, float, str]] = []

        if self.config.caption_engine == "faster-whisper":
            words, segments = self._transcribe_with_faster_whisper(narration_wav)
            if words:
                captions = self._chunk_word_events(words)
                self.caption_stats["source"] = "faster-whisper-words"
            elif segments:
                pseudo_words = self._segments_to_pseudo_words(segments)
                captions = self._chunk_word_events(pseudo_words)
                self.caption_stats["source"] = "faster-whisper-segments"
                self._warn("Word timestamps unavailable; using segment-based subtitle timing.")
            else:
                self._warn("faster-whisper caption pass failed; falling back to heuristic captions")

        if not captions:
            captions = self._captions_heuristic(plan)
            self.caption_stats["source"] = "heuristic"

        self._update_caption_stats(captions)
        return captions

    def _transcribe_with_faster_whisper(
        self,
        narration_wav: Path,
    ) -> tuple[list[tuple[float, float, str]], list[tuple[float, float, str]]]:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception:
            self._warn("faster-whisper not installed; using heuristic subtitles")
            return ([], [])

        try:
            model = WhisperModel("large-v3", device="auto", compute_type="int8")
            segments, _ = model.transcribe(
                str(narration_wav),
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
            )

            word_events: list[tuple[float, float, str]] = []
            segment_events: list[tuple[float, float, str]] = []

            for segment in segments:
                seg_start = float(segment.start)
                seg_end = float(segment.end)
                seg_text = (segment.text or "").strip()
                if seg_text:
                    segment_events.append((seg_start, max(seg_start + 0.05, seg_end), seg_text))

                raw_words = getattr(segment, "words", None)
                if not raw_words:
                    continue

                for raw_word in raw_words:
                    token = (getattr(raw_word, "word", "") or "").strip()
                    if not token:
                        continue

                    word_start = getattr(raw_word, "start", None)
                    word_end = getattr(raw_word, "end", None)
                    if word_start is None or word_end is None:
                        continue

                    start = float(word_start)
                    end = float(word_end)
                    if end <= start:
                        continue
                    word_events.append((start, end, token))

            return (word_events, segment_events)
        except Exception as exc:
            self._log(f"faster-whisper error: {exc}")
            return ([], [])

    def _segments_to_pseudo_words(
        self,
        segments: list[tuple[float, float, str]],
    ) -> list[tuple[float, float, str]]:
        words: list[tuple[float, float, str]] = []
        for seg_start, seg_end, seg_text in segments:
            tokens = self._tokenize_caption_text(seg_text)
            if not tokens:
                continue

            duration = max(0.2, seg_end - seg_start)
            per_token = duration / max(1, len(tokens))
            for index, token in enumerate(tokens):
                start = seg_start + (index * per_token)
                end = seg_start + ((index + 1) * per_token)
                words.append((start, end, token))

        return words

    def _captions_heuristic(self, plan: ScriptPlan) -> list[tuple[float, float, str]]:
        words: list[tuple[float, float, str]] = []
        cursor = 0.0

        for scene in plan.scenes:
            tokens = self._tokenize_caption_text(scene.voiceover)
            if not tokens:
                cursor += scene.seconds
                continue

            scene_duration = max(scene.seconds, 0.4)
            per_token = scene_duration / max(1, len(tokens))
            for index, token in enumerate(tokens):
                start = cursor + (index * per_token)
                end = cursor + ((index + 1) * per_token)
                words.append((start, end, token))

            cursor += scene_duration

        return self._chunk_word_events(words)

    def _chunk_word_events(self, words: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
        if not words:
            return []

        if self.config.caption_style == "line":
            min_words = max(4, self.config.caption_words_min)
            max_words = max(min_words, max(self.config.caption_words_max, 10))
            max_chars = max(self.config.caption_max_chars, 42)
            min_seconds = max(self.config.caption_min_seconds, 1.0)
            max_seconds = max(self.config.caption_max_seconds, 4.0)
        else:
            min_words = self.config.caption_words_min
            max_words = self.config.caption_words_max
            max_chars = self.config.caption_max_chars
            min_seconds = self.config.caption_min_seconds
            max_seconds = self.config.caption_max_seconds

        chunks: list[tuple[float, float, str]] = []
        current: list[tuple[float, float, str]] = []

        def flush_current() -> None:
            if not current:
                return
            start = float(current[0][0])
            end = float(current[-1][1])
            text = self._join_caption_tokens([item[2] for item in current])
            if text:
                chunks.append((start, max(start + 0.05, end), text))
            current.clear()

        for start, end, token in words:
            normalized = token.strip()
            if not normalized:
                continue

            if not current:
                current.append((start, end, normalized))
                continue

            gap = start - current[-1][1]
            candidate_tokens = [item[2] for item in current] + [normalized]
            candidate_text = self._join_caption_tokens(candidate_tokens)
            candidate_words = len(candidate_tokens)
            candidate_duration = end - current[0][0]

            should_break = False
            if gap >= 0.38 and len(current) >= min_words:
                should_break = True
            if gap >= 0.22 and self._token_has_terminal_punctuation(str(current[-1][2])):
                should_break = True
            if candidate_words > max_words:
                should_break = True
            if len(candidate_text) > max_chars:
                should_break = True
            if candidate_duration > max_seconds:
                should_break = True

            if should_break:
                flush_current()

            current.append((start, end, normalized))

            if self._token_has_terminal_punctuation(normalized):
                flush_current()

        flush_current()

        if not chunks:
            return []

        stabilized: list[tuple[float, float, str]] = []
        for index, (start, end, text) in enumerate(chunks):
            next_start = chunks[index + 1][0] if index + 1 < len(chunks) else None
            target_end = end
            if (end - start) < min_seconds:
                target_end = start + min_seconds
                if next_start is not None:
                    target_end = min(target_end, max(start + 0.08, next_start - 0.03))
            if target_end <= start:
                target_end = end
            stabilized.append((start, target_end, text))

        return stabilized

    def _tokenize_caption_text(self, text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[.,!?;:]", text)

    def _join_caption_tokens(self, tokens: list[str]) -> str:
        punctuation = {".", ",", "!", "?", ";", ":"}
        out = ""
        for token in tokens:
            if not out:
                out = token
            elif token in punctuation:
                out += token
            else:
                out += " " + token
        return out.strip()

    def _token_has_terminal_punctuation(self, token: str) -> bool:
        return token.endswith((".", "!", "?", ";"))

    def _update_caption_stats(self, captions: list[tuple[float, float, str]]) -> None:
        if not captions:
            self.caption_stats["entries"] = 0
            self.caption_stats["avg_words_per_entry"] = 0.0
            return

        word_counts = [len(text.split()) for _, _, text in captions if text.strip()]
        durations = [max(0.01, end - start) for start, end, _ in captions]
        avg_words = (sum(word_counts) / len(word_counts)) if word_counts else 0.0
        avg_duration = (sum(durations) / len(durations)) if durations else 0.0

        self.caption_stats["entries"] = len(captions)
        self.caption_stats["avg_words_per_entry"] = round(avg_words, 2)
        self.caption_stats["avg_duration_seconds"] = round(avg_duration, 2)
        self.caption_stats["style"] = self.config.caption_style

    def _write_srt(self, srt_path: Path, captions: list[tuple[float, float, str]]) -> None:
        lines: list[str] = []
        for idx, (start, end, text) in enumerate(captions, start=1):
            lines.append(str(idx))
            lines.append(f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}")
            lines.append(text)
            lines.append("")
        self._write_text(srt_path, "\n".join(lines))

    def _write_ass(self, ass_path: Path, captions: list[tuple[float, float, str]]) -> None:
        base_scale = 0.046 if self.config.caption_style == "engagement" else 0.04
        font_size = max(22, int(self.config.height * base_scale * self.config.caption_font_scale))
        margin_v = max(18, int(self.config.height * self.config.caption_bottom_ratio))
        outline = 2.6 if self.config.caption_style == "engagement" else 2.0
        shadow = 0.8 if self.config.caption_style == "engagement" else 0.5

        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
                "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
            ),
            (
                "Style: Caption,Helvetica Neue,"
                f"{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,"
                "-1,0,0,0,100,100,0,0,1,"
                f"{outline},{shadow},2,40,40,{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for start, end, text in captions:
            safe_text = self._escape_ass_text(text)
            lines.append(
                f"Dialogue: 0,{self._format_ass_time(start)},{self._format_ass_time(end)},Caption,,0,0,0,,{safe_text}"
            )

        self._write_text(ass_path, "\n".join(lines) + "\n")

    def _intro_bookend_seconds(self) -> float:
        if not self.config.include_intro:
            return 0.0
        return max(0.0, float(self.config.intro_seconds))

    def _outro_bookend_seconds(self) -> float:
        if not self.config.include_outro:
            return 0.0
        return max(0.0, float(self.config.outro_seconds))

    def _shift_captions(
        self,
        captions: list[tuple[float, float, str]],
        offset_seconds: float,
    ) -> list[tuple[float, float, str]]:
        if offset_seconds <= 0.0:
            return captions
        shifted: list[tuple[float, float, str]] = []
        for start, end, text in captions:
            shifted.append((start + offset_seconds, end + offset_seconds, text))
        return shifted

    def _build_timeline(self, plan: ScriptPlan) -> list[TimelineClip]:
        clips: list[TimelineClip] = []
        cursor = 0.0

        intro_seconds = self._intro_bookend_seconds()
        if intro_seconds > 0.0:
            start = cursor
            end = cursor + intro_seconds
            clips.append(
                TimelineClip(
                    scene_id="__intro",
                    clip_name="intro-card",
                    start=start,
                    end=end,
                    seconds=intro_seconds,
                    source_path=None,
                    heading=plan.title,
                )
            )
            cursor = end

        for scene in plan.scenes:
            start = cursor
            end = cursor + scene.seconds
            clips.append(
                TimelineClip(
                    scene_id=scene.scene_id,
                    clip_name=scene.clip_name,
                    start=start,
                    end=end,
                    seconds=scene.seconds,
                    source_path=scene.asset_path,
                    heading=scene.heading,
                )
            )
            cursor = end

        outro_seconds = self._outro_bookend_seconds()
        if outro_seconds > 0.0:
            start = cursor
            end = cursor + outro_seconds
            clips.append(
                TimelineClip(
                    scene_id="__outro",
                    clip_name="outro-card",
                    start=start,
                    end=end,
                    seconds=outro_seconds,
                    source_path=None,
                    heading=self.config.outro_text,
                )
            )

        return clips

    def _render_video(self, timeline: list[TimelineClip], narration_wav: Path, final_mp4: Path) -> None:
        render_dir = self.paths["tmp"] / "render"
        render_dir.mkdir(parents=True, exist_ok=True)

        render_audio = render_dir / "narration_with_bookends.wav"
        self._build_render_audio_track(
            narration_wav=narration_wav,
            output_audio=render_audio,
            intro_seconds=self._intro_bookend_seconds(),
            outro_seconds=self._outro_bookend_seconds(),
        )

        clip_files: list[Path] = []
        for idx, clip in enumerate(timeline):
            clip_path = render_dir / f"clip_{idx:04d}.mp4"
            self._render_single_clip(clip, clip_path, idx)
            clip_files.append(clip_path)

        concat_list = render_dir / "concat.txt"
        concat_lines = [f"file '{path.resolve()}'" for path in clip_files]
        self._write_text(concat_list, "\n".join(concat_lines) + "\n")

        visuals_mp4 = render_dir / "visuals.mp4"
        concat = self._run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(visuals_mp4),
            ],
            timeout=3600,
            check=False,
        )
        if concat.returncode != 0:
            raise RuntimeError(f"Failed to concat clips: {concat.stderr.strip()}")

        mixed_mp4 = render_dir / "master_with_audio.mp4"
        mux = self._run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(visuals_mp4),
                "-i",
                str(render_audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(mixed_mp4),
            ],
            timeout=1200,
            check=False,
        )
        if mux.returncode != 0:
            raise RuntimeError(f"Failed to mux narration and video: {mux.stderr.strip()}")

        if self.config.burn_subtitles:
            self._burn_subtitles(mixed_mp4, self.paths["captions_ass"], final_mp4)
        else:
            shutil.copy2(mixed_mp4, final_mp4)

    def _resolve_optional_input_path(self, raw_path: str | None, *, purpose: str) -> Path | None:
        if not raw_path:
            return None

        path = Path(str(raw_path).strip()).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()

        if not path.exists():
            self._warn(f"Configured {purpose} path does not exist: {path}")
            return None
        if path.stat().st_size <= 0:
            self._warn(f"Configured {purpose} path is empty: {path}")
            return None
        return path

    def _resolve_bookend_logo_overlay(self) -> Path | None:
        logo_path = self._resolve_optional_input_path(self.config.brand_logo_path, purpose="brand logo")
        if logo_path is None:
            return None

        ext = logo_path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            return logo_path

        if ext != ".svg":
            self._warn(f"Unsupported brand logo format ({ext}); expected png/jpg/webp/svg.")
            return None

        target_dir = self.paths["tmp"] / "bookends"
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(str(logo_path).encode("utf-8")).hexdigest()[:12]
        rasterized = target_dir / f"brand-logo-{digest}.png"
        if rasterized.exists() and rasterized.stat().st_size > 0:
            return rasterized

        result = self._run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(logo_path),
                "-frames:v",
                "1",
                str(rasterized),
            ],
            timeout=120,
            check=False,
        )
        if result.returncode == 0 and rasterized.exists() and rasterized.stat().st_size > 0:
            return rasterized

        if shutil.which("sips") is not None:
            sips_result = self._run_command(
                [
                    "sips",
                    "-s",
                    "format",
                    "png",
                    str(logo_path),
                    "--out",
                    str(rasterized),
                ],
                timeout=60,
                check=False,
            )
            if sips_result.returncode == 0 and rasterized.exists() and rasterized.stat().st_size > 0:
                return rasterized

        self._warn(
            f"Could not rasterize brand logo SVG ({logo_path.name}); "
            "intro/outro will render without logo overlay."
        )
        return None

    def _bookend_allows_scene_background_fallback(self) -> bool:
        style = self._normalized_bookend_style()
        if style == "brand-image-motion":
            return bool(self.config.brand_use_scene_fallback)
        return True

    def _prepare_bookend_backgrounds(self, plan: ScriptPlan) -> None:
        self._intro_bookend_background = None
        self._outro_bookend_background = None
        self._bookend_logo_overlay = self._resolve_bookend_logo_overlay()

        configured_intro = self._resolve_optional_input_path(
            self.config.brand_intro_image_path,
            purpose="brand intro background",
        )
        configured_outro = self._resolve_optional_input_path(
            self.config.brand_outro_image_path,
            purpose="brand outro background",
        )
        if configured_intro is not None:
            self._intro_bookend_background = configured_intro
        if configured_outro is not None:
            self._outro_bookend_background = configured_outro
        if self._outro_bookend_background is None and self._intro_bookend_background is not None:
            self._outro_bookend_background = self._intro_bookend_background

        if (
            self._intro_bookend_background is not None
            and self._outro_bookend_background is not None
            and not self._bookend_allows_scene_background_fallback()
        ):
            return

        if not self._bookend_allows_scene_background_fallback():
            return

        scene_assets: list[Path] = []
        for scene in plan.scenes:
            if not scene.asset_path:
                continue
            source = Path(scene.asset_path)
            if source.exists():
                scene_assets.append(source)

        if not scene_assets:
            return

        if self._intro_bookend_background is None:
            self._intro_bookend_background = self._resolve_bookend_background(scene_assets[0], "intro")
        if self._outro_bookend_background is None:
            self._outro_bookend_background = self._resolve_bookend_background(scene_assets[-1], "outro")

        if self._outro_bookend_background is None and self._intro_bookend_background is not None:
            self._outro_bookend_background = self._intro_bookend_background

    def _resolve_bookend_background(self, source: Path, tag: str) -> Path | None:
        ext = source.suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            return source

        if ext not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
            return None

        target_dir = self.paths["tmp"] / "bookends"
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:12]
        frame_path = target_dir / f"{tag}-{digest}.jpg"
        if frame_path.exists() and frame_path.stat().st_size > 0:
            return frame_path

        for seek in (1.0, 0.0):
            command = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{seek:.2f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(frame_path),
            ]
            result = self._run_command(command, timeout=180, check=False)
            if result.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 0:
                return frame_path

        self._warn(f"Could not extract {tag} background frame from {source.name}; using default card background.")
        return None

    def _write_clip_catalog(self, plan: ScriptPlan, rights: list[AssetRight]) -> None:
        rights_by_scene: dict[str, AssetRight] = {item.scene_id: item for item in rights}
        clips: list[dict[str, Any]] = []
        for scene in plan.scenes:
            right = rights_by_scene.get(scene.scene_id)
            clips.append(
                {
                    "scene_id": scene.scene_id,
                    "clip_name": scene.clip_name,
                    "heading": scene.heading,
                    "seconds": round(scene.seconds, 3),
                    "search_terms": list(scene.search_terms),
                    "asset_provider": scene.asset_provider,
                    "asset_path": scene.asset_path,
                    "source_asset_id": right.source_asset_id if right else None,
                    "source_url": right.source_url if right else None,
                    "creator_name": right.creator_name if right else None,
                }
            )

        payload = {
            "title": plan.title,
            "summary": plan.summary,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "asset_keywords": list(self.config.asset_keywords),
            "clips": clips,
        }
        self._write_json(self.paths["clip_catalog"], payload)

    def _render_single_clip(self, clip: TimelineClip, output_clip: Path, index: int) -> None:
        duration = max(0.3, clip.seconds)
        if clip.scene_id == "__intro":
            include_text = True
            command = self._intro_clip_command(
                output_clip=output_clip,
                duration=duration,
                title=clip.heading,
                background_image=self._intro_bookend_background,
                logo_image=self._bookend_logo_overlay,
                include_text=include_text,
            )
            result = self._run_command(command, timeout=900, check=False)
            if result.returncode != 0 and self._is_drawtext_missing_error(result.stderr):
                self._ffmpeg_drawtext_available = False
                raise RuntimeError(
                    "ffmpeg drawtext filter is required for intro/outro text rendering. "
                    "Install an ffmpeg build with drawtext support or disable intro/outro bookends."
                )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to render intro clip: {result.stderr.strip()}")
            return

        if clip.scene_id == "__outro":
            include_text = True
            command = self._outro_clip_command(
                output_clip=output_clip,
                duration=duration,
                text=clip.heading,
                background_image=self._outro_bookend_background,
                logo_image=self._bookend_logo_overlay,
                include_text=include_text,
            )
            result = self._run_command(command, timeout=900, check=False)
            if result.returncode != 0 and self._is_drawtext_missing_error(result.stderr):
                self._ffmpeg_drawtext_available = False
                raise RuntimeError(
                    "ffmpeg drawtext filter is required for intro/outro text rendering. "
                    "Install an ffmpeg build with drawtext support or disable intro/outro bookends."
                )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to render outro clip: {result.stderr.strip()}")
            return

        source = Path(clip.source_path) if clip.source_path else None
        vf_fallback = self._base_clip_vf()

        if source and source.exists():
            ext = source.suffix.lower()
            if ext in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
                vf_primary = self._clip_vf(source_kind="video", index=index)
                vf_retry = vf_fallback

                input_args = ["-i", str(source)]
                source_duration: float | None = None
                try:
                    source_duration = self._media_duration(source)
                except Exception as exc:
                    self._log(f"Could not probe source duration for {clip.scene_id}: {exc}")

                if source_duration is not None and source_duration > duration + 0.08:
                    max_seek = max(0.0, source_duration - duration)
                    if max_seek > 0:
                        bucket = self._stable_pivot(f"{clip.scene_id}:{source.name}", 1000)
                        seek_ratio = float(bucket) / 999.0
                        seek_seconds = max_seek * seek_ratio
                        input_args = ["-ss", f"{seek_seconds:.3f}", "-i", str(source)]
                elif source_duration is not None and source_duration < max(0.2, duration - 0.05):
                    slow_factor = min(8.0, max(1.0, duration / max(0.05, source_duration)))
                    vf_primary = f"setpts={slow_factor:.6f}*PTS,{vf_primary}"
                    vf_retry = f"setpts={slow_factor:.6f}*PTS,{vf_fallback}"

                command = [
                    "ffmpeg",
                    "-y",
                    *input_args,
                    "-t",
                    f"{duration:.3f}",
                    "-vf",
                    vf_primary,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    str(output_clip),
                ]
                fallback = [*command]
                fallback[fallback.index("-vf") + 1] = vf_retry
            elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
                vf_primary = self._clip_vf(source_kind="image", index=index)
                command = [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(source),
                    "-t",
                    f"{duration:.3f}",
                    "-vf",
                    vf_primary,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    str(output_clip),
                ]
                fallback = [*command]
                fallback[fallback.index("-vf") + 1] = vf_fallback
            else:
                vf_primary = self._clip_vf(source_kind="placeholder", index=index)
                command = self._placeholder_clip_command(output_clip, duration, index, vf_primary)
                fallback = self._placeholder_clip_command(output_clip, duration, index, vf_fallback)
        else:
            vf_primary = self._clip_vf(source_kind="placeholder", index=index)
            command = self._placeholder_clip_command(output_clip, duration, index, vf_primary)
            fallback = self._placeholder_clip_command(output_clip, duration, index, vf_fallback)

        result = self._run_command(command, timeout=900, check=False)
        if result.returncode != 0:
            self._warn(
                f"Primary visual filter failed for {clip.scene_id}; retrying simplified filter. "
                f"Details: {result.stderr.strip()}"
            )
            result = self._run_command(fallback, timeout=900, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to render clip {clip.scene_id}: {result.stderr.strip()}")

    def _intro_clip_command(
        self,
        output_clip: Path,
        duration: float,
        title: str,
        background_image: Path | None = None,
        logo_image: Path | None = None,
        include_text: bool = True,
    ) -> list[str]:
        style = self._normalized_bookend_style()
        palette = self._bookend_palette(style=style, is_intro=True)
        wrapped_title = self._wrap_bookend_text(title or "Explainer")
        title_font = self._bookend_title_font_size(wrapped_title)
        brand_label = self._bookend_channel_name()
        intro_tagline = self._normalized_bookend_tagline(self.config.intro_tagline)

        title_file = output_clip.with_suffix(".intro_title.txt")
        brand_file = output_clip.with_suffix(".intro_brand.txt")
        subtitle_file = output_clip.with_suffix(".intro_subtitle.txt")
        self._write_text(title_file, wrapped_title + "\n")
        self._write_text(brand_file, brand_label + "\n")
        self._write_text(subtitle_file, intro_tagline + "\n")

        title_textfile = self._escape_drawtext_path(title_file)
        brand_textfile = self._escape_drawtext_path(brand_file)
        subtitle_textfile = self._escape_drawtext_path(subtitle_file)

        title_lines = max(1, len([line for line in wrapped_title.splitlines() if line.strip()]))
        title_y = int(self.config.height * (0.34 if title_lines >= 3 else 0.37))
        subtitle_y = int(self.config.height * 0.65)
        subtitle_font = max(20, int(self.config.height * 0.031))

        fade = min(0.45, max(0.2, duration * 0.2))
        fade_out_start = max(0.0, duration - fade)

        if style == "brand-image-motion":
            title_font = max(28, int(title_font * 0.84))
            subtitle_font = max(16, int(self.config.height * 0.024))
            brand_font = max(16, int(self.config.height * 0.022))
            panel_x = int(self.config.width * 0.08)
            panel_y = int(self.config.height * 0.12)
            panel_w = int(self.config.width * 0.42)
            panel_h = int(self.config.height * 0.72)
            accent_w = max(4, int(self.config.width * 0.006))
            title_x = panel_x + int(self.config.width * 0.03)
            logo_y = int(self.config.height * 0.15)
            title_y = int(self.config.height * 0.42)
            subtitle_y = int(self.config.height * 0.69)
            brand_y = int(self.config.height * 0.34)
            text_alpha = "'if(lt(t,0.22),0,if(lt(t,0.62),(t-0.22)/0.40,1))'"
            subtitle_alpha = "'if(lt(t,0.38),0,if(lt(t,0.76),(t-0.38)/0.38,1))'"

            input_args: list[str] = []
            if background_image and background_image.exists():
                input_args.extend(["-loop", "1", "-i", str(background_image)])
            else:
                input_args.extend(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
                    ]
                )

            use_logo = bool(logo_image and logo_image.exists())
            if use_logo:
                input_args.extend(["-loop", "1", "-i", str(logo_image)])

            filter_parts: list[str] = [
                (
                    f"[0:v]scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.config.width}:{self.config.height},"
                    "zoompan=z='min(zoom+0.0008,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={self.config.width}x{self.config.height}:fps={self.config.fps},"
                    "eq=contrast=1.05:saturation=1.08:brightness=0.015,"
                    f"drawbox=x=0:y=0:w=iw:h=ih:color={palette['overlay']}:t=fill,"
                    f"drawbox=x={panel_x}:y={panel_y}:w={panel_w}:h={panel_h}:color={palette['panel']}:t=fill,"
                    f"drawbox=x={panel_x}:y={panel_y + int(self.config.height * 0.08)}:w={accent_w}:h={int(self.config.height * 0.38)}:color={palette['accent']}:t=fill,"
                    f"drawbox=x={panel_x + int(self.config.width * 0.03)}:y={int(self.config.height * 0.64)}:"
                    f"w={int(self.config.width * 0.18)}:h=2:color={palette['accent']}:t=fill[bookend_base]"
                ),
            ]

            current_label = "bookend_base"
            if use_logo:
                logo_width = int(self.config.width * 0.17)
                filter_parts.append(
                    f"[1:v]scale=w={logo_width}:h=-1,format=rgba,fade=t=in:st=0.08:d=0.26:alpha=1[bookend_logo]"
                )
                filter_parts.append(
                    f"[{current_label}][bookend_logo]overlay=x={title_x}:y={logo_y}:format=auto[bookend_logo_out]"
                )
                current_label = "bookend_logo_out"

            if include_text:
                text_parts = [
                    f"drawtext=textfile='{brand_textfile}':fontcolor={palette['subtitle_color']}:fontsize={brand_font}:"
                    f"x={title_x}:y={brand_y}:alpha={text_alpha}:shadowcolor=black@0.75:shadowx=1:shadowy=1",
                    f"drawtext=textfile='{title_textfile}':fontcolor={palette['title_color']}:fontsize={title_font}:"
                    f"x={title_x}:y={title_y}:line_spacing=8:alpha={text_alpha}:"
                    "shadowcolor=black@0.88:shadowx=2:shadowy=2",
                ]
                if intro_tagline:
                    text_parts.append(
                        f"drawtext=textfile='{subtitle_textfile}':fontcolor={palette['subtitle_color']}:"
                        f"fontsize={subtitle_font}:x={title_x}:y={subtitle_y}:alpha={subtitle_alpha}:"
                        "shadowcolor=black@0.72:shadowx=1:shadowy=1"
                    )
                filter_parts.append(
                    f"[{current_label}]{','.join(text_parts)}[bookend_text]"
                )
                current_label = "bookend_text"

            filter_parts.append(
                f"[{current_label}]fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[v]"
            )

            return [
                "ffmpeg",
                "-y",
                *input_args,
                "-t",
                f"{duration:.3f}",
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(output_clip),
            ]

        input_args: list[str]
        visual_prefix = ""
        if background_image and background_image.exists():
            input_args = ["-loop", "1", "-i", str(background_image)]
            visual_prefix = (
                f"scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=increase,"
                f"crop={self.config.width}:{self.config.height},fps={self.config.fps},"
            )
        else:
            input_args = [
                "-f",
                "lavfi",
                "-i",
                f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
            ]

        text_overlay = ""
        if include_text:
            text_overlay = (
                f"drawtext=textfile='{title_textfile}':fontcolor={palette['title_color']}:fontsize={title_font}:"
                f"x=(w-text_w)/2:y={title_y}:line_spacing=10:shadowcolor=black@0.9:shadowx=2:shadowy=2,"
                f"drawtext=textfile='{subtitle_textfile}':fontcolor={palette['subtitle_color']}:fontsize={subtitle_font}:"
                f"x=(w-text_w)/2:y={subtitle_y},"
            )

        return [
            "ffmpeg",
            "-y",
            *input_args,
            "-t",
            f"{duration:.3f}",
            "-vf",
            (
                f"{visual_prefix}"
                f"drawbox=x=0:y=0:w=iw:h=ih:color={palette['overlay']}:t=fill,"
                f"drawbox=x=iw*0.11:y=ih*0.22:w=iw*0.78:h=ih*0.56:color={palette['panel']}:t=fill,"
                f"drawbox=x=iw*0.16:y=ih*0.245:w=iw*0.68:h=2:color={palette['accent']}:t=fill,"
                f"{text_overlay}"
                f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(output_clip),
        ]

    def _outro_clip_command(
        self,
        output_clip: Path,
        duration: float,
        text: str,
        background_image: Path | None = None,
        logo_image: Path | None = None,
        include_text: bool = True,
    ) -> list[str]:
        style = self._normalized_bookend_style()
        palette = self._bookend_palette(style=style, is_intro=False)
        wrapped_title = self._wrap_bookend_text(text or "Thanks for watching")
        title_font = self._bookend_title_font_size(wrapped_title)
        brand_label = self._bookend_channel_name()
        outro_tagline = self._normalized_bookend_tagline(self.config.outro_tagline)

        outro_file = output_clip.with_suffix(".outro_title.txt")
        brand_file = output_clip.with_suffix(".outro_brand.txt")
        subtitle_file = output_clip.with_suffix(".outro_subtitle.txt")
        self._write_text(outro_file, wrapped_title + "\n")
        self._write_text(brand_file, brand_label + "\n")
        self._write_text(subtitle_file, outro_tagline + "\n")

        outro_textfile = self._escape_drawtext_path(outro_file)
        brand_textfile = self._escape_drawtext_path(brand_file)
        sub_textfile = self._escape_drawtext_path(subtitle_file)

        title_lines = max(1, len([line for line in wrapped_title.splitlines() if line.strip()]))
        title_y = int(self.config.height * (0.40 if title_lines >= 3 else 0.43))
        subtitle_y = int(self.config.height * 0.63)
        subtitle_font = max(20, int(self.config.height * 0.03))

        fade = min(0.45, max(0.2, duration * 0.2))
        fade_out_start = max(0.0, duration - fade)

        if style == "brand-image-motion":
            title_font = max(28, int(title_font * 0.8))
            subtitle_font = max(16, int(self.config.height * 0.024))
            brand_font = max(16, int(self.config.height * 0.021))
            header_x = int(self.config.width * 0.09)
            header_y = int(self.config.height * 0.13)
            logo_y = int(self.config.height * 0.08)
            title_y = int(self.config.height * 0.19)
            subtitle_y = int(self.config.height * 0.31)
            left_box_x = int(self.config.width * 0.08)
            right_box_x = int(self.config.width * 0.56)
            box_y = int(self.config.height * 0.39)
            box_w = int(self.config.width * 0.30)
            box_h = int(self.config.height * 0.33)
            cta_x = int(self.config.width * 0.36)
            cta_y = int(self.config.height * 0.83)
            cta_w = int(self.config.width * 0.28)
            cta_h = int(self.config.height * 0.08)
            label_y = box_y + box_h + int(self.config.height * 0.03)
            text_alpha = "'if(lt(t,0.18),0,if(lt(t,0.55),(t-0.18)/0.37,1))'"

            input_args: list[str] = []
            if background_image and background_image.exists():
                input_args.extend(["-loop", "1", "-i", str(background_image)])
            else:
                input_args.extend(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
                    ]
                )

            use_logo = bool(logo_image and logo_image.exists())
            if use_logo:
                input_args.extend(["-loop", "1", "-i", str(logo_image)])

            filter_parts: list[str] = [
                (
                    f"[0:v]scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.config.width}:{self.config.height},"
                    "zoompan=z='min(zoom+0.0007,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={self.config.width}x{self.config.height}:fps={self.config.fps},"
                    "eq=contrast=1.04:saturation=1.05:brightness=0.01,"
                    f"drawbox=x=0:y=0:w=iw:h=ih:color={palette['overlay']}:t=fill,"
                    f"drawbox=x={left_box_x}:y={box_y}:w={box_w}:h={box_h}:color={palette['accent']}:t=4,"
                    f"drawbox=x={right_box_x}:y={box_y}:w={box_w}:h={box_h}:color={palette['accent']}:t=4,"
                    f"drawbox=x={cta_x}:y={cta_y}:w={cta_w}:h={cta_h}:color={palette['panel']}:t=fill,"
                    f"drawbox=x={header_x}:y={int(self.config.height * 0.12)}:w={int(self.config.width * 0.20)}:h=2:"
                    f"color={palette['accent']}:t=fill[bookend_base]"
                ),
            ]

            current_label = "bookend_base"
            if use_logo:
                logo_width = int(self.config.width * 0.12)
                filter_parts.append(
                    f"[1:v]scale=w={logo_width}:h=-1,format=rgba,fade=t=in:st=0.05:d=0.22:alpha=1[bookend_logo]"
                )
                filter_parts.append(
                    f"[{current_label}][bookend_logo]overlay=x={header_x}:y={logo_y}:format=auto[bookend_logo_out]"
                )
                current_label = "bookend_logo_out"

            if include_text:
                text_parts = [
                    f"drawtext=textfile='{brand_textfile}':fontcolor={palette['subtitle_color']}:fontsize={brand_font}:"
                    f"x={header_x + int(self.config.width * 0.14)}:y={header_y}:alpha={text_alpha}:"
                    "shadowcolor=black@0.70:shadowx=1:shadowy=1",
                    f"drawtext=textfile='{outro_textfile}':fontcolor={palette['title_color']}:fontsize={title_font}:"
                    f"x={header_x}:y={title_y}:line_spacing=8:alpha={text_alpha}:"
                    "shadowcolor=black@0.85:shadowx=2:shadowy=2",
                    f"drawtext=text='WATCH NEXT':fontcolor={palette['subtitle_color']}:fontsize={subtitle_font}:"
                    f"x={left_box_x}:y={label_y}:alpha={text_alpha}",
                    f"drawtext=text='MORE TO EXPLORE':fontcolor={palette['subtitle_color']}:fontsize={subtitle_font}:"
                    f"x={right_box_x}:y={label_y}:alpha={text_alpha}",
                ]
                if outro_tagline:
                    text_parts.append(
                        f"drawtext=textfile='{sub_textfile}':fontcolor={palette['subtitle_color']}:"
                        f"fontsize={subtitle_font}:x=(w-text_w)/2:y={cta_y + int(cta_h * 0.25)}:alpha={text_alpha}:"
                        "shadowcolor=black@0.68:shadowx=1:shadowy=1"
                    )
                filter_parts.append(
                    f"[{current_label}]{','.join(text_parts)}[bookend_text]"
                )
                current_label = "bookend_text"

            filter_parts.append(
                f"[{current_label}]fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[v]"
            )

            return [
                "ffmpeg",
                "-y",
                *input_args,
                "-t",
                f"{duration:.3f}",
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(output_clip),
            ]

        input_args: list[str]
        visual_prefix = ""
        if background_image and background_image.exists():
            input_args = ["-loop", "1", "-i", str(background_image)]
            visual_prefix = (
                f"scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=increase,"
                f"crop={self.config.width}:{self.config.height},fps={self.config.fps},"
            )
        else:
            input_args = [
                "-f",
                "lavfi",
                "-i",
                f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
            ]

        text_overlay = ""
        if include_text:
            text_overlay = (
                f"drawtext=textfile='{outro_textfile}':fontcolor={palette['title_color']}:fontsize={title_font}:"
                f"x=(w-text_w)/2:y={title_y}:line_spacing=10:shadowcolor=black@0.9:shadowx=2:shadowy=2,"
                f"drawtext=textfile='{sub_textfile}':fontcolor={palette['subtitle_color']}:fontsize={subtitle_font}:"
                f"x=(w-text_w)/2:y={subtitle_y},"
            )

        return [
            "ffmpeg",
            "-y",
            *input_args,
            "-t",
            f"{duration:.3f}",
            "-vf",
            (
                f"{visual_prefix}"
                f"drawbox=x=0:y=0:w=iw:h=ih:color={palette['overlay']}:t=fill,"
                f"drawbox=x=iw*0.10:y=ih*0.26:w=iw*0.80:h=ih*0.50:color={palette['panel']}:t=fill,"
                f"drawbox=x=iw*0.16:y=ih*0.735:w=iw*0.68:h=2:color={palette['accent']}:t=fill,"
                f"{text_overlay}"
                f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(output_clip),
        ]

    def _normalized_bookend_style(self) -> str:
        style = str(self.config.bookend_style or "minimal-clean").strip().lower()
        if style not in {"minimal-clean", "cinematic-subtle", "brand-image-motion"}:
            return "minimal-clean"
        return style

    def _ffmpeg_supports_drawtext(self) -> bool:
        cached = self._ffmpeg_drawtext_available
        if cached is not None:
            return bool(cached)

        result = self._run_command(["ffmpeg", "-hide_banner", "-filters"], timeout=30, check=False)
        catalog = f"{result.stdout}\n{result.stderr}".lower()
        available = "drawtext" in catalog
        self._ffmpeg_drawtext_available = available
        return available

    def _is_drawtext_missing_error(self, error_text: str) -> bool:
        text = (error_text or "").lower()
        return "no such filter" in text and "drawtext" in text

    def _ffmpeg_supports_subtitles_filter(self) -> bool:
        cached = self._ffmpeg_subtitles_available
        if cached is not None:
            return bool(cached)

        result = self._run_command(["ffmpeg", "-hide_banner", "-filters"], timeout=30, check=False)
        catalog = f"{result.stdout}\n{result.stderr}".lower()
        available = bool(re.search(r"\bsubtitles\b", catalog))
        self._ffmpeg_subtitles_available = available
        return available

    def _bookend_palette(self, style: str, is_intro: bool) -> dict[str, str]:
        if style == "brand-image-motion":
            if is_intro:
                return {
                    "base": "#0c1018",
                    "overlay": "#05080f@0.42",
                    "panel": "#111827@0.58",
                    "accent": "#4dc0ff@0.85",
                    "title_color": "white",
                    "subtitle_color": "white@0.86",
                }
            return {
                "base": "#0d1118",
                "overlay": "#060a10@0.40",
                "panel": "#111827@0.72",
                "accent": "#73f3c6@0.86",
                "title_color": "white",
                "subtitle_color": "white@0.86",
            }

        if style == "cinematic-subtle":
            if is_intro:
                return {
                    "base": "#0b1220",
                    "overlay": "#0b1020@0.42",
                    "panel": "#111a2a@0.66",
                    "accent": "#7dd3fc@0.75",
                    "title_color": "white",
                    "subtitle_color": "white@0.85",
                }
            return {
                "base": "#0a1322",
                "overlay": "#0a1120@0.40",
                "panel": "#101b2a@0.64",
                "accent": "#93c5fd@0.72",
                "title_color": "white",
                "subtitle_color": "white@0.82",
            }

        if is_intro:
            return {
                "base": "#132033",
                "overlay": "#0f172a@0.32",
                "panel": "#111827@0.58",
                "accent": "#60a5fa@0.70",
                "title_color": "white",
                "subtitle_color": "white@0.84",
            }
        return {
            "base": "#151c2b",
            "overlay": "#0f172a@0.30",
            "panel": "#111827@0.58",
            "accent": "#93c5fd@0.66",
            "title_color": "white",
            "subtitle_color": "white@0.82",
        }

    def _bookend_channel_name(self) -> str:
        normalized = re.sub(r"\s+", " ", str(self.config.channel_name or "").strip())
        if not normalized:
            return "IMAGINE"
        return normalized[:40]

    def _normalized_bookend_tagline(self, text: str | None) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "").strip())
        return normalized[:80]

    def _wrap_bookend_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if not normalized:
            return "Explainer"

        max_chars = max(18, min(48, int(self.config.width / 32)))
        words = normalized.split(" ")
        lines: list[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = word
            else:
                lines.append(word[:max_chars])
                current = word[max_chars:]

        if current:
            lines.append(current)

        if len(lines) > 4:
            merged = lines[:3]
            merged.append(" ".join(lines[3:]))
            lines = merged

        return "\n".join(line.strip() for line in lines if line.strip())

    def _bookend_title_font_size(self, wrapped_text: str) -> int:
        lines = [line for line in wrapped_text.splitlines() if line.strip()]
        longest = max((len(line) for line in lines), default=12)
        line_count = max(1, len(lines))

        base = int(self.config.height * 0.066)
        width_fit = int((self.config.width * 0.82) / max(1.0, longest * 0.58))
        line_penalty = 1.0 - max(0, line_count - 2) * 0.10
        final = int(min(base, width_fit) * max(0.68, line_penalty))
        return max(24, min(64, final))

    def _placeholder_clip_command(self, output_clip: Path, duration: float, index: int, vf: str) -> list[str]:
        hue_shift = (index * 33) % 360
        return [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s={self.config.width}x{self.config.height}:r={self.config.fps}",
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"hue=h={hue_shift}:s=0.45,{vf}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(output_clip),
        ]

    def _burn_subtitles(self, input_mp4: Path, subtitles_ass: Path, output_mp4: Path) -> None:
        if not subtitles_ass.exists() or subtitles_ass.stat().st_size == 0:
            self._warn("Subtitle burn-in skipped because captions.ass is missing or empty.")
            shutil.copy2(input_mp4, output_mp4)
            return

        if not self._ffmpeg_supports_subtitles_filter():
            raise RuntimeError(
                "ffmpeg subtitles filter is required for burned subtitles. "
                "Install an ffmpeg build with libass/subtitles support or disable burned subtitles."
            )

        filter_value = self._ffmpeg_subtitles_filter(subtitles_ass)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_mp4),
            "-vf",
            filter_value,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(output_mp4),
        ]
        result = self._run_command(command, timeout=2400, check=False)
        if result.returncode != 0:
            lowered = (result.stderr or "").lower()
            if "no such filter" in lowered and "subtitles" in lowered:
                self._ffmpeg_subtitles_available = False
                raise RuntimeError(
                    "ffmpeg subtitles filter is required for burned subtitles. "
                    "Install an ffmpeg build with libass/subtitles support or disable burned subtitles."
                )
            self._warn(f"Subtitle burn-in failed; shipping video without burned subtitles. Details: {result.stderr.strip()}")
            shutil.copy2(input_mp4, output_mp4)

    def _ffmpeg_subtitles_filter(self, subtitles_ass: Path) -> str:
        value = str(subtitles_ass.resolve())
        value = value.replace("\\", "\\\\")
        value = value.replace(":", "\\:")
        value = value.replace("'", "\\'")
        value = value.replace(",", "\\,")
        value = value.replace(";", "\\;")
        return f"subtitles=filename='{value}'"

    def _build_manifest(self, plan: ScriptPlan, rights: list[AssetRight]) -> dict[str, Any]:
        out_files = [
            self.paths["final_mp4"],
            self.paths["final_srt"],
            self.paths["captions_ass"],
            self.paths["script"],
            self.paths["timeline"],
            self.paths["clip_catalog"],
        ]
        outputs: list[dict[str, Any]] = []
        for path in out_files:
            if path.exists():
                outputs.append(
                    {
                        "path": str(path.resolve()),
                        "sha256": self._file_sha256(path),
                        "bytes": path.stat().st_size,
                    }
                )

        tools = [
            {
                "tool_name": "ffmpeg",
                "tool_version": self._tool_version(["ffmpeg", "-version"]),
                "tool_license": "Depends on build",
            },
            {
                "tool_name": "ffprobe",
                "tool_version": self._tool_version(["ffprobe", "-version"]),
                "tool_license": "Depends on build",
            },
            {
                "tool_name": "ollama",
                "tool_version": self._tool_version(["ollama", "--version"]),
                "tool_license": "Runtime license applies",
            },
        ]

        return {
            "manifest_version": 1,
            "project_id": self.config.project_dir.name,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "app_version": "0.1.0",
            "pipeline_version": "v1-local-720p",
            "config": {
                "fast_mode": self.config.fast_mode,
                "minutes": self.config.minutes,
                "asset_keywords": list(self.config.asset_keywords),
                "resolution": f"{self.config.width}x{self.config.height}",
                "fps": self.config.fps,
                "video_effects": self.config.video_effects,
                "include_intro": self.config.include_intro,
                "include_outro": self.config.include_outro,
                "intro_seconds": self.config.intro_seconds,
                "outro_seconds": self.config.outro_seconds,
                "outro_text": self.config.outro_text,
                "channel_name": self.config.channel_name,
                "intro_tagline": self.config.intro_tagline,
                "outro_tagline": self.config.outro_tagline,
                "bookend_style": self.config.bookend_style,
                "brand_logo_path": self.config.brand_logo_path,
                "brand_intro_image_path": self.config.brand_intro_image_path,
                "brand_outro_image_path": self.config.brand_outro_image_path,
                "brand_use_scene_fallback": self.config.brand_use_scene_fallback,
                "strict_commercial_safe": self.config.strict_commercial_safe,
                "script_engine": self.config.script_engine,
                "tts_engine": self.config.tts_engine,
                "caption_engine": self.config.caption_engine,
                "caption_style": self.config.caption_style,
                "burn_subtitles": self.config.burn_subtitles,
                "caption_font_scale": self.config.caption_font_scale,
                "caption_bottom_ratio": self.config.caption_bottom_ratio,
                "duration_tolerance_ratio": self.config.duration_tolerance_ratio,
                "target_speech_wpm": self.config.target_speech_wpm,
                "voice_profile": self.config.voice_profile,
                "voice_speed": self.config.voice_speed,
                "piper_voice_id": self.config.piper_voice_id,
                "piper_speaker_id": self.config.piper_speaker_id,
                "ollama_model": self.config.ollama_model,
            },
            "inputs": {
                "prompt_file": str(self.paths["prompt"].resolve()),
                "prompt_sha256": self._file_sha256(self.paths["prompt"]),
            },
            "assets": [record.to_dict() for record in rights],
            "models": [
                {
                    "model_id": self.config.ollama_model if self.config.script_engine == "ollama" else "template-script",
                    "provider": "ollama" if self.config.script_engine == "ollama" else "local-template",
                    "model_license": "Verify model-specific license",
                },
                {
                    "model_id": self.config.tts_engine,
                    "provider": "local",
                    "model_license": "Verify voice/model license",
                },
            ],
            "tools": tools,
            "outputs": outputs,
            "policy_decisions": [
                {
                    "rule_id": "strict-commercial-safe",
                    "result": "allow" if self.config.strict_commercial_safe else "warn",
                    "reason": "Strict mode enabled" if self.config.strict_commercial_safe else "Strict mode disabled",
                }
            ],
        }

    def _run_stage(self, key: str, label: str, fn: Any) -> Any:
        self._log(label)
        start = dt.datetime.now(dt.timezone.utc)
        result = fn()
        elapsed = (dt.datetime.now(dt.timezone.utc) - start).total_seconds()
        self.stage_times[key] = round(elapsed, 3)
        self._log(f"{key} completed in {elapsed:.2f}s")
        return result

    def _update_pacing_post_tts(self, narration_text: str, audio_duration: float, adjust_passes: int) -> None:
        words = self._word_count_text(narration_text)
        spoken_minutes = max(0.01, audio_duration / 60.0)
        effective_wpm = float(words) / spoken_minutes
        self.pacing_stats["final_words"] = words
        self.pacing_stats["audio_seconds"] = round(audio_duration, 3)
        self.pacing_stats["effective_wpm"] = round(effective_wpm, 2)
        self.pacing_stats["adjustment_passes"] = adjust_passes

    def _ollama_server_ready(self) -> bool:
        result = self._run_command(["ollama", "list"], timeout=10, check=False)
        return result.returncode == 0

    def _clean_narration_text(self, text: str) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        cleaned_parts: list[str] = []

        for paragraph in paragraphs:
            cleaned = re.sub(r"\s+", " ", paragraph).strip()
            cleaned = re.sub(r"\b([A-Za-z]{3,})(\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"([?!.,])\1+", r"\1", cleaned)
            if cleaned:
                cleaned_parts.append(cleaned)

        return "\n\n".join(cleaned_parts)

    def _base_clip_vf(self) -> str:
        w = str(self.config.width)
        h = str(self.config.height)
        fps = str(self.config.fps)
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"

    def _clip_vf(self, source_kind: str, index: int) -> str:
        _ = index
        preset = str(self.config.video_effects or "clean").strip().lower()
        if preset not in {"clean", "subtle-motion", "dynamic"}:
            preset = "clean"

        if preset == "clean":
            return self._base_clip_vf()

        if source_kind == "image":
            return self._image_effect_vf(preset)
        return self._video_effect_vf(preset)

    def _video_effect_vf(self, preset: str) -> str:
        base = self._base_clip_vf()

        if preset == "subtle-motion":
            drift = (
                "crop=iw*0.985:ih*0.985:"
                "(iw-iw*0.985)/2+sin(t*0.45)*(iw*0.004):"
                "(ih-ih*0.985)/2+cos(t*0.37)*(ih*0.004)"
            )
            polish = "eq=contrast=1.03:saturation=1.06:brightness=0.01"
            return f"{drift},{base},{polish}"

        if preset == "dynamic":
            drift = (
                "crop=iw*0.97:ih*0.97:"
                "(iw-iw*0.97)/2+sin(t*0.9)*(iw*0.01):"
                "(ih-ih*0.97)/2+cos(t*0.71)*(ih*0.01)"
            )
            polish = "eq=contrast=1.06:saturation=1.12:brightness=0.015,unsharp=5:5:0.5:5:5:0.0"
            return f"{drift},{base},{polish}"

        return base

    def _image_effect_vf(self, preset: str) -> str:
        w = self.config.width
        h = self.config.height
        fps = self.config.fps

        if preset == "subtle-motion":
            return (
                "zoompan=z='min(zoom+0.00065,1.05)':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d=1:s={w}x{h}:fps={fps},"
                "eq=contrast=1.03:saturation=1.06:brightness=0.01"
            )

        if preset == "dynamic":
            return (
                "zoompan=z='min(zoom+0.0011,1.08)':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d=1:s={w}x{h}:fps={fps},"
                "eq=contrast=1.06:saturation=1.12:brightness=0.015,"
                "unsharp=5:5:0.45:5:5:0.0"
            )

        return self._base_clip_vf()

    def _write_run_report(self, status: str, outputs: dict[str, str], error: str | None = None) -> None:
        started = self._started_at or dt.datetime.now(dt.timezone.utc)
        finished = self._finished_at or dt.datetime.now(dt.timezone.utc)
        payload = {
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "total_seconds": round((finished - started).total_seconds(), 3),
            "stage_times": self.stage_times,
            "warnings": self.warnings,
            "caption_stats": self.caption_stats,
            "duration_stats": self.duration_stats,
            "pacing_stats": self.pacing_stats,
            "asset_stats": self.asset_stats,
            "optimization_stats": self.optimization_stats,
            "fallbacks": {
                "used_template_script": self.used_template_fallback,
            },
            "outputs": outputs,
        }
        if error:
            payload["error"] = error
        self._write_json(self.paths["run_report"], payload)

    def _media_duration(self, media_path: Path) -> float:
        result = self._run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

        try:
            return float((result.stdout or "0").strip())
        except ValueError as exc:
            raise RuntimeError("Could not parse media duration") from exc

    def _tool_version(self, command: list[str]) -> str | None:
        if not command:
            return None
        if shutil.which(command[0]) is None:
            return None
        result = self._run_command(command, timeout=15, check=False)
        if result.returncode != 0:
            return None
        first_line = (result.stdout or result.stderr or "").splitlines()
        if not first_line:
            return None
        return first_line[0].strip()

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first == -1 or last == -1 or last <= first:
            return None

        candidate = cleaned[first : last + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None
        return parsed

    def _run_command(
        self,
        command: list[str],
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            joined = " ".join(command)
            raise RuntimeError(f"Command failed ({joined}): {result.stderr.strip()}")
        return result

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def _write_text(self, path: Path, payload: str) -> None:
        path.write_text(payload, encoding="utf-8")

    def _file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _format_srt_time(self, seconds: float) -> str:
        total_ms = max(0, int(math.floor(seconds * 1000)))
        hours = total_ms // 3_600_000
        total_ms %= 3_600_000
        minutes = total_ms // 60_000
        total_ms %= 60_000
        secs = total_ms // 1000
        ms = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def _format_ass_time(self, seconds: float) -> str:
        total_cs = max(0, int(math.floor(seconds * 100)))
        hours = total_cs // 360000
        total_cs %= 360000
        minutes = total_cs // 6000
        total_cs %= 6000
        secs = total_cs // 100
        cs = total_cs % 100
        return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"

    def _escape_ass_text(self, text: str) -> str:
        value = text.replace("\\", r"\\")
        value = value.replace("{", r"\{")
        value = value.replace("}", r"\}")
        return value

    def _escape_drawtext_path(self, path: Path) -> str:
        value = str(path.resolve()).replace("\\", r"\\")
        value = value.replace(":", r"\:")
        value = value.replace("'", r"\'")
        value = value.replace(",", r"\,")
        value = value.replace("%", r"\%")
        return value

    def _log(self, message: str) -> None:
        self._log_with_level(message, level="INFO")

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        self._log_with_level(message, level="WARN")

    def _log_with_level(self, message: str, level: str) -> None:
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        line = f"{timestamp} [{level}] {message}"
        try:
            self.paths["run_log"].parent.mkdir(parents=True, exist_ok=True)
            with self.paths["run_log"].open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass

        if self.config.verbose or level in {"WARN", "ERROR"}:
            print(f"[local-video-mvp] {message}")
