from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any
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
        self.used_template_fallback = False
        self._ollama_ready = False
        self._started_at: dt.datetime | None = None
        self._finished_at: dt.datetime | None = None

    def run(self) -> dict[str, str]:
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")

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

                if (
                    self._duration_too_short(audio_duration)
                    and adjust_passes < self.config.max_duration_adjust_passes
                ):
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

                if (
                    self._duration_too_long(audio_duration)
                    and adjust_passes < self.config.max_duration_adjust_passes
                ):
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

            rights = self._run_stage("assets", "Stage 3/7: Resolving visual assets", lambda: self._resolve_assets(plan))
            self._write_json(self.paths["script"], plan.to_dict())

            captions = self._run_stage(
                "captions",
                "Stage 4/7: Building captions",
                lambda: self._generate_captions(plan, self.paths["narration_wav"]),
            )
            self._write_srt(self.paths["captions"], captions)
            self._write_ass(self.paths["captions_ass"], captions)

            timeline = self._run_stage("timeline", "Stage 5/7: Building timeline", lambda: self._build_timeline(plan))
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
                self._render_video(timeline, self.paths["narration_wav"], self.paths["final_mp4"])
                shutil.copy2(self.paths["captions"], self.paths["final_srt"])

            self._run_stage("render", "Stage 6/7: Rendering final video", render_stage)

            manifest = self._run_stage("manifest", "Stage 7/7: Writing rights manifest", lambda: self._build_manifest(plan, rights))
            self._write_json(self.paths["manifest"], manifest)

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "timeline": str(self.paths["timeline"].resolve()),
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
            "manifest": project_dir / "rights_manifest.json",
            "run_log": project_dir / "run.log",
            "run_report": project_dir / "run_report.json",
            "final_mp4": project_dir / "output" / "final.mp4",
            "final_srt": project_dir / "output" / "final.srt",
        }

    def _prepare_dirs(self) -> None:
        for key in ("root", "assets_cache", "tmp", "output"):
            self.paths[key].mkdir(parents=True, exist_ok=True)

    def _check_dependencies(self) -> None:
        self._require_binary("ffmpeg")
        self._require_binary("ffprobe")
        if self.config.script_engine == "ollama":
            self._require_binary("ollama")
            self._ollama_ready = self._ollama_server_ready()
            if not self._ollama_ready:
                message = "Could not connect to Ollama server; run 'ollama serve' to enable model-based scripts"
                if self.config.require_ollama:
                    raise RuntimeError(message)
                self._warn(message + ". Falling back to template script mode.")
        if self.config.tts_engine == "say":
            self._require_binary("say")

        if self.config.strict_commercial_safe and self.config.tts_engine == "say" and not self.config.allow_system_tts:
            raise RuntimeError(
                "Strict commercial-safe mode blocks system TTS fallback by default. "
                "Use --tts-engine melo or pass --allow-system-tts explicitly."
            )

    def _require_binary(self, binary_name: str) -> None:
        if shutil.which(binary_name) is None:
            raise RuntimeError(f"Missing required binary: {binary_name}")

    def _generate_script_plan(self) -> ScriptPlan:
        raw_plan: dict[str, Any] | None = None
        if self.config.script_engine == "ollama" and self._ollama_ready:
            raw_plan = self._generate_script_plan_ollama()
            if raw_plan is None:
                self._warn("Ollama plan generation failed; using template fallback")

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

            scenes.append(
                Scene(
                    scene_id=f"scene_{idx + 1:03d}",
                    heading=heading,
                    voiceover=voiceover,
                    search_terms=search_terms[:4],
                    seconds=0.0,
                )
            )

        if not scenes:
            fallback = self._generate_script_plan_template()
            return self._normalize_script_plan(fallback)

        # Set initial equal durations before voiceover timing rebalance.
        per_scene = max(self.config.min_scene_seconds, self.config.target_seconds() / len(scenes))
        for scene in scenes:
            scene.seconds = per_scene

        return ScriptPlan(title=title, summary=summary, scenes=scenes)

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

        self.duration_stats["word_count_final"] = words
        self.duration_stats["audio_seconds"] = round(audio_duration, 3)
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

        if self.config.tts_engine == "say":
            if self.config.strict_commercial_safe and not self.config.allow_system_tts:
                raise RuntimeError("System TTS fallback is blocked in strict mode unless --allow-system-tts is set")
            self._tts_with_say(chunks, output_raw_wav)
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

    def _tts_with_say(self, chunks: list[dict[str, Any]], output_raw_wav: Path) -> None:
        parts_dir = self.paths["tmp"] / "tts_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        part_files: list[Path] = []

        for idx, chunk in enumerate(chunks):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue

            temp_txt = parts_dir / f"say_{idx:04d}.txt"
            temp_aiff = parts_dir / f"say_{idx:04d}.aiff"
            temp_wav = parts_dir / f"say_{idx:04d}.wav"
            self._write_text(temp_txt, text)

            result = self._run_command(
                ["say", "-f", str(temp_txt), "-o", str(temp_aiff)],
                timeout=600,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"say command failed: {result.stderr.strip()}")

            self._standardize_wav(temp_aiff, temp_wav)
            part_files.append(temp_wav)

            pause_seconds = float(chunk.get("pause_after") or 0.0)
            if pause_seconds > 0.0:
                pause_path = parts_dir / f"pause_{idx:04d}.wav"
                self._generate_silence_wav(pause_path, pause_seconds)
                part_files.append(pause_path)

        if not part_files:
            raise RuntimeError("System TTS did not produce any audio parts")

        self._concat_wav_parts(part_files, output_raw_wav)

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

    def _generate_silence_wav(self, output_wav: Path, seconds: float) -> None:
        duration = max(0.03, float(seconds))
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
        result = self._run_command(command, timeout=120, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to generate silence audio: {result.stderr.strip()}")

    def _concat_wav_parts(self, part_files: list[Path], output_wav: Path) -> None:
        if len(part_files) == 1:
            shutil.copy2(part_files[0], output_wav)
            return

        concat_list = self.paths["tmp"] / "tts_parts" / "concat_audio.txt"
        lines = [f"file '{path.resolve()}'" for path in part_files]
        self._write_text(concat_list, "\n".join(lines) + "\n")

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
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
        result = self._run_command(command, timeout=900, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to concatenate narration chunks: {result.stderr.strip()}")

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

    def _resolve_assets(self, plan: ScriptPlan) -> list[AssetRight]:
        rights: list[AssetRight] = []
        query_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        download_failures = 0
        placeholder_scenes = 0

        provider_order = [
            ("pexels", self.config.pexels_api_key),
            ("pixabay", self.config.pixabay_api_key),
        ]

        if not any(key for _, key in provider_order):
            self._warn("No stock API keys configured (PEXELS_API_KEY / PIXABAY_API_KEY). Using generated placeholders.")

        for scene in plan.scenes:
            query = self._query_for_scene(scene)
            resolved = False
            for provider_name, api_key in provider_order:
                if not api_key:
                    continue

                cache_key = (provider_name, query.lower())
                candidate = query_cache.get(cache_key)
                if cache_key not in query_cache:
                    candidate = None
                    try:
                        if provider_name == "pexels":
                            candidate = self._search_pexels_video(scene, api_key, query=query)
                        elif provider_name == "pixabay":
                            candidate = self._search_pixabay_video(scene, api_key, query=query)
                    except Exception as exc:
                        self._log(f"{provider_name} search failed for {scene.scene_id}: {exc}")
                        candidate = None

                    query_cache[cache_key] = candidate

                if not candidate:
                    continue

                try:
                    local_path = self._download_asset(str(candidate["download_url"]))

                    scene.asset_path = str(local_path)
                    scene.asset_provider = str(candidate.get("source_platform") or "unknown")

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
                        attribution_text=(str(candidate.get("attribution_text")) if candidate.get("attribution_text") else None),
                    )
                    rights.append(right)
                    resolved = True
                    break
                except Exception as exc:
                    download_failures += 1
                    self._log(f"Asset download failed for {scene.scene_id} ({provider_name}): {exc}")

            if not resolved:
                placeholder_scenes += 1

        if download_failures > 0:
            self._warn(
                f"{download_failures} asset downloads failed (network or provider timeout). "
                "Placeholders were used for affected scenes."
            )
        if placeholder_scenes > 0 and rights:
            self._warn(f"{placeholder_scenes} scenes used placeholder visuals because no stock asset was resolved.")

        if not rights:
            self._warn("No external assets resolved for this run. Final video uses generated placeholders only.")
        return rights

    def _search_pexels_video(self, scene: Scene, api_key: str, query: str | None = None) -> dict[str, Any] | None:
        if not query:
            query = self._query_for_scene(scene)
        url = f"https://api.pexels.com/videos/search?query={quote_plus(query)}&orientation=landscape&per_page=8"
        response = self.http.get(url, headers={"Authorization": api_key}, timeout=(5, 15))
        if response.status_code != 200:
            return None

        payload = response.json()
        videos = payload.get("videos")
        if not isinstance(videos, list) or not videos:
            return None

        pivot = abs(hash(f"{scene.scene_id}:{query}")) % min(len(videos), 5)
        best = videos[pivot]
        files = best.get("video_files") if isinstance(best, dict) else None
        if not isinstance(files, list) or not files:
            return None

        files_sorted = sorted(
            [f for f in files if isinstance(f, dict) and f.get("link")],
            key=lambda f: int(f.get("width") or 0),
            reverse=True,
        )
        if not files_sorted:
            return None

        selected = files_sorted[0]
        user = best.get("user") if isinstance(best, dict) else {}
        if not isinstance(user, dict):
            user = {}

        return {
            "source_platform": "pexels",
            "source_asset_id": str(best.get("id") or ""),
            "download_url": str(selected.get("link")),
            "source_url": f"https://www.pexels.com/video/{best.get('id')}/",
            "creator_name": user.get("name"),
            "creator_profile_url": user.get("url"),
            "license_name": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "restriction_flags": [],
            "attribution_required": False,
        }

    def _search_pixabay_video(self, scene: Scene, api_key: str, query: str | None = None) -> dict[str, Any] | None:
        if not query:
            query = self._query_for_scene(scene)
        url = (
            "https://pixabay.com/api/videos/"
            f"?key={quote_plus(api_key)}&q={quote_plus(query)}&safesearch=true&per_page=8"
        )
        response = self.http.get(url, timeout=(5, 15))
        if response.status_code != 200:
            return None

        payload = response.json()
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            return None

        pivot = abs(hash(f"{scene.scene_id}:{query}")) % min(len(hits), 5)
        best = hits[pivot]
        if not isinstance(best, dict):
            return None

        videos = best.get("videos")
        if not isinstance(videos, dict):
            return None

        quality_order = ["large", "medium", "small", "tiny"]
        selected_url = None
        for key in quality_order:
            block = videos.get(key)
            if isinstance(block, dict) and block.get("url"):
                selected_url = str(block["url"])
                break
        if not selected_url:
            return None

        return {
            "source_platform": "pixabay",
            "source_asset_id": str(best.get("id") or ""),
            "download_url": selected_url,
            "source_url": str(best.get("pageURL") or ""),
            "creator_name": str(best.get("user") or "") or None,
            "creator_profile_url": None,
            "license_name": "Pixabay License",
            "license_url": "https://pixabay.com/service/license/",
            "restriction_flags": [],
            "attribution_required": False,
        }

    def _query_for_scene(self, scene: Scene) -> str:
        terms = [term.strip() for term in scene.search_terms if term.strip()]
        if terms:
            return " ".join(terms[:3])
        return scene.heading

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
            if candidate_words > max_words:
                should_break = True
            if len(candidate_text) > max_chars:
                should_break = True
            if candidate_duration > max_seconds:
                should_break = True

            if should_break:
                flush_current()

            current.append((start, end, normalized))

            if self._token_has_terminal_punctuation(normalized) and len(current) >= min_words:
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

    def _build_timeline(self, plan: ScriptPlan) -> list[TimelineClip]:
        clips: list[TimelineClip] = []
        cursor = 0.0
        for scene in plan.scenes:
            start = cursor
            end = cursor + scene.seconds
            clips.append(
                TimelineClip(
                    scene_id=scene.scene_id,
                    start=start,
                    end=end,
                    seconds=scene.seconds,
                    source_path=scene.asset_path,
                    heading=scene.heading,
                )
            )
            cursor = end
        return clips

    def _render_video(self, timeline: list[TimelineClip], narration_wav: Path, final_mp4: Path) -> None:
        render_dir = self.paths["tmp"] / "render"
        render_dir.mkdir(parents=True, exist_ok=True)

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
                str(narration_wav),
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

    def _render_single_clip(self, clip: TimelineClip, output_clip: Path, index: int) -> None:
        duration = max(0.3, clip.seconds)
        source = Path(clip.source_path) if clip.source_path else None
        vf_fallback = self._base_clip_vf()

        if source and source.exists():
            ext = source.suffix.lower()
            if ext in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
                vf_primary = self._clip_vf(source_kind="video", index=index)
                command = [
                    "ffmpeg",
                    "-y",
                    "-stream_loop",
                    "-1",
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
            self._warn(f"Subtitle burn-in failed; shipping video without burned subtitles. Details: {result.stderr.strip()}")
            shutil.copy2(input_mp4, output_mp4)

    def _ffmpeg_subtitles_filter(self, subtitles_ass: Path) -> str:
        value = str(subtitles_ass.resolve())
        value = value.replace("\\", "\\\\")
        value = value.replace(":", "\\:")
        value = value.replace("'", "\\'")
        return f"subtitles='{value}':charenc=UTF-8"

    def _build_manifest(self, plan: ScriptPlan, rights: list[AssetRight]) -> dict[str, Any]:
        out_files = [
            self.paths["final_mp4"],
            self.paths["final_srt"],
            self.paths["captions_ass"],
            self.paths["script"],
            self.paths["timeline"],
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
                "minutes": self.config.minutes,
                "resolution": f"{self.config.width}x{self.config.height}",
                "fps": self.config.fps,
                "video_effects": self.config.video_effects,
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
