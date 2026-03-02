from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PipelineConfig:
    prompt: str
    project_dir: Path
    minutes: int = 5
    width: int = 1280
    height: int = 720
    fps: int = 30
    script_engine: str = "ollama"
    ollama_model: str = "qwen2.5:14b"
    require_ollama: bool = False
    tts_engine: str = "melo"
    caption_engine: str = "heuristic"
    caption_style: str = "engagement"
    burn_subtitles: bool = True
    caption_words_min: int = 2
    caption_words_max: int = 5
    caption_max_chars: int = 32
    caption_min_seconds: float = 0.7
    caption_max_seconds: float = 2.4
    caption_font_scale: float = 0.9
    caption_bottom_ratio: float = 0.055
    duration_tolerance_ratio: float = 0.25
    target_speech_wpm: int = 145
    max_duration_adjust_passes: int = 2
    strict_commercial_safe: bool = True
    allow_system_tts: bool = False
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    video_effects: str = "clean"
    voice_profile: str = "calm-documentary"
    voice_speed: float = 1.0
    melo_language: str = "EN"
    melo_speaker: str = "EN-US"
    max_scenes: int = 40
    min_scene_seconds: float = 5.0
    verbose: bool = False

    def target_seconds(self) -> int:
        return int(self.minutes * 60)


@dataclass
class Scene:
    scene_id: str
    heading: str
    voiceover: str
    search_terms: list[str]
    seconds: float
    asset_path: str | None = None
    asset_provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "heading": self.heading,
            "voiceover": self.voiceover,
            "search_terms": self.search_terms,
            "seconds": round(self.seconds, 3),
            "asset_path": self.asset_path,
            "asset_provider": self.asset_provider,
        }


@dataclass
class ScriptPlan:
    title: str
    summary: str
    scenes: list[Scene] = field(default_factory=list)

    def narration_text(self) -> str:
        return "\n\n".join(scene.voiceover.strip() for scene in self.scenes if scene.voiceover.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "scenes": [scene.to_dict() for scene in self.scenes],
        }


@dataclass
class AssetRight:
    scene_id: str
    source_platform: str
    source_asset_id: str | None
    source_url: str
    creator_name: str | None
    creator_profile_url: str | None
    license_name: str | None
    license_url: str | None
    downloaded_at: str
    local_path: str
    sha256: str
    restriction_flags: list[str] = field(default_factory=list)
    attribution_required: bool = False
    attribution_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "source_platform": self.source_platform,
            "source_asset_id": self.source_asset_id,
            "source_url": self.source_url,
            "creator_name": self.creator_name,
            "creator_profile_url": self.creator_profile_url,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "downloaded_at": self.downloaded_at,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "restriction_flags": self.restriction_flags,
            "attribution_required": self.attribution_required,
            "attribution_text": self.attribution_text,
        }


@dataclass
class TimelineClip:
    scene_id: str
    start: float
    end: float
    seconds: float
    source_path: str | None
    heading: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "seconds": round(self.seconds, 3),
            "source_path": self.source_path,
            "heading": self.heading,
        }
