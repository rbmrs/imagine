from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTENT_MODE_CHOICES = ("explainer", "news")
NEWS_VISUAL_STRATEGY_CHOICES = ("stock", "news-source-screenshot", "source-card")
SHOT_CONFIDENCE_CHOICES = ("high", "medium", "low")
DEFAULT_NEWS_FEED_URLS = (
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.npr.org/1004/rss.xml",
    "https://www.theguardian.com/world/rss",
)
SUBTITLE_PRESET_CHOICES = ("regular", "highlight-follow", "highlight-box-follow")
SUBTITLE_PRESET_ALIASES = {
    "boxed-highlight": "highlight-follow",
}
SUBTITLE_POSITION_CHOICES = ("bottom", "mid-safe")
SUBTITLE_BOX_COLOR_CHOICES = (
    "sunflower",
    "mint",
    "coral",
    "sky",
    "lime",
    "rose",
    "lavender",
    "tangerine",
)
SUBTITLE_TEXT_COLOR_CHOICES = SUBTITLE_BOX_COLOR_CHOICES + (
    "white",
    "black",
)
CAPTION_FONT_SCALE_MIN = 0.75
CAPTION_FONT_SCALE_MAX = 1.6


def normalize_subtitle_preset(value: Any, default: str = "regular") -> str:
    candidate = str(value or default).strip().lower()
    candidate = SUBTITLE_PRESET_ALIASES.get(candidate, candidate)
    if candidate not in SUBTITLE_PRESET_CHOICES:
        return default
    return candidate


def normalize_subtitle_position(value: Any, default: str = "bottom") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in SUBTITLE_POSITION_CHOICES:
        return default
    return candidate


def normalize_subtitle_accent_color(value: Any, default: str = "sunflower") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in SUBTITLE_TEXT_COLOR_CHOICES:
        return default
    return candidate


def normalize_subtitle_box_color(value: Any, default: str = "sunflower") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in SUBTITLE_BOX_COLOR_CHOICES:
        return default
    return candidate


def normalize_caption_font_scale(value: Any, default: float = 0.9) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        candidate = float(default)
    return max(CAPTION_FONT_SCALE_MIN, min(CAPTION_FONT_SCALE_MAX, candidate))


def normalize_content_mode(value: Any, default: str = "explainer") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in CONTENT_MODE_CHOICES:
        return default
    return candidate


def normalize_news_visual_strategy(value: Any, default: str = "stock") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in NEWS_VISUAL_STRATEGY_CHOICES:
        return default
    return candidate


def default_news_feed_urls() -> list[str]:
    return list(DEFAULT_NEWS_FEED_URLS)


def normalize_shot_confidence(value: Any, default: str = "medium") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in SHOT_CONFIDENCE_CHOICES:
        return default
    return candidate


@dataclass
class PipelineConfig:
    prompt: str
    project_dir: Path
    content_mode: str = "explainer"
    asset_keywords: list[str] = field(default_factory=list)
    news_feed_urls: list[str] = field(default_factory=list)
    news_max_age_hours: int = 72
    news_max_candidates: int = 20
    news_min_approved_sources: int = 2
    news_jurisdiction: str = "us"
    news_require_manual_source_approval: bool = False
    fast_mode: bool = False
    minutes: int = 5
    width: int = 1280
    height: int = 720
    fps: int = 30
    script_engine: str = "ollama"
    ollama_model: str = "qwen2.5:14b"
    require_ollama: bool = False
    script_tone: str = "conversational"
    target_audience: str = "curious general audience"
    hook_style: str = "surprising-fact"
    narrative_mode: str = "story-led"
    example_density: str = "balanced"
    tts_engine: str = "melo"
    caption_engine: str = "heuristic"
    caption_style: str = "engagement"
    burn_subtitles: bool = True
    subtitle_preset: str = "regular"
    subtitle_position: str = "bottom"
    subtitle_accent_color: str = "sunflower"
    subtitle_box_color: str = "sunflower"
    subtitle_bold: bool = True
    subtitle_outline: bool = True
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
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    coverr_api_key: str | None = None
    coverr_app_id: str | None = None
    vecteezy_api_key: str | None = None
    vecteezy_account_id: str | None = None
    require_external_assets: bool = False
    enable_pexels_provider: bool = True
    enable_pixabay_provider: bool = True
    enable_coverr_provider: bool = False
    enable_vecteezy_provider: bool = False
    allow_image_assets: bool = True
    allow_attribution_required_assets: bool = True
    asset_mode: str = "prefer-video"
    asset_shortlist_size: int = 6
    video_effects: str = "clean"
    image_motion_style: str = "slow"
    include_intro: bool = False
    include_outro: bool = True
    intro_seconds: float = 2.8
    outro_seconds: float = 3.0
    outro_text: str = "Thanks for watching"
    bookend_style: str = "minimal-clean"
    brand_logo_path: str | None = None
    brand_intro_image_path: str | None = None
    brand_outro_image_path: str | None = None
    brand_use_scene_fallback: bool = False
    channel_name: str = "IMAGINE"
    intro_tagline: str = ""
    outro_tagline: str = "Watch next"
    voice_profile: str = "calm-documentary"
    voice_speed: float = 1.0
    melo_language: str = "EN"
    melo_speaker: str = "EN-US"
    kokoro_lang_code: str = "en-us"
    kokoro_voice: str = "af_heart"
    piper_voice_id: str | None = None
    piper_speaker_id: int | None = None
    piper_model_url: str | None = None
    piper_config_url: str | None = None
    max_scenes: int = 40
    min_scene_seconds: float = 5.0
    verbose: bool = False

    def target_seconds(self) -> int:
        return int(self.minutes * 60)


