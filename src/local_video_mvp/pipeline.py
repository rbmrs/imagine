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


class VideoPipeline:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.paths = self._build_paths(config.project_dir)
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "local-video-mvp/0.1"})
        self.stage_times: dict[str, float] = {}
        self.warnings: list[str] = []
        self.caption_stats: dict[str, Any] = {}
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
            self._write_json(self.paths["script"], plan.to_dict())

            narration_text = self._clean_narration_text(plan.narration_text())
            self._write_text(self.paths["narration_txt"], narration_text + "\n")

            def voice_stage() -> None:
                self._synthesize_narration(narration_text, self.paths["narration_raw"])
                self._normalize_audio(self.paths["narration_raw"], self.paths["narration_wav"])

            self._run_stage("narration", "Stage 2/7: Synthesizing narration audio", voice_stage)

            audio_duration = self._media_duration(self.paths["narration_wav"])
            self._rebalance_scene_durations(plan, audio_duration)
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
        if self.config.tts_engine == "melo":
            self._tts_with_melo(text, output_raw_wav)
            return

        if self.config.tts_engine == "say":
            if self.config.strict_commercial_safe and not self.config.allow_system_tts:
                raise RuntimeError("System TTS fallback is blocked in strict mode unless --allow-system-tts is set")
            self._tts_with_say(text, output_raw_wav)
            return

        raise RuntimeError(f"Unsupported TTS engine: {self.config.tts_engine}")

    def _tts_with_melo(self, text: str, output_raw_wav: Path) -> None:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            from melo.api import TTS  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "MeloTTS not available. Install voice deps with: python -m pip install -e '.[voice]'"
            ) from exc

        try:
            tts = TTS(language=self.config.melo_language, device="auto")
            speaker_id = 0
            spk2id = {}
            if hasattr(tts, "hps") and hasattr(tts.hps, "data") and hasattr(tts.hps.data, "spk2id"):
                spk2id = dict(getattr(tts.hps.data, "spk2id") or {})

            if self.config.melo_speaker in spk2id:
                speaker_id = int(spk2id[self.config.melo_speaker])
            elif spk2id:
                speaker_id = int(next(iter(spk2id.values())))

            tts.tts_to_file(text, speaker_id, str(output_raw_wav), speed=float(self.config.voice_speed))
        except Exception as exc:
            raise RuntimeError(f"MeloTTS synthesis failed: {exc}") from exc

        if not output_raw_wav.exists() or output_raw_wav.stat().st_size == 0:
            raise RuntimeError("MeloTTS did not produce audio output")

    def _tts_with_say(self, text: str, output_raw_wav: Path) -> None:
        temp_txt = self.paths["tmp"] / "say_input.txt"
        temp_aiff = self.paths["tmp"] / "say_output.aiff"
        self._write_text(temp_txt, text)

        result = self._run_command(
            ["say", "-f", str(temp_txt), "-o", str(temp_aiff)],
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"say command failed: {result.stderr.strip()}")

        convert = self._run_command(
            ["ffmpeg", "-y", "-i", str(temp_aiff), "-ac", "1", "-ar", "24000", str(output_raw_wav)],
            timeout=300,
            check=False,
        )
        if convert.returncode != 0:
            raise RuntimeError(f"Failed to convert system TTS output: {convert.stderr.strip()}")

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
        font_size = max(26, int(self.config.height * (0.05 if self.config.caption_style == "engagement" else 0.042)))
        margin_v = max(30, int(self.config.height * 0.075))
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
        vf_primary = self._clip_vf(clip.heading, index)
        vf_fallback = self._base_clip_vf()

        if source and source.exists():
            ext = source.suffix.lower()
            if ext in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
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
                command = self._placeholder_clip_command(output_clip, duration, index, vf_primary)
                fallback = self._placeholder_clip_command(output_clip, duration, index, vf_fallback)
        else:
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
                "strict_commercial_safe": self.config.strict_commercial_safe,
                "script_engine": self.config.script_engine,
                "tts_engine": self.config.tts_engine,
                "caption_engine": self.config.caption_engine,
                "caption_style": self.config.caption_style,
                "burn_subtitles": self.config.burn_subtitles,
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

    def _ollama_server_ready(self) -> bool:
        result = self._run_command(["ollama", "list"], timeout=10, check=False)
        return result.returncode == 0

    def _clean_narration_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(r"\b([A-Za-z]{3,})(\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"([?!.,])\1+", r"\1", cleaned)
        return cleaned

    def _base_clip_vf(self) -> str:
        w = str(self.config.width)
        h = str(self.config.height)
        fps = str(self.config.fps)
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"

    def _clip_vf(self, heading: str, index: int) -> str:
        base = self._base_clip_vf()
        heading_clean = self._escape_drawtext((heading or "Explainer Segment")[:80])
        scene_label = self._escape_drawtext(f"Scene {index + 1}")
        heading_size = max(22, int(self.config.height * 0.038))
        label_size = max(16, int(self.config.height * 0.024))
        return (
            f"{base},"
            "drawbox=x=0:y=0:w=iw:h=ih*0.17:color=black@0.35:t=fill,"
            f"drawtext=text='{scene_label}':fontcolor=white@0.85:fontsize={label_size}:x=40:y=40,"
            f"drawtext=text='{heading_clean}':fontcolor=white:fontsize={heading_size}:x=(w-text_w)/2:y=h*0.08:"
            "shadowcolor=black@0.9:shadowx=2:shadowy=2"
        )

    def _escape_drawtext(self, text: str) -> str:
        value = text.replace("\\", r"\\")
        value = value.replace(":", r"\:")
        value = value.replace("'", r"\'")
        value = value.replace(",", r"\,")
        value = value.replace("%", r"\%")
        return value

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