@dataclass
class Scene:
    scene_id: str
    clip_name: str
    heading: str
    voiceover: str
    search_terms: list[str]
    seconds: float
    source_refs: list[str] = field(default_factory=list)
    visual_strategy: str = "stock"
    asset_path: str | None = None
    asset_provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "clip_name": self.clip_name,
            "heading": self.heading,
            "voiceover": self.voiceover,
            "search_terms": self.search_terms,
            "seconds": round(self.seconds, 3),
            "source_refs": list(self.source_refs),
            "visual_strategy": normalize_news_visual_strategy(self.visual_strategy, "stock"),
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
    media_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    restriction_flags: list[str] = field(default_factory=list)
    attribution_required: bool = False
    attribution_text: str | None = None
    scene_components: list[dict[str, Any]] = field(default_factory=list)

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
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "duration_seconds": round(self.duration_seconds, 3) if self.duration_seconds is not None else None,
            "restriction_flags": self.restriction_flags,
            "attribution_required": self.attribution_required,
            "attribution_text": self.attribution_text,
            "scene_components": [dict(item) for item in self.scene_components if isinstance(item, dict)],
        }


@dataclass
class AssetCandidate:
    source_platform: str
    media_type: str
    download_url: str
    source_url: str
    source_asset_id: str | None = None
    preview_url: str | None = None
    creator_name: str | None = None
    creator_profile_url: str | None = None
    license_name: str | None = None
    license_url: str | None = None
    description: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    download_extension: str | None = None
    query: str | None = None
    quality_score: float = 0.0
    ranking_score: float = 0.0
    restriction_flags: list[str] = field(default_factory=list)
    attribution_required: bool = False
    attribution_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_platform": self.source_platform,
            "media_type": self.media_type,
            "download_url": self.download_url,
            "source_url": self.source_url,
            "source_asset_id": self.source_asset_id,
            "preview_url": self.preview_url,
            "creator_name": self.creator_name,
            "creator_profile_url": self.creator_profile_url,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "description": self.description,
            "width": self.width,
            "height": self.height,
            "duration_seconds": round(self.duration_seconds, 3) if self.duration_seconds is not None else None,
            "download_extension": self.download_extension,
            "query": self.query,
            "quality_score": round(float(self.quality_score), 4),
            "ranking_score": round(float(self.ranking_score), 4),
            "restriction_flags": self.restriction_flags,
            "attribution_required": self.attribution_required,
            "attribution_text": self.attribution_text,
        }


@dataclass
class NewsSourceCandidate:
    source_id: str
    feed_url: str
    article_url: str
    canonical_url: str
    domain: str
    title: str
    publisher: str
    summary: str
    dek: str | None = None
    byline: str | None = None
    published_at: str | None = None
    extracted_text: str = ""
    screenshot_path: str | None = None
    source_card_path: str | None = None
    screenshot_available: bool = False
    screenshot_reason: str | None = None
    content_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "feed_url": self.feed_url,
            "article_url": self.article_url,
            "canonical_url": self.canonical_url,
            "domain": self.domain,
            "title": self.title,
            "publisher": self.publisher,
            "summary": self.summary,
            "dek": self.dek,
            "byline": self.byline,
            "published_at": self.published_at,
            "extracted_text": self.extracted_text,
            "screenshot_path": self.screenshot_path,
            "source_card_path": self.source_card_path,
            "screenshot_available": bool(self.screenshot_available),
            "screenshot_reason": self.screenshot_reason,
            "content_sha256": self.content_sha256,
        }


@dataclass
class ApprovedEditorialSource:
    source_id: str
    article_url: str
    canonical_url: str
    domain: str
    title: str
    publisher: str
    summary: str
    dek: str | None = None
    byline: str | None = None
    published_at: str | None = None
    decision: str = "approve-facts"
    screenshot_path: str | None = None
    source_card_path: str | None = None
    screenshot_available: bool = False
    approved_at: str | None = None
    rationale: str | None = None

    def visual_path(self) -> str | None:
        if self.decision == "approve-screenshot" and self.screenshot_path:
            return self.screenshot_path
        return self.source_card_path

    def visual_strategy(self) -> str:
        if self.decision == "approve-screenshot" and self.screenshot_path:
            return "news-source-screenshot"
        return "source-card"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "article_url": self.article_url,
            "canonical_url": self.canonical_url,
            "domain": self.domain,
            "title": self.title,
            "publisher": self.publisher,
            "summary": self.summary,
            "dek": self.dek,
            "byline": self.byline,
            "published_at": self.published_at,
            "decision": self.decision,
            "screenshot_path": self.screenshot_path,
            "source_card_path": self.source_card_path,
            "screenshot_available": bool(self.screenshot_available),
            "approved_at": self.approved_at,
            "rationale": self.rationale,
            "visual_path": self.visual_path(),
            "visual_strategy": self.visual_strategy(),
        }


@dataclass
class NewsBrief:
    title: str
    summary: str
    sources: list[ApprovedEditorialSource] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sources": [source.to_dict() for source in self.sources],
            "facts": [dict(item) for item in self.facts if isinstance(item, dict)],
        }


@dataclass
class PlannedShot:
    shot_id: str
    scene_id: str
    clip_name: str
    heading: str
    shot_index: int
    total_shots: int
    narration_text: str
    seconds: float
    narration_start: float
    narration_end: float
    shot_objective: str
    key_info: str
    required_entities: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    fallback_strategy: str = "internal-card"
    visual_type: str = "stock-video"
    match_confidence: str = "medium"
    fallback_level: str = "exact"
    asset_path: str | None = None
    asset_provider: str | None = None
    source_refs: list[str] = field(default_factory=list)
    visual_strategy: str = "stock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "clip_name": self.clip_name,
            "heading": self.heading,
            "shot_index": int(self.shot_index),
            "total_shots": int(self.total_shots),
            "narration_text": self.narration_text,
            "seconds": round(self.seconds, 3),
            "narration_start": round(self.narration_start, 3),
            "narration_end": round(self.narration_end, 3),
            "shot_objective": self.shot_objective,
            "key_info": self.key_info,
            "required_entities": list(self.required_entities),
            "search_queries": list(self.search_queries),
            "fallback_strategy": self.fallback_strategy,
            "visual_type": self.visual_type,
            "match_confidence": normalize_shot_confidence(self.match_confidence, "medium"),
            "fallback_level": self.fallback_level,
            "asset_path": self.asset_path,
            "asset_provider": self.asset_provider,
            "source_refs": list(self.source_refs),
            "visual_strategy": normalize_news_visual_strategy(self.visual_strategy, "stock"),
        }


@dataclass
class ShotPlan:
    title: str
    summary: str
    shots: list[PlannedShot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "shots": [shot.to_dict() for shot in self.shots],
        }


@dataclass
class ShotReviewState:
    shots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "shots": {
                str(shot_id): dict(value)
                for shot_id, value in sorted(self.shots.items())
                if str(shot_id).strip() and isinstance(value, dict)
            }
        }


@dataclass
class TimelineClip:
    scene_id: str
    clip_name: str
    start: float
    end: float
    seconds: float
    source_path: str | None
    heading: str
    narration_start: float | None = None
    narration_end: float | None = None
    visual_strategy: str = "stock"
    editorial_source_id: str | None = None
    shot_id: str | None = None
    parent_scene_id: str | None = None
    match_confidence: str | None = None
    fallback_level: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "scene_id": self.scene_id,
            "clip_name": self.clip_name,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "seconds": round(self.seconds, 3),
            "source_path": self.source_path,
            "heading": self.heading,
            "visual_strategy": normalize_news_visual_strategy(self.visual_strategy, "stock"),
            "editorial_source_id": self.editorial_source_id,
        }
        if self.shot_id is not None:
            payload["shot_id"] = self.shot_id
        if self.parent_scene_id is not None:
            payload["parent_scene_id"] = self.parent_scene_id
        if self.match_confidence is not None:
            payload["match_confidence"] = normalize_shot_confidence(self.match_confidence, "medium")
        if self.fallback_level is not None:
            payload["fallback_level"] = self.fallback_level
        if self.narration_start is not None:
            payload["narration_start"] = round(self.narration_start, 3)
        if self.narration_end is not None:
            payload["narration_end"] = round(self.narration_end, 3)
        return payload
