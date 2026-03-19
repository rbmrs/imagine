from __future__ import annotations

import copy
import curses
import curses.textpad as textpad
import io
import datetime as dt
import base64
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import warnings
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Literal, TextIO, TypeVar, cast, overload

from .cli import _apply_default_brand_bookends, _parse_resolution
from .models import (
    CONTENT_MODE_CHOICES,
    SUBTITLE_BOX_COLOR_CHOICES,
    SUBTITLE_POSITION_CHOICES,
    SUBTITLE_PRESET_CHOICES,
    SUBTITLE_TEXT_COLOR_CHOICES,
    PipelineConfig,
    default_news_feed_urls,
    normalize_caption_font_scale,
    normalize_content_mode,
    normalize_subtitle_accent_color,
    normalize_subtitle_box_color,
    normalize_news_visual_strategy,
    normalize_subtitle_position,
    normalize_subtitle_preset,
)
from .pipeline import (
    PIPER_VOICE_PRESETS,
    VideoPipeline,
    default_kokoro_voice,
    describe_tts_selection_policy,
    kokoro_voice_choices_for_lang,
    normalize_kokoro_lang_code,
)
from .thumbnail_studio import (
    THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL,
    THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR,
    THUMBNAIL_STUDIO_DEFAULT_PALETTE,
    THUMBNAIL_STUDIO_DEFAULT_PRESET,
    THUMBNAIL_STUDIO_PALETTE_CHOICES,
    THUMBNAIL_STUDIO_PRESET_CHOICES,
    ThumbnailStudioSession,
    comfyui_endpoint_reachable,
    create_thumbnail_studio_session,
    export_thumbnail_studio_variant,
    generate_thumbnail_studio_variants,
    list_thumbnail_studio_sessions,
    load_thumbnail_studio_session,
    palette_label,
    preset_label,
    rerender_thumbnail_studio_session,
    save_thumbnail_studio_session,
    selected_thumbnail_variant,
    thumbnail_studio_session_dir,
    thumbnail_studio_session_preview_path,
)
from .youtube import (
    YOUTUBE_THUMBNAIL_ANCHOR_CHOICES,
    YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR,
    YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR,
    YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE,
    YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR,
    YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN,
    YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE,
    YOUTUBE_THUMBNAIL_FONT_COLOR_CHOICES,
    YOUTUBE_THUMBNAIL_FONT_SIZE_CHOICES,
    YOUTUBE_THUMBNAIL_MAX_OFFSET,
    YOUTUBE_THUMBNAIL_OUTLINE_COLOR_CHOICES,
    YOUTUBE_THUMBNAIL_TEXT_PLATE_CHOICES,
    YOUTUBE_THUMBNAIL_TEXT_ALIGN_CHOICES,
    YOUTUBE_VISIBILITY_CHOICES,
    YouTubeAuthState,
    YouTubePublishDraft,
    auth_review_text,
    disconnect_youtube_auth,
    ensure_youtube_token,
    draft_review_text,
    ensure_youtube_publish_draft,
    ensure_project_youtube_thumbnail,
    export_youtube_thumbnail_work_image,
    publish_youtube_draft,
    render_youtube_thumbnail_for_draft,
    save_youtube_publish_draft,
    youtube_thumbnail_diagnostics,
    youtube_thumbnail_text_suggestions,
    youtube_draft_path,
    detect_youtube_auth_state,
)
from . import trends


@dataclass
class TuiConfig:
    prompt: str
    content_mode: str
    asset_keywords: list[str]
    news_feed_urls: list[str]
    project_dir: Path
    minutes: int
    resolution: str
    burn_subtitles: bool
    subtitle_preset: str
    subtitle_position: str
    subtitle_accent_color: str
    subtitle_box_color: str
    fast_mode: bool
    script_tone: str
    target_audience: str
    hook_style: str
    narrative_mode: str
    example_density: str
    asset_mode: str
    image_motion_style: str
    tts_engine: str
    piper_voice_id: str
    piper_speaker_id: int | None
    voice_profile: str
    voice_speed: float
    melo_language: str
    melo_speaker: str
    kokoro_lang_code: str
    kokoro_voice: str
    include_intro: bool = False
    external_editor_command: str = ""
    caption_font_scale: float = 0.9
    subtitle_bold: bool = True
    subtitle_outline: bool = True
    enable_pexels_provider: bool = True
    enable_pixabay_provider: bool = True
    enable_coverr_provider: bool = False
    enable_vecteezy_provider: bool = False
    allow_image_assets: bool = True
    allow_attribution_required_assets: bool = True
    active_channel: str = "general"
    script_language: str = "en"
    thumbnail_comfyui_url: str = THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL
    thumbnail_comfyui_install_dir: str = ""
    thumbnail_default_export_dir: str = str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR)
    thumbnail_use_ollama_hooks: bool = True


T = TypeVar("T")


class SpinnerCancelled(RuntimeError):
    pass


@dataclass
class ConfigEditResult:
    escaped: bool = False
    had_warning: bool = False


class LocalVideoMvpTui:
    SPINNER_FRAMES = ["-", "\\", "|", "/"]
    IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
    SHOT_REVIEW_PREVIEW_IMAGE_ID = 91021
    STOCK_ENV_KEYS = (
        "PEXELS_API_KEY",
        "PIXABAY_API_KEY",
        "COVERR_APP_ID",
        "COVERR_API_KEY",
        "VECTEEZY_ACCOUNT_ID",
        "VECTEEZY_API_KEY",
    )
    MELO_LANGUAGE_CHOICES = ("EN",)
    KOKORO_TUI_LANG_CHOICES = ("en-us", "en-gb", "pt-br")
    KOKORO_TUI_LANG_LABELS = {
        "en-us": "EN-US",
        "en-gb": "EN-GB",
        "pt-br": "PT-BR",
    }
    CONTENT_MODE_CHOICES = CONTENT_MODE_CHOICES
    CONTENT_MODE_DETAILS = {
        "explainer": "Standard Imagine flow that builds from a prompt and licensed/public-domain support visuals.",
        "news": "Editorial workflow with RSS intake, source review, approved screenshots/source cards, and a U.S.-only fair-use gate. Curated editable feeds are prefilled by default.",
    }
    RESOLUTION_CHOICES = ("720p", "1080p", "1440p")
    RESOLUTION_PRESETS = {
        "720p": "1280x720",
        "1080p": "1920x1080",
        "1440p": "2560x1440",
    }
    SCRIPT_TONE_CHOICES = ("conversational", "documentary", "curiosity-driven", "analytical")
    SCRIPT_PROFILE_DETAILS = {
        "conversational": (
            "Relaxed explainer profile. Uses a surprising-fact hook, story-led structure, and balanced examples "
            "for a curious general audience."
        ),
        "documentary": (
            "Measured profile. Uses a question hook, explainer structure, and lighter examples for a curious "
            "general audience."
        ),
        "curiosity-driven": (
            "More YouTube-forward profile. Uses a surprising-fact hook, story-led structure, and heavier examples "
            "for a curious general audience."
        ),
        "analytical": (
            "Reasoning-first profile. Uses a problem-first hook, argument-led structure, and lighter examples for "
            "a curious general audience."
        ),
    }
    HOOK_STYLE_CHOICES = ("surprising-fact", "question", "problem-first", "story-first")
    HOOK_STYLE_DETAILS = {
        "surprising-fact": "Opens with an unexpected idea to pull the viewer in fast.",
        "question": "Starts with a direct question so the viewer wants the answer.",
        "problem-first": "Leads with tension or a problem before explaining the topic.",
        "story-first": "Begins with a small scenario or imagined moment instead of a blunt explanation.",
    }
    NARRATIVE_MODE_CHOICES = ("story-led", "explainer", "argument-led")
    NARRATIVE_MODE_DETAILS = {
        "story-led": "Flows more like a narrative, with smoother transitions and momentum.",
        "explainer": "Straight teaching mode. Best when clarity matters more than drama.",
        "argument-led": "Builds around a central claim and supporting points.",
    }
    EXAMPLE_DENSITY_CHOICES = ("light", "balanced", "heavy")
    EXAMPLE_DENSITY_DETAILS = {
        "light": "Uses fewer examples and stays more direct.",
        "balanced": "Mixes explanation with a moderate number of examples.",
        "heavy": "Uses more mini-scenarios and concrete examples to make points feel vivid.",
    }
    ASSET_MODE_CHOICES = ("prefer-video", "balanced", "prefer-images", "images-only")
    ASSET_MODE_DETAILS = {
        "prefer-video": "Default footage-first mode. Still images are used only when needed or explicitly chosen.",
        "balanced": "Lets high-quality images compete more directly with video without forcing an image-only workflow.",
        "prefer-images": "Biases the resolver toward stills so image-led scenes become much more common.",
        "images-only": "Disables video selection for scenes and builds the run entirely from still-image assets.",
    }
    IMAGE_MOTION_STYLE_CHOICES = ("static", "slow", "balanced", "fast")
    IMAGE_MOTION_STYLE_ALIASES = {
        "subtle": "slow",
        "documentary": "balanced",
        "dynamic": "fast",
    }
    IMAGE_MOTION_STYLE_DETAILS = {
        "static": "No motion on still images. Best only for debugging or intentionally static visuals.",
        "slow": "Slow camera movement with light pan and crop changes. Best when you want a conservative image-led pace.",
        "balanced": "Noticeable pan-and-crop motion with steadier camera travel. Good default for image-only explainers.",
        "fast": "More aggressive camera travel and cropping for energetic image-led scenes.",
    }
    SUBTITLE_PRESET_CHOICES = SUBTITLE_PRESET_CHOICES
    SUBTITLE_PRESET_DETAILS = {
        "regular": "Current clean burned-in look. Bottom-centered, conservative, and close to broadcast readability.",
        "highlight-follow": "Social-style captions with a colored active word that follows the current spoken word.",
        "highlight-box-follow": "Social-style captions where the active spoken word switches into an accent word box while the rest stays plain.",
    }
    SUBTITLE_POSITION_CHOICES = SUBTITLE_POSITION_CHOICES
    SUBTITLE_POSITION_DETAILS = {
        "bottom": "Default lower-third placement near the bottom center of the frame.",
        "mid-safe": "Raised lower-middle placement in the second vertical band from the bottom.",
    }
    SUBTITLE_TEXT_COLOR_CHOICES = SUBTITLE_TEXT_COLOR_CHOICES
    SUBTITLE_BOX_COLOR_CHOICES = SUBTITLE_BOX_COLOR_CHOICES
    SUBTITLE_ACCENT_COLOR_DETAILS = {
        "sunflower": "Warm yellow accent. Best default for energetic social-style captions.",
        "mint": "Fresh green accent with a softer contrast profile.",
        "coral": "Warm red-orange accent that feels bold without leaning neon.",
        "sky": "Clean blue accent for a cooler highlight treatment.",
        "lime": "Bright yellow-green accent for punchier active-word emphasis.",
        "rose": "Hot pink accent that feels playful without cycling colors.",
        "lavender": "Soft purple accent for a calmer, more stylized highlight.",
        "tangerine": "Orange accent that stays warm but reads brighter than coral.",
        "white": "Pure white highlight text for the cleanest high-contrast treatment.",
        "black": "Black highlight text for bright box backgrounds and stronger contrast control.",
    }
    SUBTITLE_FONT_SIZE_PRESETS = (
        ("Default", 0.90),
        ("Small", 0.80),
        ("Medium", 1.00),
        ("Large", 1.25),
        ("X-Large", 1.50),
    )
    SUBTITLE_FONT_SIZE_DETAILS = {
        "Default": "Current baseline subtitle size.",
        "Small": "Slightly smaller than the default subtitle scale.",
        "Medium": "A more prominent subtitle size than the current default.",
        "Large": "Noticeably larger captions for stronger on-screen presence.",
        "X-Large": "Maximum preset size for the most prominent caption treatment.",
    }
    YOUTUBE_THUMBNAIL_COLOR_DETAILS = {
        "white": "High-contrast default for bright, bold text.",
        "sunflower": "Warm yellow for a classic YouTube-style pop.",
        "mint": "Soft green for a fresher, calmer look.",
        "coral": "Warm red-orange for a stronger urgent feel.",
        "sky": "Cool blue for a cleaner technical look.",
        "tangerine": "Bright orange for a warmer headline treatment.",
        "black": "Dark text only when the frame is bright enough.",
        "charcoal": "Near-black outline with softer contrast than pure black.",
        "midnight": "Dark blue outline for cooler scenes.",
        "navy": "Blue-black outline that stays softer than black.",
        "crimson": "Dark red outline for a stylized warmer edge.",
    }
    YOUTUBE_THUMBNAIL_FONT_SIZE_LABELS = {
        "small": "Small",
        "medium": "Medium",
        "large": "Large",
        "extra-large": "Extra Large",
    }
    YOUTUBE_THUMBNAIL_ANCHOR_LABELS = {
        "top-left": "Top Left",
        "top-center": "Top Center",
        "top-right": "Top Right",
        "center-left": "Center Left",
        "center": "Center",
        "center-right": "Center Right",
        "bottom-left": "Bottom Left",
        "bottom-center": "Bottom Center",
        "bottom-right": "Bottom Right",
    }
    YOUTUBE_THUMBNAIL_ALIGN_LABELS = {
        "left": "Left",
        "center": "Center",
        "right": "Right",
    }
    YOUTUBE_THUMBNAIL_TEXT_PLATE_LABELS = {
        "none": "None",
        "soft": "Soft Plate",
        "strong": "Strong Plate",
    }
    YOUTUBE_THUMBNAIL_TEXT_PLATE_DETAILS = {
        "none": "No extra plate behind the text. Cleanest look, but less forgiving on busy frames.",
        "soft": "Adds a subtle dark plate behind the headline for safer contrast.",
        "strong": "Adds a stronger dark plate for high-contrast banner-style thumbnails.",
    }
    YOUTUBE_THUMBNAIL_STYLE_PRESETS = (
        {
            "key": "classic-hook",
            "label": "Classic Hook",
            "detail": "Large lower-left hook with a soft contrast plate. Best default for most explainers.",
            "font_color": "white",
            "outline_color": "charcoal",
            "font_size_mode": "extra-large",
            "anchor": "bottom-left",
            "text_align": "left",
            "text_plate": "soft",
        },
        {
            "key": "top-banner",
            "label": "Top Banner",
            "detail": "Big centered headline across the top with a stronger plate.",
            "font_color": "white",
            "outline_color": "charcoal",
            "font_size_mode": "extra-large",
            "anchor": "top-center",
            "text_align": "center",
            "text_plate": "strong",
        },
        {
            "key": "right-hook",
            "label": "Right Hook",
            "detail": "Right-aligned hook that leaves more room for a subject on the left.",
            "font_color": "sunflower",
            "outline_color": "charcoal",
            "font_size_mode": "large",
            "anchor": "bottom-right",
            "text_align": "right",
            "text_plate": "soft",
        },
        {
            "key": "object-focus",
            "label": "Object Focus",
            "detail": "Top-centered hook with softer technical styling for single-object thumbnails.",
            "font_color": "white",
            "outline_color": "midnight",
            "font_size_mode": "large",
            "anchor": "top-center",
            "text_align": "center",
            "text_plate": "soft",
        },
    )
    VOICE_PROFILE_CHOICES = ("calm-documentary", "balanced", "energetic-explainer")
    CHANNEL_PROFILES: dict[str, dict] = {
        "general": {
            "label": "General (English)",
            "description": "General-purpose English content",
            "tts_engine": "kokoro",
            "kokoro_lang_code": "en-us",
            "kokoro_voice": "af_heart",
            "script_language": "en",
            "script_tone": "conversational",
            "target_audience": "curious general audience",
            "hook_style": "surprising-fact",
            "narrative_mode": "story-led",
            "example_density": "balanced",
            "voice_profile": "balanced",
            "channel_name": "IMAGINE",
            "intro_tagline": "",
            "outro_text": "Thanks for watching",
            "outro_tagline": "Remember to like, share and subscribe",
            "outro_spoken_text": "Thanks for watching. Remember to like, share and subscribe.",
            "theme_context": None,
        },
        "refugio_da_fe": {
            "label": "Refúgio da Fé",
            "description": "Fé, esperança e espiritualidade — PT-BR",
            "tts_engine": "kokoro",
            "kokoro_lang_code": "pt-br",
            "kokoro_voice": "pf_dora",
            "script_language": "pt-br",
            "script_tone": "conversational",
            "target_audience": "público cristão brasileiro interessado em fé, esperança e espiritualidade",
            "hook_style": "story-first",
            "narrative_mode": "story-led",
            "example_density": "balanced",
            "voice_profile": "calm-documentary",
            "channel_name": "Refúgio da Fé",
            "intro_tagline": "Refúgio da Fé",
            "outro_text": "Obrigado por assistir",
            "outro_tagline": "Lembre-se de curtir, compartilhar e se inscrever",
            "outro_spoken_text": "Obrigado por assistir. Lembre-se de curtir, compartilhar e se inscrever.",
            "theme_context": "fé cristã, esperança, oração, escrituras bíblicas, crescimento espiritual, vida cristã, cultura religiosa brasileira",
        },
    }
    ASSET_PROVIDER_POLICIES = (
        {
            "key": "pexels",
            "label": "Pexels",
            "env_keys": ("PEXELS_API_KEY",),
            "media": "video + image",
            "notes": (
                "API-backed stock provider. Current Imagine policy treats Pexels as usable without mandatory "
                "description credits, though provider/source credit remains good hygiene."
            ),
        },
        {
            "key": "pixabay",
            "label": "Pixabay",
            "env_keys": ("PIXABAY_API_KEY",),
            "media": "video + image",
            "notes": (
                "API-backed stock provider. Current Imagine policy treats Pixabay as usable without mandatory "
                "description credits in the normal local-cache workflow."
            ),
        },
        {
            "key": "coverr",
            "label": "Coverr (experimental)",
            "env_keys": ("COVERR_API_KEY",),
            "media": "video fallback",
            "notes": (
                "Experimental fallback provider with a tight hourly request cap on demo access. Coverr API usage "
                "also carries provider-branding requirements, and the public license language is conservative "
                "enough that Imagine treats Coverr results as attribution-required."
            ),
        },
        {
            "key": "vecteezy",
            "label": "Vecteezy (experimental)",
            "env_keys": ("VECTEEZY_ACCOUNT_ID", "VECTEEZY_API_KEY"),
            "media": "video + image fallback",
            "notes": (
                "Experimental fallback provider. Search is only used when the primary providers return a weak "
                "shortlist, and licensed downloads are deferred until a Vecteezy candidate is actually selected. "
                "Free-tier downloads are quota-limited monthly and require attribution."
            ),
        },
    )
    STAGE_LINE_RE = re.compile(r"\[local-video-mvp\]\s+Stage\s+(\d+(?:\.\d+)?)/(\d+):\s+(.+)")
    STAGE_COMPLETE_RE = re.compile(r"\[local-video-mvp\]\s+([A-Za-z0-9_]+)\s+completed in\s+([0-9]+(?:\.[0-9]+)?)s")
    ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    DEBUG_VOICE_TEST_PHRASE = (
        "Autonomous vehicles are reshaping mobility, safety, and city planning across the world."
    )
    DEBUG_VOICE_TEST_PHRASES: dict[str, str] = {
        "pt-br": "Veículos autônomos estão transformando a mobilidade, a segurança e o planejamento das cidades em todo o mundo.",
    }
    DEBUG_PIPER_VOICES = tuple(dict(item) for item in PIPER_VOICE_PRESETS)

    def __init__(self, config: TuiConfig) -> None:
        self.config = config
        self.config.content_mode = normalize_content_mode(self.config.content_mode, "explainer")
        self.config.asset_keywords = self._normalize_asset_keywords(self.config.asset_keywords)
        self.config.news_feed_urls = [
            str(item).strip()
            for item in self.config.news_feed_urls
            if str(item).strip()
        ]
        self._apply_default_news_feeds_if_needed()
        self.config.project_dir = self.config.project_dir.expanduser().resolve()
        self.config.project_dir.mkdir(parents=True, exist_ok=True)
        self.config.kokoro_lang_code = normalize_kokoro_lang_code(self.config.kokoro_lang_code)
        self.config.kokoro_voice = str(self.config.kokoro_voice).strip() or default_kokoro_voice(self.config.kokoro_lang_code)

        self._stdscr: Any = None
        self._repo_root = Path(__file__).resolve().parents[2]
        self._load_dotenv_into_environ()

        self._lock = threading.Lock()
        self._logs: Deque[str] = deque(maxlen=1200)
        self._last_ui_stream_line: str | None = None
        self._status = "Ready. Press R to generate now."
        self._running = False
        self._exit_requested = False
        self._modal_depth = 0

        self._run_started_at: float | None = None
        self._last_elapsed_seconds: float | None = None
        self._workflow_kind: str | None = None
        self._stage_label: str | None = None
        self._stage_index: float | None = None
        self._stage_total: int | None = None
        self._prior_total_seconds: float | None = None
        self._active_project_dir: Path | None = None
        self._pending_export_path: Path | None = None
        self._pending_unique_asset_prompt: dict[str, Any] | None = None
        self._pending_clip_review_prompt: dict[str, Any] | None = None
        self._pending_stage_transition_prompt: dict[str, Any] | None = None
        self._pending_source_review_prompt: dict[str, Any] | None = None
        self._pending_script_review_prompt: dict[str, Any] | None = None
        self._pending_shot_review_prompt: dict[str, Any] | None = None
        self._pending_scene_review_prompt: dict[str, Any] | None = None
        self._hitl_stage = "sources" if self._content_mode() == "news" else "draft"
        self._hitl_enabled = True

        self._stock_api_keys: dict[str, str] = {}
        self._stock_key_sources: dict[str, str] = {}
        self._stock_key_warnings: list[str] = []

        self._worker: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None
        self._mpv_supported_vos: set[str] | None = None
        self._mpv_vo_probe_attempted = False
        self._mpv_input_conf_path: Path | None = None
        self._shot_preview_image_cache: dict[tuple[str, str, int], Path] = {}
        self._shot_preview_color_cache: dict[tuple[str, int, int, int], list[bytes]] = {}

        self._started_ollama = False
        self._ollama_process: subprocess.Popen[str] | None = None
        self._ollama_log_handle: TextIO | None = None
        self._ollama_starting = False
        self._ollama_probe_checked_at = 0.0
        self._ollama_probe_online: bool | None = None
        self._started_comfyui = False
        self._comfyui_process: subprocess.Popen[str] | None = None
        self._comfyui_log_handle: TextIO | None = None
        self._comfyui_starting = False
        self._comfyui_probe_checked_at = 0.0
        self._comfyui_probe_online: bool | None = None
        self._comfyui_detected_install_dir: Path | None = None
        self._comfyui_detection_attempted = False

        self._used_trends: list[str] = []

        self._session_log_handle: TextIO | None = None
        self._session_log_path, self._latest_log_path = self._init_session_log_paths()

        self._color_enabled = False
        self._color_pairs: dict[str, int] = {}

        self._open_session_log()
        self._load_persisted_settings()
        self._enforce_news_hitl_policy()
        self._refresh_prior_total_seconds()
        self._refresh_stock_key_cache()
        self._append_log(f"Session log: {self._session_log_path}")

    def run(self) -> int:
        try:
            curses.wrapper(self._main)
        finally:
            self._shutdown()
        return 0

    def _main(self, stdscr: Any) -> None:
        self._stdscr = stdscr
        stdscr.nodelay(True)
        stdscr.timeout(120)
        stdscr.keypad(True)
        self._init_colors()
        self._start_ollama_on_launch()
        self._start_comfyui_on_launch()

        try:
            curses.curs_set(0)
        except curses.error:
            pass

        while True:
            self._draw()
            if self._exit_requested and not self._is_running():
                break

            if not self._is_running():
                self._maybe_prompt_unique_asset_shortfall()
                self._maybe_prompt_source_review()
                self._maybe_prompt_script_review()
                self._maybe_prompt_shot_review()
                self._maybe_prompt_stage_transition()

            key = stdscr.getch()
            if key == -1:
                continue
            self._handle_key(key)

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return

        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_BLUE, -1)
            curses.init_pair(6, curses.COLOR_WHITE, -1)
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_BLACK)
            self._color_pairs = {
                "title": 1,
                "ok": 2,
                "warn": 3,
                "error": 4,
                "accent": 5,
                "muted": 6,
                "backdrop": 7,
            }
            self._color_enabled = True
        except curses.error:
            self._color_enabled = False
            self._color_pairs = {}

    def _attr(self, name: str, *, bold: bool = False) -> int:
        attr = 0
        if self._color_enabled and name in self._color_pairs:
            attr |= curses.color_pair(self._color_pairs[name])
        if bold:
            attr |= curses.A_BOLD
        return attr

    def _handle_key(self, key: int) -> None:
        if key == curses.KEY_RESIZE:
            return

        if not (0 <= key <= 255):
            return

        char = chr(key)

        if char in {"q", "Q"}:
            if self._is_running():
                self._append_log("Stopping active command before exit...")
                self._cancel_active_process()
            self._exit_requested = True
            self._set_status("Exiting...")
            return

        if char in {"r", "R"}:
            self._start_run_workflow()
            return

        if char in {"p", "P"}:
            self._open_prompt_settings_menu()
            return

        if char in {"t", "T"}:
            self._open_thumbnail_studio_menu()
            return

        if char in {"s", "S"}:
            self._open_settings_menu()
            return

        if char in {"y", "Y"}:
            self._open_youtube_publish_menu()
            return

        if char in {"c", "C"}:
            self._clean_projects()
            return

    def _start_run_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        should_run_preflight = self._active_project_dir is None or self._hitl_stage == "done"
        if should_run_preflight and not self._run_preflight():
            return

        if self._enforce_news_hitl_policy(persist=True):
            self._set_status("News mode requires HITL. Source review will open before draft.")

        if not self._hitl_enabled:
            self._refresh_stock_key_cache()
            if not self._passes_asset_hard_guard_precheck():
                return
            if not self._passes_tts_policy_precheck():
                return
            workspace = self._prepare_run_workspace()
            self._hitl_stage = "full"
            with self._lock:
                self._pending_unique_asset_prompt = None
                self._pending_clip_review_prompt = None
                self._pending_stage_transition_prompt = None
                self._pending_source_review_prompt = None
                self._pending_script_review_prompt = None
                self._pending_shot_review_prompt = None
                self._pending_scene_review_prompt = None

            self._mark_command_start(workflow_kind="run")
            self._set_running(True)
            self._set_status(f"Starting full stage: {workspace.name}")
            self._worker = threading.Thread(target=self._run_workflow, daemon=True)
            self._worker.start()
            return

        if self._hitl_stage == "source-review" and self._active_project_dir is not None:
            self._queue_source_review_prompt(self._active_project_dir)
            self._set_status("Resuming source review.")
            return

        if self._hitl_stage == "script-review" and self._active_project_dir is not None:
            self._queue_script_review_prompt(self._active_project_dir)
            self._set_status("Resuming script review.")
            return

        if self._hitl_stage == "shot-review" and self._active_project_dir is not None:
            self._queue_shot_review_prompt(self._active_project_dir)
            self._set_status("Resuming shot review.")
            return

        if self._active_project_dir is None or self._hitl_stage == "done":
            workspace = self._prepare_run_workspace()
            self._hitl_stage = "sources" if self._content_mode() == "news" else "draft"
            with self._lock:
                self._pending_unique_asset_prompt = None
                self._pending_clip_review_prompt = None
                self._pending_stage_transition_prompt = None
                self._pending_source_review_prompt = None
                self._pending_script_review_prompt = None
                self._pending_shot_review_prompt = None
                self._pending_scene_review_prompt = None
        else:
            workspace = self._active_project_dir

        self._refresh_stock_key_cache()
        if self._hitl_stage != "sources" and not self._passes_asset_hard_guard_precheck():
            return
        if self._hitl_stage != "sources" and not self._passes_tts_policy_precheck():
            return

        if self._hitl_stage in {"draft", "full"} and not self._ensure_ollama_available_with_modal():
            return

        self._mark_command_start(workflow_kind="run")
        self._set_running(True)
        self._set_status(f"Starting {self._display_stage_name(self._hitl_stage)} stage: {workspace.name}")
        self._worker = threading.Thread(target=self._run_workflow, daemon=True)
        self._worker.start()

    def _start_inspect_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._mark_command_start(workflow_kind="inspect")
        self._set_running(True)
        self._set_status("Inspecting project...")
        self._worker = threading.Thread(target=self._inspect_workflow, daemon=True)
        self._worker.start()

    def _run_workflow(self) -> None:
        try:
            stage = self._hitl_stage
            self._append_log("Using preferred profile. Press R to advance workflow.")
            self._append_log(f"HITL stage: {stage}")
            self._append_log(self._asset_preflight_message())
            if self._stock_key_sources:
                self._append_log(f"Stock key sources: {self._stock_key_sources}")
            for warning in self._stock_key_warnings:
                self._append_log(f"WARN: {warning}")

            if stage in {"draft", "full"}:
                self._ensure_ollama_running()

            run_code = self._run_and_stream(self._build_run_command(workflow_stage=stage), label="run")

            if run_code != 0:
                queued_unique_prompt = self._queue_unique_asset_prompt_from_run_report()
                if queued_unique_prompt:
                    self._set_status("Run paused: broaden asset keywords to unlock more unique clips.")
                else:
                    self._set_status(f"{self._display_stage_name(stage).title()} stage failed with exit code {run_code}.")
                return

            if stage == "sources":
                if self._active_project_dir is not None:
                    self._hitl_stage = "source-review"
                    self._queue_source_review_prompt(self._active_project_dir)
                self._set_status("Sources complete. Source review is ready.")
                return

            if stage == "draft":
                if self._active_project_dir is not None:
                    self._hitl_stage = "script-review"
                    self._queue_script_review_prompt(self._active_project_dir)
                self._set_status("Draft complete. Script review is ready.")
                return

            if stage == "shot-plan":
                if self._active_project_dir is not None:
                    self._hitl_stage = "shot-review"
                    self._queue_shot_review_prompt(self._active_project_dir)
                self._set_status("Shot plan complete. Shot review is ready.")
                return

            if stage == "review":
                self._queue_stage_transition_prompt(
                    next_stage="preview",
                    title="Review Complete",
                    body="Review checkpoint done. Generate preview now?",
                )
                self._set_status("Review complete. Preview checkpoint pending.")
                return

            if stage == "preview":
                self._set_status("Preview complete. Inspecting outputs...")
                inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
                self._queue_stage_transition_prompt(
                    next_stage="finalize",
                    title="Preview Ready",
                    body="Preview is ready. Finalize when ready.",
                )
                if inspect_code == 0:
                    self._set_status("Preview complete. Finalize checkpoint pending.")
                else:
                    self._set_status(f"Preview complete, inspect failed with exit code {inspect_code}.")
                return

            if stage == "finalize":
                placeholder_count = self._count_placeholder_scenes()
                if placeholder_count > 0:
                    self._append_log(
                        f"ERROR: Hard guard violation: {placeholder_count} placeholder scenes detected after finalize."
                    )
                    self._set_status("Hard guard violation: placeholders detected. Finalize rejected.")
                    return

                self._set_status("Finalize complete. Inspecting outputs...")
                inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
                exported_mp4, exported_thumbnail = self._export_project_outputs_to_downloads()
                self._hitl_stage = "done"

                if inspect_code == 0 and exported_mp4 is not None:
                    if exported_thumbnail is not None:
                        self._set_status(f"Finalize complete. MP4 and thumbnail exported to Downloads.")
                    else:
                        self._set_status(f"Finalize complete. MP4 exported to {exported_mp4.name}.")
                elif inspect_code == 0:
                    self._set_status("Finalize complete. MP4 export skipped.")
                else:
                    self._set_status(f"Finalize succeeded, inspect failed with exit code {inspect_code}.")
                return

            if stage == "full":
                self._set_status("Full run complete. Inspecting outputs...")
                inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
                exported_mp4, exported_thumbnail = self._export_project_outputs_to_downloads()
                self._hitl_stage = "done"

                if inspect_code == 0 and exported_mp4 is not None:
                    if exported_thumbnail is not None:
                        self._set_status("Full run complete. MP4 and thumbnail exported to Downloads.")
                    else:
                        self._set_status(f"Full run complete. MP4 exported to {exported_mp4.name}.")
                elif inspect_code == 0:
                    self._set_status("Full run complete. MP4 export skipped.")
                else:
                    self._set_status(f"Full run succeeded, inspect failed with exit code {inspect_code}.")
                return

            self._set_status(f"Unknown workflow stage: {stage}")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: {exc}")
            self._set_status("Run failed before completion.")
        finally:
            self._mark_command_stop()
            self._set_running(False)

    def _inspect_workflow(self) -> None:
        try:
            code = self._run_and_stream(self._build_inspect_command(), label="inspect")
            if code == 0:
                self._set_status("Inspect completed.")
            else:
                self._set_status(f"Inspect failed with exit code {code}.")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: {exc}")
            self._set_status("Inspect failed.")
        finally:
            self._mark_command_stop()
            self._set_running(False)

    def _run_and_stream(self, command: list[str], label: str) -> int:
        self._append_log(f"$ {shlex.join(command)}")
        env = os.environ.copy()
        repo_src = str(self._repo_root / "src")
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_src}:{current_pythonpath}" if current_pythonpath else repo_src

        if label in {"run", "replace"}:
            for key, value in self._stock_api_keys.items():
                if key not in env and value:
                    env[key] = value

        process = subprocess.Popen(
            command,
            cwd=str(self._repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._set_active_process(process)

        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\n")
                    if line:
                        self._append_stream_log(line)
                        self._update_status_from_log_line(line)
            process.wait()
            self._append_log(f"[{label}] exited with code {process.returncode}")

            if label == "run" and int(process.returncode or 0) == 0:
                self._refresh_prior_total_seconds()

            return int(process.returncode or 0)
        finally:
            self._set_active_process(None)

    def _build_run_command(self, workflow_stage: str | None = None) -> list[str]:
        project_dir = self._active_project_dir
        if project_dir is None:
            project_dir = self._prepare_run_workspace()

        stage = str(workflow_stage or self._hitl_stage).strip().lower() or "full"

        command = [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "run",
            "--prompt",
            self.config.prompt,
            "--content-mode",
            self._content_mode(),
            "--workflow-stage",
            stage,
            "--project-dir",
            str(project_dir),
            "--minutes",
            str(self.config.minutes),
            "--resolution",
            self.config.resolution,
            "--tts-engine",
            self.config.tts_engine,
            "--voice-profile",
            self.config.voice_profile,
            "--voice-speed",
            f"{self.config.voice_speed:.2f}",
            "--script-tone",
            self.config.script_tone,
            "--target-audience",
            self.config.target_audience,
            "--hook-style",
            self.config.hook_style,
            "--narrative-mode",
            self.config.narrative_mode,
            "--example-density",
            self.config.example_density,
            "--caption-style",
            "engagement",
            "--burn-subtitles" if self.config.burn_subtitles else "--no-burn-subtitles",
            "--subtitle-preset",
            self._normalized_subtitle_preset(),
            "--subtitle-position",
            self._normalized_subtitle_position(),
            "--subtitle-accent-color",
            self._normalized_subtitle_accent_color(),
            "--subtitle-box-color",
            self._normalized_subtitle_box_color(),
            "--caption-font-scale",
            f"{self._normalized_caption_font_scale():.2f}",
            "--subtitle-bold" if self._normalized_subtitle_bold() else "--no-subtitle-bold",
            "--subtitle-outline" if self._normalized_subtitle_outline() else "--no-subtitle-outline",
            "--strict-commercial-safe",
            "--enable-pexels-provider" if self.config.enable_pexels_provider else "--no-enable-pexels-provider",
            "--enable-pixabay-provider" if self.config.enable_pixabay_provider else "--no-enable-pixabay-provider",
            "--enable-coverr-provider" if self.config.enable_coverr_provider else "--no-enable-coverr-provider",
            "--enable-vecteezy-provider" if self.config.enable_vecteezy_provider else "--no-enable-vecteezy-provider",
            "--allow-image-assets" if self.config.allow_image_assets else "--no-allow-image-assets",
            "--asset-mode",
            self._normalized_asset_mode(),
            "--image-motion-style",
            self._normalized_image_motion_style(),
            (
                "--allow-attribution-required-assets"
                if self.config.allow_attribution_required_assets
                else "--no-allow-attribution-required-assets"
            ),
            "--verbose",
        ]

        if self.config.fast_mode:
            command.append("--fast-mode")
        else:
            command.extend(
                [
                    "--video-effects",
                    "clean",
                    "--include-intro" if self.config.include_intro else "--no-include-intro",
                    "--include-outro",
                    "--outro-seconds",
                    "4.2",
                    "--outro-text",
                    "Thanks for watching",
                    "--outro-tagline",
                    "Remember to like the video and subscribe",
                    "--bookend-style",
                    "corner-fade",
                    "--caption-engine",
                    "faster-whisper",
                    "--duration-tolerance",
                    "0.25",
                    "--require-external-assets",
                ]
            )

        if self.config.tts_engine == "melo":
            command.extend(
                [
                    "--melo-language",
                    self.config.melo_language,
                    "--melo-speaker",
                    self.config.melo_speaker,
                ]
            )
        elif self.config.tts_engine == "piper":
            piper_meta = self._selected_piper_voice_meta()
            command.extend(["--piper-voice-id", str(piper_meta.get("id") or "")])
            speaker_id = piper_meta.get("speaker_id")
            if speaker_id is not None:
                command.extend(["--piper-speaker-id", str(int(speaker_id))])
            model_url = str(piper_meta.get("model_url") or "").strip()
            config_url = str(piper_meta.get("config_url") or "").strip()
            if model_url:
                command.extend(["--piper-model-url", model_url])
            if config_url:
                command.extend(["--piper-config-url", config_url])
        elif self.config.tts_engine == "kokoro":
            command.extend(
                [
                    "--kokoro-lang-code",
                    normalize_kokoro_lang_code(self.config.kokoro_lang_code),
                    "--kokoro-voice",
                    str(self.config.kokoro_voice).strip() or default_kokoro_voice(self.config.kokoro_lang_code),
                ]
            )

        if stage in {"full", "draft"}:
            command.extend(
                [
                    "--script-engine",
                    "ollama",
                    "--ollama-model",
                    "qwen2.5:14b",
                    "--require-ollama",
                ]
            )
        else:
            command.extend(["--script-engine", "template"])

        if self.config.asset_keywords:
            command.extend(["--asset-keywords", ", ".join(self.config.asset_keywords)])
        for feed_url in self.config.news_feed_urls:
            command.extend(["--news-feed-url", feed_url])

        return command

    def _build_inspect_command(self) -> list[str]:
        project_dir = self._active_project_dir or self._latest_project_workspace()
        if project_dir is None:
            project_dir = self.config.project_dir

        return [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "inspect",
            "--project-dir",
            str(project_dir),
        ]

    def _start_ollama_on_launch(self) -> None:
        if str(getattr(self.config, "script_engine", "ollama") or "ollama").strip().lower() != "ollama":
            return
        thread = threading.Thread(target=self._ensure_ollama_running, daemon=True)
        thread.start()

    def _start_comfyui_on_launch(self) -> None:
        thread = threading.Thread(target=self._ensure_comfyui_running, daemon=True)
        thread.start()

    def _ensure_ollama_running(self) -> None:
        if shutil.which("ollama") is None:
            self._append_log("WARN: `ollama` not found in PATH; run may fail with --require-ollama.")
            return

        with self._lock:
            if self._ollama_starting:
                return
            if self._ollama_process is not None and self._ollama_process.poll() is None:
                return
            self._ollama_starting = True

        if shutil.which("pgrep") is not None:
            try:
                check = subprocess.run(["pgrep", "-f", "ollama serve"], capture_output=True, text=True, check=False)
                if check.returncode == 0:
                    with self._lock:
                        self._ollama_probe_online = True
                        self._ollama_probe_checked_at = time.monotonic()
                    self._append_log("Ollama server already running.")
                    return
            finally:
                with self._lock:
                    self._ollama_starting = False
        else:
            with self._lock:
                self._ollama_starting = False

        with self._lock:
            self._ollama_starting = True

        try:
            log_path = Path("/tmp/local-video-mvp-ollama.log")
            log_handle = log_path.open("a", encoding="utf-8")
            process = subprocess.Popen(["ollama", "serve"], stdout=log_handle, stderr=subprocess.STDOUT, text=True)

            with self._lock:
                if self._ollama_log_handle is not None:
                    self._ollama_log_handle.close()
                self._ollama_log_handle = log_handle
                self._ollama_process = process
                self._started_ollama = True

            self._append_log(f"Started Ollama server (pid={process.pid}).")
            self._append_log(f"Ollama log: {log_path}")
            time.sleep(2.0)
        finally:
            with self._lock:
                self._ollama_starting = False

    def _ollama_available(self) -> bool:
        if shutil.which("ollama") is None:
            return False
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return False
        return result.returncode == 0

    def _ensure_ollama_available_with_modal(self) -> bool:
        if self._ollama_available():
            return True

        self._ensure_ollama_running()
        if self._ollama_available():
            return True

        self._show_paginated_text_modal(
            "Ollama Unavailable",
            (
                "Ollama is unavailable, so script generation cannot continue.\n\n"
                "Start Ollama with `ollama serve` and try again.\n"
                "If you intentionally want placeholder script text, run the CLI with "
                "`--script-engine template` instead."
            ),
        )
        self._append_log("WARN: Ollama unavailable. Run was blocked before start.")
        self._set_status("Ollama unavailable. Start `ollama serve` and retry.")
        return False

    def _thumbnail_comfyui_install_dir(self) -> Path | None:
        configured = str(getattr(self.config, "thumbnail_comfyui_install_dir", "") or "").strip()
        if configured:
            resolved = Path(configured).expanduser().resolve()
            if self._is_comfyui_install_dir(resolved):
                return resolved
        return self._detect_local_comfyui_install_dir()

    def _is_comfyui_install_dir(self, candidate: Path) -> bool:
        resolved = candidate.expanduser().resolve()
        if not resolved.is_dir():
            return False
        main_py = resolved / "main.py"
        if not main_py.exists():
            return False
        return (resolved / "custom_nodes").exists() or (resolved / "web").exists() or (resolved / "models").exists()

    def _detect_local_comfyui_install_dir(self) -> Path | None:
        if self._comfyui_detection_attempted:
            return self._comfyui_detected_install_dir

        candidates: list[Path] = []
        env_dir = os.environ.get("IMAGINE_THUMBNAIL_COMFYUI_DIR")
        if env_dir:
            candidates.append(Path(env_dir).expanduser())

        home = Path.home()
        candidates.extend(
            [
                self._managed_comfyui_install_dir(),
                home / "ComfyUI",
                home / "comfyui",
                home / "Applications" / "ComfyUI",
                home / "Applications" / "comfyui",
                home / "Dev" / "ComfyUI",
                home / "Developer" / "ComfyUI",
                home / "Documents" / "ComfyUI",
                home / "Downloads" / "ComfyUI",
            ]
        )

        search_roots = [
            home / "Applications",
            home / "Dev",
            home / "Developer",
            home / "Documents",
            home / "Downloads",
        ]
        for root in search_roots:
            if not root.exists() or not root.is_dir():
                continue
            try:
                for child in root.iterdir():
                    if "comfyui" in child.name.lower():
                        candidates.append(child)
            except Exception:
                continue

        checked: set[str] = set()
        for candidate in candidates:
            key = str(candidate.expanduser())
            if key in checked:
                continue
            checked.add(key)
            if self._is_comfyui_install_dir(candidate):
                resolved = candidate.expanduser().resolve()
                self._comfyui_detected_install_dir = resolved
                self._comfyui_detection_attempted = True
                return resolved

        self._comfyui_detected_install_dir = None
        self._comfyui_detection_attempted = True
        return None

    def _comfyui_launch_command(self) -> tuple[list[str], Path] | None:
        install_dir = self._thumbnail_comfyui_install_dir()
        if install_dir is None:
            return None
        url = self._thumbnail_studio_comfyui_url()
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8188
        python_candidates = [
            install_dir / ".venv" / "bin" / "python",
            install_dir / "venv" / "bin" / "python",
            install_dir / "env" / "bin" / "python",
            Path(sys.executable).expanduser().resolve(),
            Path("/usr/bin/python3"),
        ]
        python_exec: Path | None = None
        for candidate in python_candidates:
            if candidate.exists() and candidate.is_file():
                python_exec = candidate
                break
        if python_exec is None:
            return None
        command = [
            str(python_exec),
            "main.py",
            "--listen",
            host,
            "--port",
            str(port),
        ]
        return command, install_dir

    def _comfyui_available(self) -> bool:
        return self._probe_comfyui_online(max_age=0.0)

    def _probe_comfyui_online(self, *, max_age: float = 4.0) -> bool:
        now = time.monotonic()
        with self._lock:
            checked_at = self._comfyui_probe_checked_at
            cached = self._comfyui_probe_online
        if cached is not None and max_age > 0 and (now - checked_at) <= max_age:
            return bool(cached)
        reachable, _ = comfyui_endpoint_reachable(self._thumbnail_studio_comfyui_url())
        with self._lock:
            self._comfyui_probe_online = reachable
            self._comfyui_probe_checked_at = now
        return reachable

    def _ensure_comfyui_running(self) -> None:
        if self._comfyui_available():
            return

        with self._lock:
            if self._comfyui_starting:
                return
            if self._comfyui_process is not None and self._comfyui_process.poll() is None:
                return
            self._comfyui_starting = True

        try:
            launch = self._comfyui_launch_command()
            if launch is None:
                self._append_log("WARN: No local ComfyUI install was detected for auto-start.")
                return
            command, install_dir = launch
            log_path = self._comfyui_log_path()
            log_handle = log_path.open("a", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=str(install_dir),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            with self._lock:
                if self._comfyui_log_handle is not None:
                    self._comfyui_log_handle.close()
                self._comfyui_log_handle = log_handle
                self._comfyui_process = process
                self._started_comfyui = True
            self._append_log(f"Started ComfyUI server (pid={process.pid}).")
            self._append_log(f"ComfyUI install: {install_dir}")
            self._append_log(f"ComfyUI log: {log_path}")
            deadline = time.monotonic() + 25.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self._append_log(f"ERROR: ComfyUI exited early with code {process.returncode}.")
                    return
                if self._comfyui_available():
                    self._append_log("ComfyUI server is online.")
                    return
                time.sleep(1.0)
        finally:
            with self._lock:
                self._comfyui_starting = False

    def _ensure_comfyui_available_with_modal(self) -> bool:
        if self._comfyui_available():
            return True

        self._ensure_comfyui_running()
        if self._comfyui_available():
            return True

        install_dir = self._thumbnail_comfyui_install_dir()
        if install_dir is None:
            should_install = self._prompt_yes_no(
                "Install ComfyUI",
                (
                    "Thumbnail Studio could not find a local ComfyUI install.\n\n"
                    f"Imagine can install a managed copy under:\n{self._managed_comfyui_install_dir()}\n\n"
                    "This keeps setup inside the TUI. Install now?"
                ),
                default_yes=True,
            )
            if should_install:
                try:
                    self._run_with_spinner_modal(
                        title="Thumbnail ComfyUI",
                        message="Installing managed ComfyUI",
                        detail_text=str(self._managed_comfyui_install_dir()),
                        task=self._install_managed_comfyui,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Managed ComfyUI install failed: {exc}")
                    self._set_status("Managed ComfyUI install failed.")
                    self._show_paginated_text_modal(
                        "Managed ComfyUI Install Failed",
                        "\n".join(
                            [
                                "Imagine could not finish the managed ComfyUI install.",
                                "",
                                f"Detail: {exc}",
                                "",
                                f"Log file: {self._comfyui_log_path()}",
                            ]
                        ),
                    )
                    return False
                self._ensure_comfyui_running()
                if self._comfyui_available():
                    return True
                install_dir = self._thumbnail_comfyui_install_dir()

        install_text = str(install_dir) if install_dir is not None else "(not detected)"
        self._show_paginated_text_modal(
            "ComfyUI Unavailable",
            (
                "Thumbnail generation needs a local ComfyUI API server.\n\n"
                f"Configured URL: {self._thumbnail_studio_comfyui_url()}\n"
                f"Detected install: {install_text}\n\n"
                "The TUI tried to auto-start ComfyUI but it is still unavailable.\n"
                "Open `Settings -> Thumbnail ComfyUI` if your ComfyUI lives in a custom folder or you want Imagine to install a managed copy.\n"
                "Check `/tmp/local-video-mvp-comfyui.log` if auto-start launched but failed."
            ),
        )
        self._append_log("WARN: ComfyUI unavailable for Thumbnail Studio.")
        self._set_status("ComfyUI unavailable. Thumbnail generation is blocked.")
        return False

    def _install_managed_comfyui(self) -> Path:
        install_dir = self._managed_comfyui_install_dir()
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        repo_url = "https://github.com/comfyanonymous/ComfyUI.git"

        if not self._is_comfyui_install_dir(install_dir):
            if install_dir.exists() and any(install_dir.iterdir()):
                raise RuntimeError(
                    f"Managed ComfyUI folder already exists but is not a valid install: {install_dir}"
                )
            clone = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(install_dir)],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            if int(clone.returncode or 0) != 0:
                raise RuntimeError(str(clone.stderr or clone.stdout or "git clone failed").strip())

        venv_dir = install_dir / ".venv"
        python_exec = venv_dir / "bin" / "python"
        if not python_exec.exists():
            create_venv = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                timeout=1200,
                check=False,
            )
            if int(create_venv.returncode or 0) != 0:
                raise RuntimeError(str(create_venv.stderr or create_venv.stdout or "venv creation failed").strip())

        pip_bootstrap = subprocess.run(
            [str(python_exec), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if int(pip_bootstrap.returncode or 0) != 0:
            raise RuntimeError(str(pip_bootstrap.stderr or pip_bootstrap.stdout or "pip bootstrap failed").strip())

        install_requirements = subprocess.run(
            [str(python_exec), "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(install_dir),
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
        )
        if int(install_requirements.returncode or 0) != 0:
            raise RuntimeError(
                str(install_requirements.stderr or install_requirements.stdout or "requirements install failed").strip()
            )

        self.config.thumbnail_comfyui_install_dir = str(install_dir)
        self._comfyui_detected_install_dir = install_dir
        self._comfyui_detection_attempted = True
        self._save_persisted_settings()
        self._append_log(f"Managed ComfyUI install ready at {install_dir}")
        return install_dir

    def _comfyui_status_lines(self) -> list[str]:
        install_dir = self._thumbnail_comfyui_install_dir()
        reachable, reason = comfyui_endpoint_reachable(self._thumbnail_studio_comfyui_url())
        checkpoint_count = self._comfyui_checkpoint_count(install_dir)
        with self._lock:
            process = self._comfyui_process
        process_text = "running" if process is not None and process.poll() is None else "not managed"
        return [
            f"Status: {'online' if reachable else 'offline'}",
            f"URL: {self._thumbnail_studio_comfyui_url()}",
            f"Install dir: {str(install_dir) if install_dir is not None else '(not detected)'}",
            f"Checkpoints: {checkpoint_count}",
            f"Managed process: {process_text}",
            f"Detail: {reason}",
            f"Log: {self._comfyui_log_path()}",
        ]

    def _open_thumbnail_comfyui_manager(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        current_option = "Review status"
        while True:
            status_text = self._thumbnail_comfyui_summary()
            install_summary = self._thumbnail_comfyui_install_summary()
            checkpoint_count = self._comfyui_checkpoint_count(self._thumbnail_comfyui_install_dir())
            options = [
                "Review status",
                "Auto setup managed ComfyUI",
                "Start ComfyUI now",
                "Auto-detect install again",
                "Change ComfyUI URL",
                "Change install dir",
                "Open ComfyUI log",
                "Back",
            ]
            option_details = {
                "Review status": f"Current status: {status_text} | install: {install_summary} | checkpoints: {checkpoint_count}",
                "Auto setup managed ComfyUI": (
                    f"Install ComfyUI under {self._managed_comfyui_install_dir()} and prepare a local Python environment."
                ),
                "Start ComfyUI now": "Try to auto-start the detected/configured ComfyUI install and wait for the API to come online.",
                "Auto-detect install again": "Rescan common folders for an existing ComfyUI install and remember it.",
                "Change ComfyUI URL": f"Current: {self._thumbnail_studio_comfyui_url()}",
                "Change install dir": (
                    f"Current: {str(self._thumbnail_comfyui_install_dir()) if self._thumbnail_comfyui_install_dir() is not None else '(auto-detect)'}"
                ),
                "Open ComfyUI log": f"Open {self._comfyui_log_path()} in your configured external editor.",
                "Back": "Return to Settings.",
            }
            choice = self._select_from_list(
                label="Thumbnail ComfyUI",
                options=options,
                current_value=current_option if current_option in options else options[0],
                option_details=option_details,
            )
            if choice is None or choice == "Back":
                self._set_status("Thumbnail ComfyUI settings closed.")
                return
            current_option = cast(str, choice)

            if current_option == "Review status":
                self._show_paginated_text_modal("Thumbnail ComfyUI Status", "\n".join(self._comfyui_status_lines()))
                continue

            if current_option == "Auto-detect install again":
                self._comfyui_detection_attempted = False
                self._comfyui_detected_install_dir = None
                detected = self._detect_local_comfyui_install_dir()
                if detected is not None:
                    self._set_status(f"Detected ComfyUI install: {detected.name}")
                else:
                    self._set_status("No local ComfyUI install was detected.")
                continue

            if current_option == "Change ComfyUI URL":
                before = self._config_snapshot()
                result = self._edit_thumbnail_comfyui_url_field()
                changed = self._commit_config_changes(before)
                self._report_config_edit(context="Thumbnail ComfyUI", changed=changed, had_warning=result.had_warning)
                continue

            if current_option == "Change install dir":
                before = self._config_snapshot()
                result = self._edit_thumbnail_comfyui_install_dir_field()
                changed = self._commit_config_changes(before)
                self._report_config_edit(context="Thumbnail ComfyUI", changed=changed, had_warning=result.had_warning)
                continue

            if current_option == "Open ComfyUI log":
                log_path = self._comfyui_log_path()
                if not log_path.exists():
                    self._set_status("ComfyUI log file does not exist yet.")
                    continue
                if self._open_path_in_external_editor(log_path):
                    self._set_status("Opened ComfyUI log.")
                continue

            if current_option == "Auto setup managed ComfyUI":
                confirm = self._prompt_yes_no(
                    "Install Managed ComfyUI",
                    (
                        f"Install ComfyUI under:\n{self._managed_comfyui_install_dir()}\n\n"
                        "This stays entirely local and may take a while on first setup. Continue?"
                    ),
                    default_yes=True,
                )
                if not confirm:
                    self._set_status("Managed ComfyUI install cancelled.")
                    continue
                try:
                    install_dir = self._run_with_spinner_modal(
                        title="Thumbnail ComfyUI",
                        message="Installing managed ComfyUI",
                        detail_text=str(self._managed_comfyui_install_dir()),
                        task=self._install_managed_comfyui,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Managed ComfyUI install failed: {exc}")
                    self._show_paginated_text_modal(
                        "Managed ComfyUI Install Failed",
                        "\n".join(
                            [
                                "Imagine could not finish the managed ComfyUI install.",
                                "",
                                f"Detail: {exc}",
                                "",
                                f"Log file: {self._comfyui_log_path()}",
                            ]
                        ),
                    )
                    self._set_status("Managed ComfyUI install failed.")
                    continue
                self._set_status(f"Managed ComfyUI installed: {Path(str(install_dir)).name}")
                continue

            if current_option == "Start ComfyUI now":
                if self._ensure_comfyui_available_with_modal():
                    self._set_status("ComfyUI is online.")
                continue

    def _draw(self) -> None:
        if self._stdscr is None:
            return

        stdscr = self._stdscr
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        if width < 50 or height < 20:
            self._draw_compact(height, width)
            if self._is_modal_active():
                self._draw_modal_backdrop(height, width)
            stdscr.refresh()
            return

        spinner = self._spinner_frame() if self._is_running() else "*"
        title = f" Imagine TUI {spinner} "
        self._safe_addstr(0, 0, title, width, attr=self._attr("title", bold=True))

        self._draw_hotkey_hint(row=1, width=width)
        self._safe_hline(2, width)

        self._draw_box(3, 0, 8, width, title=" Configuration ", attr=self._attr("accent"))
        self._safe_addstr(4, 2, f"Prompt : {self._trim_tail(self.config.prompt, width - 14)}", width)
        mode_text = self._content_mode()
        if mode_text == "news":
            mode_text = f"news ({len(self.config.news_feed_urls)} feeds)"
        self._safe_addstr(5, 2, f"Mode   : {mode_text}  Minutes: {self.config.minutes}", width)
        self._safe_addstr(6, 2, f"Fast   : {'On' if self.config.fast_mode else 'Off'}", width)
        self._safe_addstr(
            7,
            2,
            f"Voice  : {self._voice_display_value()}  "
            f"profile={self.config.voice_profile} speed={self.config.voice_speed:.2f}",
            width,
        )
        # Show concise MP4 path: just ~/Downloads/filename instead of full path
        mp4_path = self._mp4_output_preview_path()
        home = Path.home()
        try:
            mp4_display = f"~/{mp4_path.relative_to(home)}"
        except ValueError:
            mp4_display = mp4_path.name
        self._safe_addstr(
            8,
            2,
            f"MP4 out: {self._trim_middle(mp4_display, width - 13)}",
            width,
            attr=self._attr("muted"),
        )
        keywords_text = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(auto from script scenes)"
        self._safe_addstr(9, 2, f"Keywords: {self._trim_tail(keywords_text, width - 14)}", width, attr=self._attr("muted"))
        tts_text, tts_attr = self._tts_status_display()
        self._safe_addstr(10, 2, f"TTS    : {self._trim_tail(tts_text, width - 14)}", width, attr=tts_attr)

        self._draw_box(12, 0, 7, width, title=" Runtime ", attr=self._attr("accent"))
        state_text, state_attr = self._state_display()
        self._safe_addstr(13, 2, f"State  : {state_text}", width, attr=state_attr)
        self._safe_addstr(14, 2, f"Phase  : {self._trim_tail(self._progress_phase_text(), width - 14)}", width)
        assets_text, assets_attr = self._asset_status_display()
        self._safe_addstr(15, 2, f"Assets : {self._trim_tail(assets_text, width - 14)}", width, attr=assets_attr)
        ollama_text, ollama_attr = self._ollama_status_display()
        self._safe_addstr(16, 2, f"Ollama: {self._trim_tail(ollama_text, width - 14)}", width, attr=ollama_attr)
        self._safe_addstr(17, 2, f"Status : {self._trim_tail(self._get_status(), width - 14)}", width)

        logs_top = 19
        logs_height = max(3, height - logs_top - 1)
        self._draw_box(logs_top, 0, logs_height, width, title=" Logs ", attr=self._attr("accent"))

        logs = self._get_logs()
        content_top = logs_top + 1
        content_bottom = logs_top + logs_height - 2
        content_rows = max(1, content_bottom - content_top + 1)

        if self._is_modal_active():
            modal_hint = "Modal focus active. Live logs hidden."
            self._safe_addstr(content_top, 2, self._trim_tail(modal_hint, width - 4), width, attr=self._attr("muted"))
        else:
            visible_logs = self._visible_logs(logs, content_rows)
            for index, line in enumerate(visible_logs):
                row = content_top + index
                attr = self._log_line_attr(line)
                self._safe_addstr(row, 2, self._trim_tail(line, width - 4), width, attr=attr)

        footer = self._footer_text(total_logs=len(logs))
        self._safe_addstr(height - 1, 0, footer, width, attr=self._attr("muted"))
        if self._is_modal_active():
            self._draw_modal_backdrop(height, width)
        stdscr.refresh()

    def _draw_compact(self, height: int, width: int) -> None:
        self._safe_addstr(0, 0, "Imagine TUI", width, attr=self._attr("title", bold=True))
        self._draw_hotkey_hint(row=1, width=width)
        self._safe_addstr(3, 0, f"Prompt: {self._trim_tail(self.config.prompt, width - 8)}", width)
        self._safe_addstr(4, 0, f"Minutes: {self.config.minutes}  Fast: {'On' if self.config.fast_mode else 'Off'}", width)
        self._safe_addstr(
            5,
            0,
            self._trim_tail(
                f"Voice: {self._voice_display_value()} "
                f"{self.config.voice_profile} {self.config.voice_speed:.2f}",
                width,
            ),
            width,
        )
        self._safe_addstr(6, 0, self._trim_tail(f"MP4: {self._mp4_output_preview_path()}", width), width)
        keywords_text = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(auto scenes)"
        self._safe_addstr(7, 0, self._trim_tail(f"Keywords: {keywords_text}", width), width)
        tts_text, tts_attr = self._tts_status_display()
        self._safe_addstr(8, 0, self._trim_tail(f"TTS: {tts_text}", width), width, attr=tts_attr)
        state_text, state_attr = self._state_display()
        self._safe_addstr(10, 0, f"{state_text}", width, attr=state_attr)
        self._safe_addstr(11, 0, self._trim_tail(self._progress_phase_text(), width), width)
        assets_text, assets_attr = self._asset_status_display()
        self._safe_addstr(12, 0, self._trim_tail(assets_text, width), width, attr=assets_attr)
        ollama_text, ollama_attr = self._ollama_status_display()
        self._safe_addstr(13, 0, self._trim_tail(f"Ollama: {ollama_text}", width), width, attr=ollama_attr)
        self._safe_addstr(14, 0, self._trim_tail(self._get_status(), width), width)

        logs = self._get_logs()
        log_rows = max(1, height - 16)
        if self._is_modal_active():
            self._safe_addstr(16, 0, self._trim_tail("Modal focus active. Live logs hidden.", width), width, attr=self._attr("muted"))
        else:
            for idx, line in enumerate(logs[-log_rows:]):
                self._safe_addstr(16 + idx, 0, self._trim_tail(line, width), width, attr=self._log_line_attr(line))

    def _voice_display_value(self) -> str:
        engine = str(self.config.tts_engine or "melo").strip().lower()
        if engine == "piper":
            voice_meta = self._selected_piper_voice_meta()
            label = str(voice_meta.get("label") or voice_meta.get("id") or "piper").strip()
            return f"piper/{label}"
        if engine == "kokoro":
            lang_code = normalize_kokoro_lang_code(self.config.kokoro_lang_code)
            voice = str(self.config.kokoro_voice).strip() or default_kokoro_voice(lang_code)
            return f"kokoro/{lang_code}/{voice}"
        return f"{self.config.melo_language}/{self.config.melo_speaker}"

    def _tts_policy_for_current_selection(self) -> dict[str, Any]:
        return describe_tts_selection_policy(
            tts_engine=self.config.tts_engine,
            strict_commercial_safe=True,
            melo_language=self.config.melo_language,
            melo_speaker=self.config.melo_speaker,
            kokoro_lang_code=self.config.kokoro_lang_code,
            kokoro_voice=self.config.kokoro_voice,
            piper_voice_id=self.config.piper_voice_id,
            piper_speaker_id=self.config.piper_speaker_id,
        )

    def _tts_policy_for_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        engine = str(entry.get("engine") or self.config.tts_engine or "melo").strip().lower()
        if engine == "piper":
            voice_meta = cast(dict[str, Any], entry.get("voice_meta") or {})
            speaker_id = voice_meta.get("speaker_id")
            return describe_tts_selection_policy(
                tts_engine="piper",
                strict_commercial_safe=True,
                piper_voice_id=str(voice_meta.get("id") or "").strip(),
                piper_speaker_id=int(speaker_id) if speaker_id is not None else None,
                piper_model_url=str(voice_meta.get("model_url") or "").strip() or None,
                piper_config_url=str(voice_meta.get("config_url") or "").strip() or None,
            )
        if engine == "kokoro":
            return describe_tts_selection_policy(
                tts_engine="kokoro",
                strict_commercial_safe=True,
                kokoro_lang_code=str(entry.get("lang_code") or self.config.kokoro_lang_code),
                kokoro_voice=str(entry.get("voice") or "").strip() or None,
            )
        return describe_tts_selection_policy(
            tts_engine="melo",
            strict_commercial_safe=True,
            melo_language=self.config.melo_language,
            melo_speaker=str(entry.get("speaker") or self.config.melo_speaker),
        )

    def _tts_policy_badge(self, policy: dict[str, Any]) -> str:
        result = str(policy.get("policy_result") or "warn").strip().lower()
        if result == "allow":
            return "safe"
        if result == "deny":
            return "blocked"
        return "review"

    def _tts_policy_text(self, policy: dict[str, Any]) -> str:
        # Concise TTS display: badge and license only
        parts = [self._tts_policy_badge(policy)]
        license_name = str(policy.get("license_name") or "").strip()
        if license_name:
            parts.append(license_name)
        return " | ".join(parts)

    def _tts_status_display(self) -> tuple[str, int]:
        policy = self._tts_policy_for_current_selection()
        result = str(policy.get("policy_result") or "warn").strip().lower()
        text = self._tts_policy_text(policy)
        if result == "allow":
            return text, self._attr("ok", bold=True)
        if result == "deny":
            return text, self._attr("error", bold=True)
        return text, self._attr("warn", bold=True)

    def _voice_selection_option_detail(self, entry: dict[str, Any]) -> str:
        policy = cast(dict[str, Any], entry.get("policy") or self._tts_policy_for_entry(entry))
        selection = str(policy.get("voice_display") or entry.get("base_label") or entry.get("label") or "").strip()
        result = str(policy.get("policy_result") or "warn").strip().lower()
        status = "Allowed" if result == "allow" else ("Blocked" if result == "deny" else "Needs review")
        reason = str(policy.get("reason") or "").strip()
        license_name = str(policy.get("license_name") or "Unknown license").strip()
        source = str(policy.get("source") or "local").strip()
        summary = f"{status} in strict-safe mode. License: {license_name}. Source: {source}."
        if selection:
            summary = f"{selection}. {summary}"
        if reason:
            summary = f"{summary} {reason}"
        return summary.strip()

    def _decorate_voice_selection_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        decorated: list[dict[str, Any]] = []
        for item in entries:
            entry = dict(item)
            base_label = str(entry.get("label") or "").strip()
            if not base_label:
                continue
            policy = self._tts_policy_for_entry(entry)
            entry["base_label"] = base_label
            entry["policy"] = policy
            entry["label"] = f"{base_label} [{self._tts_policy_badge(policy)}]"
            decorated.append(entry)
        return decorated

    def _passes_tts_policy_precheck(self) -> bool:
        policy = self._tts_policy_for_current_selection()
        if str(policy.get("policy_result") or "") != "deny":
            return True
        selection = str(policy.get("voice_display") or self._voice_display_value()).strip() or self._voice_display_value()
        reason = str(policy.get("reason") or "Narration voice is blocked by policy.").strip()
        self._append_log(f"ERROR: TTS policy blocked narration selection: {selection}. {reason}")
        self._set_status(f"TTS policy blocked run: {selection}.")
        return False

    def _tts_policy_summary(self) -> str:
        policy = self._tts_policy_for_current_selection()
        selection = str(policy.get("voice_display") or self._voice_display_value()).strip() or self._voice_display_value()
        return f"{selection} [{self._tts_policy_badge(policy)}]"

    def _tts_policy_notes_text(self) -> str:
        current_policy = self._tts_policy_for_current_selection()
        allowed_piper: list[str] = []
        blocked_piper: list[str] = []
        for voice_meta in self.DEBUG_PIPER_VOICES:
            speaker_id = voice_meta.get("speaker_id")
            policy = describe_tts_selection_policy(
                tts_engine="piper",
                strict_commercial_safe=True,
                piper_voice_id=str(voice_meta.get("id") or "").strip(),
                piper_speaker_id=int(speaker_id) if speaker_id is not None else None,
            )
            label = str(voice_meta.get("label") or voice_meta.get("id") or "Piper").strip()
            if str(policy.get("policy_result") or "") == "deny":
                blocked_piper.append(label)
            else:
                allowed_piper.append(label)

        lines = [
            "TUI narration policy",
            "",
            "Strict commercial-safe mode is always on in the TUI.",
            "",
            "Current selection",
            f"Voice: {str(current_policy.get('voice_display') or self._voice_display_value()).strip()}",
            f"Status: {self._tts_policy_badge(current_policy)}",
            f"License: {str(current_policy.get('license_name') or 'Unknown').strip()}",
            f"Source: {str(current_policy.get('source') or 'local').strip()}",
            f"Reason: {str(current_policy.get('reason') or '').strip()}",
        ]
        note = str(current_policy.get("note") or "").strip()
        if note:
            lines.append(f"Note: {note}")

        lines.extend(
            [
                "",
                "Catalog rules",
                "Melo: allowed as the current baseline engine, but review the installed voice package before shipping.",
                "Kokoro: allowlisted for strict-safe use and preferred for higher-quality local narration.",
                "Piper: only curated allowlisted presets stay selectable in strict-safe mode.",
                "Piper custom URLs: blocked in strict-safe mode until a model/weights allowlist exists.",
                "",
                "Allowed Piper presets",
            ]
        )
        lines.extend(f"- {label}" for label in allowed_piper)
        lines.append("")
        lines.append("Blocked Piper presets")
        lines.extend(f"- {label}" for label in blocked_piper)
        return "\n".join(lines).strip()

    def _state_display(self) -> tuple[str, int]:
        running = self._is_running()
        elapsed = self._elapsed_seconds()

        if running:
            frame = self._spinner_frame()
            return f"{frame} RUNNING  {self._format_runtime_elapsed(elapsed)}", self._attr("ok", bold=True)

        if self._last_elapsed_seconds is not None:
            return f"IDLE  last run {self._last_elapsed_seconds:.1f}s", self._attr("muted")

        return "IDLE", self._attr("muted")

    def _format_runtime_elapsed(self, elapsed_seconds: float) -> str:
        elapsed = max(0.0, float(elapsed_seconds))
        if elapsed < 60.0:
            return f"{elapsed:.1f}s elapsed"

        total_seconds = int(elapsed)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d} elapsed"
        return f"{minutes:02d}:{seconds:02d} elapsed"

    def _footer_text(self, total_logs: int) -> str:
        return f"Logs: {total_logs} | File: {self._latest_log_path}"

    def _draw_box(self, top: int, left: int, box_height: int, box_width: int, title: str, attr: int = 0) -> None:
        if self._stdscr is None:
            return
        if box_height < 3 or box_width < 4:
            return

        bottom = top + box_height - 1
        right = left + box_width - 1
        if bottom < 0 or right < 0:
            return

        try:
            self._stdscr.addch(top, left, curses.ACS_ULCORNER, attr)
            self._stdscr.addch(top, right, curses.ACS_URCORNER, attr)
            self._stdscr.addch(bottom, left, curses.ACS_LLCORNER, attr)
            self._stdscr.addch(bottom, right, curses.ACS_LRCORNER, attr)
            self._stdscr.hline(top, left + 1, curses.ACS_HLINE, max(0, box_width - 2), attr)
            self._stdscr.hline(bottom, left + 1, curses.ACS_HLINE, max(0, box_width - 2), attr)
            for row in range(top + 1, bottom):
                self._stdscr.addch(row, left, curses.ACS_VLINE, attr)
                self._stdscr.addch(row, right, curses.ACS_VLINE, attr)
        except curses.error:
            return

        safe_title = self._trim_tail(title, max(1, box_width - 4))
        self._safe_addstr(top, left + 2, safe_title, left + box_width, attr=self._attr("accent", bold=True))

    def _is_modal_active(self) -> bool:
        with self._lock:
            return self._modal_depth > 0

    @contextmanager
    def _modal_focus(self) -> Any:
        with self._lock:
            self._modal_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._modal_depth = max(0, self._modal_depth - 1)

    def _safe_hline(self, row: int, width: int) -> None:
        if self._stdscr is None:
            return
        try:
            self._stdscr.hline(row, 0, curses.ACS_HLINE, max(1, width - 1), self._attr("muted"))
        except curses.error:
            pass

    def _draw_modal_backdrop(self, height: int, width: int) -> None:
        if self._stdscr is None or height <= 0 or width <= 0:
            return

        attr = self._attr("backdrop")
        if attr == 0:
            attr = curses.A_REVERSE | curses.A_DIM

        for row in range(height):
            try:
                self._stdscr.hline(row, 0, ord(" "), max(1, width), attr)
            except curses.error:
                try:
                    self._stdscr.addstr(row, 0, " " * max(1, width - 1), attr)
                except curses.error:
                    pass

    def _visible_logs(self, logs: list[str], rows: int) -> list[str]:
        total = len(logs)
        if total <= rows:
            return logs

        start = max(0, total - rows)
        end = min(total, start + rows)
        return logs[start:end]

    def _log_line_attr(self, line: str) -> int:
        upper = line.upper()
        if "ERROR" in upper:
            return self._attr("error", bold=True)
        if "WARN" in upper:
            return self._attr("warn", bold=True)
        if line.startswith("$"):
            return self._attr("accent", bold=True)
        if "EXITED WITH CODE 0" in upper:
            return self._attr("ok")
        return 0

    def _spinner_frame(self) -> str:
        index = int(time.monotonic() * 8) % len(self.SPINNER_FRAMES)
        return self.SPINNER_FRAMES[index]

    def _normalize_resolution_value(self, value: str | None) -> str:
        raw = str(value or "").strip().lower()
        for label, preset in self.RESOLUTION_PRESETS.items():
            if raw in {label.lower(), preset.lower()}:
                return preset
        return self.RESOLUTION_PRESETS["720p"]

    def _resolution_label(self, resolution: str | None = None) -> str:
        normalized = self._normalize_resolution_value(resolution or self.config.resolution)
        for label, preset in self.RESOLUTION_PRESETS.items():
            if preset == normalized:
                return label
        return normalized

    def _display_stage_name(self, stage: str | None = None) -> str:
        stage_value = str(stage or self._hitl_stage).strip().lower()
        labels = {
            "sources": "sources",
            "source-review": "source review",
            "draft": "draft",
            "script-review": "script review",
            "shot-plan": "shot plan",
            "shot-review": "shot review",
            "review": "review",
            "preview": "preview",
            "finalize": "finalize",
            "full": "full",
            "done": "done",
        }
        return labels.get(stage_value, stage_value.replace("-", " ") or "draft")

    def _content_mode(self) -> str:
        return normalize_content_mode(self.config.content_mode, "explainer")

    def _apply_default_news_feeds_if_needed(self) -> None:
        if self._content_mode() != "news":
            return
        if self.config.news_feed_urls:
            return
        self.config.news_feed_urls = default_news_feed_urls()

    def _enforce_news_hitl_policy(self, *, persist: bool = False) -> bool:
        if self._content_mode() != "news" or self._hitl_enabled:
            return False
        self._hitl_enabled = True
        self._append_log("News mode requires HITL source review. Forcing HITL On.")
        if persist:
            self._save_persisted_settings()
        return True

    def _progress_phase_text(self) -> str:
        with self._lock:
            stage_index = self._stage_index
            stage_total = self._stage_total
            stage_label = self._stage_label
            workflow_kind = self._workflow_kind

        if workflow_kind != "run":
            if workflow_kind == "inspect":
                return "Inspect workflow"
            if workflow_kind == "replace":
                return "Clip replacement workflow"
            return f"HITL ready: {self._display_stage_name()}"

        if stage_index is None or stage_total is None or stage_total <= 0:
            return f"Starting {self._display_stage_name()} stage..."

        if float(stage_index).is_integer():
            stage_idx_text = str(int(stage_index))
        else:
            stage_idx_text = f"{stage_index:.1f}"

        if stage_label:
            return f"Stage {stage_idx_text}/{stage_total}: {stage_label}"
        return f"Stage {stage_idx_text}/{stage_total}"

    def _asset_status_display(self) -> tuple[str, int]:
        ready_labels = [str(spec.get("label") or "").strip().lower() for spec in self._ready_provider_specs()]
        enabled_labels = [str(spec.get("label") or "").strip().lower() for spec in self._enabled_provider_specs()]
        asset_mode_label = self._asset_mode_label()

        def format_ready_text(labels: list[str]) -> str:
            non_experimental = [label.title() for label in labels if "experimental" not in label]
            experimental = [label.replace(" (experimental)", "").title() for label in labels if "experimental" in label]
            provider_parts: list[str] = []
            if non_experimental:
                provider_parts.extend(non_experimental)
            if experimental:
                provider_parts.append("/".join(experimental) + " beta")
            provider_text = ", ".join(provider_parts) if provider_parts else "Stock"
            return f"ready ({provider_text}; {asset_mode_label})"

        if ready_labels:
            if len(ready_labels) == len(enabled_labels):
                return format_ready_text(ready_labels), self._attr("ok", bold=True)
            return f"limited ({', '.join(label.title() for label in ready_labels)}; {asset_mode_label})", self._attr("warn", bold=True)

        if enabled_labels:
            return f"keys missing ({asset_mode_label})", self._attr("warn", bold=True)
        return "stock disabled in settings", self._attr("warn", bold=True)

    def _ollama_status_display(self) -> tuple[str, int]:
        engine = str(getattr(self.config, "script_engine", "ollama") or "ollama").strip().lower()
        if engine != "ollama":
            return "not in use", self._attr("muted")

        if shutil.which("ollama") is None:
            return "missing from PATH", self._attr("error", bold=True)

        with self._lock:
            process = self._ollama_process
            starting = self._ollama_starting
            checked_at = self._ollama_probe_checked_at
            cached_online = self._ollama_probe_online

        if starting:
            return "starting...", self._attr("warn", bold=True)

        process_alive = process is not None and process.poll() is None
        now = time.monotonic()
        probe_interval = 1.0 if process_alive else 3.0
        should_probe = cached_online is None or (now - checked_at) >= probe_interval
        if should_probe:
            cached_online = self._probe_ollama_online(timeout=1.2)
            with self._lock:
                self._ollama_probe_online = cached_online
                self._ollama_probe_checked_at = now

        if cached_online:
            return "online", self._attr("ok", bold=True)

        if process is not None:
            code = process.poll()
            if code is None:
                return "starting...", self._attr("warn", bold=True)
            return f"offline (process exited {code})", self._attr("error", bold=True)

        return "offline", self._attr("warn", bold=True)

    def _probe_ollama_online(self, timeout: float = 1.2) -> bool:
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception:
            return False
        return result.returncode == 0

    def _asset_provider_specs(self) -> tuple[dict[str, Any], ...]:
        return self.ASSET_PROVIDER_POLICIES

    def _asset_provider_spec(self, provider_key: str) -> dict[str, Any]:
        key = str(provider_key or "").strip().lower()
        for spec in self._asset_provider_specs():
            if str(spec.get("key") or "").strip().lower() == key:
                return spec
        return {}

    def _provider_env_keys(self, provider_spec: dict[str, Any]) -> list[str]:
        raw_env_keys = provider_spec.get("env_keys")
        if isinstance(raw_env_keys, (list, tuple)):
            return [str(item).strip() for item in raw_env_keys if str(item).strip()]
        legacy_env_key = str(provider_spec.get("env_key") or "").strip()
        return [legacy_env_key] if legacy_env_key else []

    def _is_provider_enabled(self, provider_key: str) -> bool:
        key = str(provider_key or "").strip().lower()
        if key == "pexels":
            return bool(self.config.enable_pexels_provider)
        if key == "pixabay":
            return bool(self.config.enable_pixabay_provider)
        if key == "coverr":
            return bool(self.config.enable_coverr_provider)
        if key == "vecteezy":
            return bool(self.config.enable_vecteezy_provider)
        return False

    def _set_provider_enabled(self, provider_key: str, enabled: bool) -> None:
        key = str(provider_key or "").strip().lower()
        if key == "pexels":
            self.config.enable_pexels_provider = bool(enabled)
        elif key == "pixabay":
            self.config.enable_pixabay_provider = bool(enabled)
        elif key == "coverr":
            self.config.enable_coverr_provider = bool(enabled)
        elif key == "vecteezy":
            self.config.enable_vecteezy_provider = bool(enabled)

    def _enabled_provider_specs(self) -> list[dict[str, Any]]:
        return [spec for spec in self._asset_provider_specs() if self._is_provider_enabled(str(spec.get("key") or ""))]

    def _enabled_provider_labels(self) -> list[str]:
        return [str(spec.get("label") or "").strip() for spec in self._enabled_provider_specs() if str(spec.get("label") or "").strip()]

    def _ready_provider_specs(self) -> list[dict[str, Any]]:
        with self._lock:
            stock_keys = dict(self._stock_api_keys)
        ready: list[dict[str, Any]] = []
        for spec in self._enabled_provider_specs():
            env_keys = self._provider_env_keys(spec)
            if env_keys and all(stock_keys.get(env_key) for env_key in env_keys):
                ready.append(spec)
        return ready

    def _missing_key_provider_specs(self) -> list[dict[str, Any]]:
        with self._lock:
            stock_keys = dict(self._stock_api_keys)
        missing: list[dict[str, Any]] = []
        for spec in self._enabled_provider_specs():
            env_keys = self._provider_env_keys(spec)
            if env_keys and any(not stock_keys.get(env_key) for env_key in env_keys):
                missing.append(spec)
        return missing

    def _normalized_asset_mode(self) -> str:
        value = str(self.config.asset_mode or "prefer-video").strip().lower()
        if value not in self.ASSET_MODE_CHOICES:
            return "prefer-video"
        return value

    def _normalized_image_motion_style(self) -> str:
        value = str(self.config.image_motion_style or "slow").strip().lower()
        value = self.IMAGE_MOTION_STYLE_ALIASES.get(value, value)
        if value not in self.IMAGE_MOTION_STYLE_CHOICES:
            return "slow"
        return value

    def _normalized_subtitle_preset(self) -> str:
        return normalize_subtitle_preset(self.config.subtitle_preset, "regular")

    def _normalized_subtitle_position(self) -> str:
        return normalize_subtitle_position(self.config.subtitle_position, "bottom")

    def _normalized_subtitle_accent_color(self) -> str:
        return normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower")

    def _normalized_subtitle_box_color(self) -> str:
        return normalize_subtitle_box_color(
            self.config.subtitle_box_color,
            self._normalized_subtitle_accent_color(),
        )

    def _normalized_caption_font_scale(self) -> float:
        return normalize_caption_font_scale(self.config.caption_font_scale, 0.9)

    def _normalized_subtitle_bold(self) -> bool:
        return bool(self.config.subtitle_bold)

    def _normalized_subtitle_outline(self) -> bool:
        return bool(self.config.subtitle_outline)

    def _asset_mode_label(self) -> str:
        return self._normalized_asset_mode().replace("-", " ")

    def _image_motion_style_label(self) -> str:
        return self._normalized_image_motion_style().replace("-", " ")

    def _subtitle_preset_label(self) -> str:
        return self._normalized_subtitle_preset().replace("-", " ")

    def _subtitle_position_label(self) -> str:
        return self._normalized_subtitle_position().replace("-", " ")

    def _subtitle_accent_color_label(self) -> str:
        return self._normalized_subtitle_accent_color().replace("-", " ")

    def _subtitle_box_color_label(self) -> str:
        return self._normalized_subtitle_box_color().replace("-", " ")

    def _subtitle_font_size_label(self) -> str:
        scale = self._normalized_caption_font_scale()
        for label, value in self.SUBTITLE_FONT_SIZE_PRESETS:
            if abs(scale - value) <= 0.01:
                return label
        return f"Custom ({scale:.2f})"

    def _subtitle_bold_label(self) -> str:
        return "On" if self._normalized_subtitle_bold() else "Off"

    def _subtitle_outline_label(self) -> str:
        return "On" if self._normalized_subtitle_outline() else "Off"

    def _asset_policy_summary(self) -> str:
        enabled_labels = self._enabled_provider_labels()
        provider_text = ", ".join(enabled_labels) if enabled_labels else "(none)"
        return (
            f"Providers: {provider_text} | "
            f"Mode: {self._asset_mode_label()} | "
            f"Images: {'on' if self.config.allow_image_assets else 'off'} | "
            f"Image motion: {self._image_motion_style_label()} | "
            f"Attribution-required: {'on' if self.config.allow_attribution_required_assets else 'off'}"
        )

    def _asset_preflight_message(self) -> str:
        enabled_labels = self._enabled_provider_labels()
        ready_labels = [str(spec.get("label") or "").strip() for spec in self._ready_provider_specs() if str(spec.get("label") or "").strip()]
        missing_labels = [str(spec.get("label") or "").strip() for spec in self._missing_key_provider_specs() if str(spec.get("label") or "").strip()]
        policy_summary = self._asset_policy_summary()

        if ready_labels:
            ready_text = ", ".join(ready_labels)
            if missing_labels:
                missing_text = ", ".join(missing_labels)
                return (
                    f"Preflight: stock ready for {ready_text}; keys missing for {missing_text}. "
                    f"{policy_summary}. Placeholders only if enabled-provider searches/downloads fail."
                )
            return (
                f"Preflight: stock ready for {ready_text}. "
                f"{policy_summary}. Placeholders only if enabled-provider searches/downloads fail."
            )

        if enabled_labels:
            return (
                "Preflight: no enabled stock provider has a usable API key. "
                f"{policy_summary}. Hard guard blocks this run."
            )
        return "Preflight: no stock providers are enabled. Hard guard blocks this run."

    def _passes_asset_hard_guard_precheck(self) -> bool:
        ready_specs = self._ready_provider_specs()
        if ready_specs:
            return True

        enabled_specs = self._enabled_provider_specs()
        if not enabled_specs:
            self._append_log("ERROR: Hard guard blocked run: all stock providers are disabled in settings.")
            self._set_status("Hard guard: no stock providers enabled. Run blocked.")
            return False

        missing_specs = self._missing_key_provider_specs()
        missing_env_keys: list[str] = []
        for spec in missing_specs:
            for env_key in self._provider_env_keys(spec):
                if env_key not in missing_env_keys:
                    missing_env_keys.append(env_key)
        self._append_log("ERROR: Hard guard blocked run: enabled stock providers are missing API keys.")
        if missing_env_keys:
            self._append_log(
                "Set "
                + " and/or ".join(missing_env_keys)
                + " in env, repo .env, or ~/.config/imagine/stock_api_keys.json"
            )
        self._set_status("Hard guard: missing keys for enabled providers. Run blocked.")
        return False

    def _refresh_stock_key_cache(self) -> None:
        dotenv_keys, dotenv_warnings = self._read_repo_dotenv_keys()
        user_keys, user_warnings = self._read_user_stock_keys()

        resolved: dict[str, str] = {}
        sources: dict[str, str] = {}
        warnings: list[str] = []

        warnings.extend(dotenv_warnings)
        warnings.extend(user_warnings)

        for key in self.STOCK_ENV_KEYS:
            process_value = (os.environ.get(key) or "").strip()
            dotenv_value = (dotenv_keys.get(key) or "").strip()
            user_value = (user_keys.get(key) or "").strip()

            if process_value:
                resolved[key] = process_value
                sources[key] = "process_env"
                continue
            if dotenv_value:
                resolved[key] = dotenv_value
                sources[key] = "repo_.env"
                continue
            if user_value:
                resolved[key] = user_value
                sources[key] = "user_config"

        with self._lock:
            self._stock_api_keys = resolved
            self._stock_key_sources = sources
            self._stock_key_warnings = warnings

    def _load_dotenv_into_environ(self) -> None:
        """Load all keys from the repo .env into os.environ (without overwriting existing vars).

        This mirrors `set -a; source .env` from the CLI pattern so that libraries like
        phonemizer that read directly from os.environ (e.g. PHONEMIZER_ESPEAK_LIBRARY)
        work correctly when the TUI is launched without a pre-sourced env.
        """
        dotenv_path = self._repo_root / ".env"
        if not dotenv_path.exists():
            return
        try:
            for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                raw_key, raw_value = line.split("=", maxsplit=1)
                key = raw_key.strip()
                if not key or key in os.environ:
                    continue
                value = raw_value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                else:
                    value = value.split(" #", maxsplit=1)[0].strip()
                if value:
                    os.environ[key] = value
        except Exception:  # noqa: BLE001
            pass

    def _read_repo_dotenv_keys(self) -> tuple[dict[str, str], list[str]]:
        dotenv_path = self._repo_root / ".env"
        values: dict[str, str] = {}
        warnings: list[str] = []

        if not dotenv_path.exists():
            return values, warnings

        try:
            for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue

                raw_key, raw_value = line.split("=", maxsplit=1)
                key = raw_key.strip()
                if key not in self.STOCK_ENV_KEYS:
                    continue

                value = raw_value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                else:
                    value = value.split(" #", maxsplit=1)[0].strip()

                if value:
                    values[key] = value
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not parse .env for stock keys: {exc}")

        return values, warnings

    def _read_user_stock_keys(self) -> tuple[dict[str, str], list[str]]:
        config_raw = os.environ.get("IMAGINE_STOCK_KEYS_FILE", "~/.config/imagine/stock_api_keys.json")
        config_path = Path(config_raw).expanduser().resolve()

        values: dict[str, str] = {}
        warnings: list[str] = []

        if not config_path.exists():
            return values, warnings

        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                warnings.append(f"Stock key file is not a JSON object: {config_path}")
                return values, warnings

            for key in self.STOCK_ENV_KEYS:
                raw_value = payload.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    values[key] = raw_value.strip()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not parse stock key file {config_path}: {exc}")

        return values, warnings

    def _estimate_progress_percent(self, elapsed_seconds: float) -> float:
        with self._lock:
            stage_index = self._stage_index
            stage_total = self._stage_total
            prior_total = self._prior_total_seconds

        stage_percent: float | None = None
        if stage_index is not None and stage_total and stage_total > 0:
            stage_percent = ((max(1.0, stage_index) - 1.0) / float(stage_total)) * 100.0
            stage_percent = max(0.0, min(99.0, stage_percent))

        time_percent: float | None = None
        if prior_total is not None and prior_total > 1.0:
            time_percent = max(0.0, min(99.0, (elapsed_seconds / prior_total) * 100.0))

        if stage_percent is not None and time_percent is not None:
            return max(stage_percent, time_percent)
        if time_percent is not None:
            return time_percent
        if stage_percent is not None:
            return stage_percent
        return 0.0

    def _estimate_eta_seconds(self, elapsed_seconds: float, progress_percent: float) -> float | None:
        with self._lock:
            prior_total = self._prior_total_seconds

        if prior_total is not None:
            return max(0.0, prior_total - elapsed_seconds)
        return None

    def _format_eta(self, eta_seconds: float | None) -> str:
        if eta_seconds is None:
            return "--:--"
        minutes = int(eta_seconds // 60)
        seconds = int(eta_seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _update_status_from_log_line(self, line: str) -> None:
        stage_match = self.STAGE_LINE_RE.search(line)
        if stage_match:
            stage_index = float(stage_match.group(1))
            stage_total = int(stage_match.group(2))
            stage_label = stage_match.group(3).strip()
            with self._lock:
                self._stage_index = stage_index
                self._stage_total = stage_total
                self._stage_label = stage_label
            self._set_status(f"Stage {stage_match.group(1)}/{stage_total}: {stage_label}")
            return

        complete_match = self.STAGE_COMPLETE_RE.search(line)
        if complete_match:
            stage_name = complete_match.group(1)
            stage_seconds = complete_match.group(2)
            self._set_status(f"{stage_name} completed in {stage_seconds}s")
            return

        if line.startswith("[local-video-mvp]"):
            message = line.split("]", maxsplit=1)[-1].strip()
            if message:
                self._set_status(message)

    def _refresh_prior_total_seconds(self) -> None:
        report_path = self._latest_run_report_path()
        prior_total: float | None = None

        if report_path is not None and report_path.exists():
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                raw_total = payload.get("total_seconds")
                if isinstance(raw_total, (int, float)) and float(raw_total) > 0:
                    prior_total = float(raw_total)
            except Exception:
                prior_total = None

        with self._lock:
            self._prior_total_seconds = prior_total

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
        slug = slug.strip("-")
        return slug or "imagine"

    def _normalize_asset_keywords(self, raw: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            value = str(item).strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(value)
        return out[:20]

    def _iter_project_workspaces(self) -> list[Path]:
        root = self.config.project_dir
        if not root.exists():
            return []

        entries: list[Path] = []
        for path in root.iterdir():
            if path.is_dir():
                entries.append(path)

        entries.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return entries

    def _latest_project_workspace(self) -> Path | None:
        entries = self._iter_project_workspaces()
        return entries[0] if entries else None

    def _thumbnail_studio_comfyui_url(self) -> str:
        return (
            str(getattr(self.config, "thumbnail_comfyui_url", THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL) or "").strip().rstrip("/")
            or THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL
        )

    def _thumbnail_studio_export_dir(self) -> Path:
        return Path(
            str(getattr(self.config, "thumbnail_default_export_dir", THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR) or "").strip()
            or str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR)
        ).expanduser().resolve()

    def _thumbnail_studio_ollama_hooks_enabled(self) -> bool:
        return bool(getattr(self.config, "thumbnail_use_ollama_hooks", True))

    def _managed_comfyui_install_dir(self) -> Path:
        return (Path.home() / ".imagine" / "tools" / "ComfyUI").resolve()

    def _comfyui_log_path(self) -> Path:
        return Path("/tmp/local-video-mvp-comfyui.log").resolve()

    def _comfyui_checkpoint_count(self, install_dir: Path | None = None) -> int:
        target = (install_dir or self._thumbnail_comfyui_install_dir())
        if target is None:
            return 0
        checkpoints_dir = target / "models" / "checkpoints"
        if not checkpoints_dir.exists():
            return 0
        count = 0
        try:
            for path in checkpoints_dir.iterdir():
                if path.is_file() and path.suffix.lower() in {".ckpt", ".pt", ".pth", ".safetensors"}:
                    count += 1
        except Exception:
            return 0
        return count

    def _show_thumbnail_studio_setup_help(self, detail: str) -> None:
        install_dir = self._thumbnail_comfyui_install_dir()
        body = "\n".join(
            [
                "Thumbnail Studio could not generate images yet.",
                "",
                f"ComfyUI URL: {self._thumbnail_studio_comfyui_url()}",
                f"Detected install: {str(install_dir) if install_dir is not None else '(not detected)'}",
                f"Detail: {detail}",
                "",
                "Setup notes:",
                "- The TUI will auto-start a detected local ComfyUI install when possible.",
                "- Make sure at least one checkpoint is installed.",
                "- You can change the ComfyUI URL or install path in Settings.",
                "- If auto-start fails after launch, inspect /tmp/local-video-mvp-comfyui.log.",
                "",
                "You can stay in the studio, edit the prompt/headline, and retry generation.",
            ]
        )
        self._show_paginated_text_modal("Thumbnail Studio Setup", body)

    def _thumbnail_studio_session_option_details(self, session: ThumbnailStudioSession) -> str:
        variant_count = len(session.variants)
        updated_text = self._format_timestamp_text(session.updated_at)
        selected = selected_thumbnail_variant(session)
        selected_text = selected.variant_id if selected is not None else "none"
        return (
            f"{self._trim_tail(session.prompt, 48)} | updated {updated_text} | "
            f"{variant_count} variants | selected {selected_text}"
        )

    def _format_timestamp_text(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "unknown"
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return raw

    def _select_recent_thumbnail_studio_session(self) -> ThumbnailStudioSession | None:
        sessions = list_thumbnail_studio_sessions(limit=30)
        if not sessions:
            self._show_paginated_text_modal(
                "Recent Thumbnail Sessions",
                "No saved thumbnail sessions were found yet.\n\nChoose `New from prompt` to create the first one.",
            )
            self._set_status("No saved thumbnail sessions yet.")
            return None
        option_details: dict[str, str] = {}
        label_to_session: dict[str, ThumbnailStudioSession] = {}
        labels: list[str] = []
        for session in sessions:
            label = self._trim_tail(f"{session.session_id} | {session.prompt}", 88)
            option_details[label] = self._thumbnail_studio_session_option_details(session)
            label_to_session[label] = session
            labels.append(label)
        selected = self._select_from_list(
            label="Recent Thumbnail Sessions",
            options=labels,
            current_value=labels[0],
            option_details=option_details,
        )
        if selected is None:
            self._set_status("Recent thumbnail sessions closed.")
            return None
        chosen = label_to_session.get(str(selected))
        if chosen is None:
            self._set_status("Thumbnail session selection failed.")
            return None
        try:
            return load_thumbnail_studio_session(thumbnail_studio_session_dir(chosen.session_id))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Could not load thumbnail session {chosen.session_id}: {exc}")
            self._set_status("Could not load thumbnail session. See logs.")
            return None

    def _create_thumbnail_studio_session_flow(self) -> ThumbnailStudioSession | None:
        prompt_text = self._prompt_input("Thumbnail prompt", self.config.prompt or "Your thumbnail idea")
        if prompt_text is None:
            self._set_status("Thumbnail Studio cancelled.")
            return None
        cleaned_prompt = str(prompt_text).strip()
        if not cleaned_prompt:
            self._set_status("Thumbnail prompt cannot be empty.")
            return None

        style_hint = self._prompt_input("Style hint (optional)", "")
        if style_hint is None:
            style_hint = ""
        badge_text = self._prompt_input("Badge text (optional)", "")
        if badge_text is None:
            badge_text = ""

        try:
            session = self._run_with_spinner_modal(
                title="Thumbnail Studio",
                message="Creating session",
                detail_text=cleaned_prompt,
                task=lambda: create_thumbnail_studio_session(
                    prompt=cleaned_prompt,
                    style_hint=str(style_hint or "").strip(),
                    badge_text=str(badge_text or "").strip(),
                    use_ollama_hooks=self._thumbnail_studio_ollama_hooks_enabled(),
                    ollama_model="qwen2.5:14b",
                    notify=self._append_log,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Could not create thumbnail studio session: {exc}")
            self._set_status("Thumbnail Studio setup failed. See logs.")
            return None

        session_obj = cast(ThumbnailStudioSession, session)
        try:
            self._generate_thumbnail_studio_batch(session_obj, append=True)
        except RuntimeError as exc:
            self._append_log(f"WARN: Thumbnail generation unavailable: {exc}")
            self._show_thumbnail_studio_setup_help(str(exc))
        return session_obj

    def _generate_thumbnail_studio_batch(self, session: ThumbnailStudioSession, *, append: bool) -> bool:
        if not self._ensure_comfyui_available_with_modal():
            raise RuntimeError("ComfyUI is unavailable.")
        try:
            generated = self._run_with_spinner_modal(
                title="Thumbnail Studio",
                message="Generating thumbnail variants",
                detail_text=session.prompt,
                task=lambda: generate_thumbnail_studio_variants(
                    session,
                    comfyui_url=self._thumbnail_studio_comfyui_url(),
                    variant_count=4,
                    append=append,
                    notify=self._append_log,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(str(exc)) from exc
        generated_list = cast(list[Any], generated or [])
        if not generated_list:
            raise RuntimeError("No thumbnail variants were produced.")
        latest_variant = generated_list[0]
        if hasattr(latest_variant, "variant_id"):
            session.selected_variant_id = str(getattr(latest_variant, "variant_id"))
        save_thumbnail_studio_session(session)
        return True

    def _select_thumbnail_studio_variant(self, session: ThumbnailStudioSession) -> bool:
        if not session.variants:
            self._set_status("No thumbnail variants are available yet.")
            return False
        selected = selected_thumbnail_variant(session)
        option_details: dict[str, str] = {}
        labels: list[str] = []
        for variant in session.variants:
            label = f"{variant.variant_id} | {preset_label(variant.preset)}"
            labels.append(label)
            notes = ", ".join(variant.score_notes) if variant.score_notes else "no notes"
            option_details[label] = f"Seed {variant.seed} | {notes}"
        current_label = labels[0]
        if selected is not None:
            selected_label = f"{selected.variant_id} | {preset_label(selected.preset)}"
            if selected_label in labels:
                current_label = selected_label
        choice = self._select_from_list(
            label="Thumbnail Variants",
            options=labels,
            current_value=current_label,
            option_details=option_details,
        )
        if choice is None:
            self._set_status("Thumbnail variant selection cancelled.")
            return False
        selected_text = str(choice)
        for variant in session.variants:
            label = f"{variant.variant_id} | {preset_label(variant.preset)}"
            if label == selected_text:
                session.selected_variant_id = variant.variant_id
                session.preset = variant.preset
                save_thumbnail_studio_session(session)
                return True
        return False

    def _thumbnail_studio_preview_detail_lines(self, session: ThumbnailStudioSession) -> list[str]:
        lines = [
            f"Prompt: {session.prompt}",
            f"Headline: {session.headline_text or '(empty)'}",
            f"Badge: {session.badge_text or '(none)'}",
            f"Preset: {preset_label(session.preset)}",
            f"Palette: {palette_label(session.palette)}",
            f"Variants: {len(session.variants)}",
        ]
        selected = selected_thumbnail_variant(session)
        if selected is not None:
            lines.append(f"Selected: {selected.variant_id} | seed {selected.seed}")
            for note in selected.score_notes:
                lines.append(f"Note: {note}")
        if session.exported_path:
            lines.append(f"Exported: {session.exported_path}")
        return lines

    def _show_standalone_thumbnail_preview_modal(self, session: ThumbnailStudioSession) -> None:
        preview_path = thumbnail_studio_session_preview_path(session)
        if preview_path is None:
            self._set_status("No thumbnail preview is available yet.")
            return
        if self._stdscr is None:
            self._play_media_path(
                preview_path,
                label=f"thumbnail-studio-{self._safe_filename_token(preview_path.stem)}",
                audio_only=False,
            )
            return

        preview_image_path = self._preview_png_from_media_path(preview_path, media_type="image") or preview_path
        detail_lines = self._thumbnail_studio_preview_detail_lines(session)

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            modal_width = min(max(92, width - 4), max(30, width - 2))
            modal_height = min(max(22, height - 4), max(14, height - 2))
            if modal_width < 30 or modal_height < 14:
                self._show_paginated_text_modal("Thumbnail Preview", "\n".join(detail_lines))
                return

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            preview_box_top = 2
            preview_box_height = max(10, min(16, modal_height - 9))
            preview_inner_width = max(12, modal_width - 8)
            detail_top = preview_box_top + preview_box_height + 1
            detail_rows = max(3, modal_height - detail_top - 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                    win.addstr(0, 2, self._trim_tail(" Thumbnail Preview ", modal_width - 4), self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail("Enter/Esc back", modal_width - 4), self._attr("muted"))
                    win.addstr(preview_box_top, 2, "+" + ("-" * preview_inner_width) + "+", self._attr("muted"))
                    win.addstr(preview_box_top + preview_box_height - 1, 2, "+" + ("-" * preview_inner_width) + "+", self._attr("muted"))
                except curses.error:
                    pass
                for row in range(max(0, preview_box_height - 2)):
                    try:
                        win.addstr(preview_box_top + 1 + row, 2, "|" + (" " * preview_inner_width) + "|")
                    except curses.error:
                        pass
                for row in range(detail_rows):
                    line = detail_lines[row] if row < len(detail_lines) else ""
                    try:
                        win.addstr(detail_top + row, 2, self._trim_tail(line, modal_width - 4), self._attr("muted"))
                    except curses.error:
                        pass
                win.refresh()

                preview_top = top + preview_box_top + 2
                preview_left = left + 4
                preview_rows = max(1, preview_box_height - 2)
                preview_cols = max(1, preview_inner_width)
                self._clear_terminal_region(
                    top_row=preview_top,
                    left_col=preview_left,
                    cols=preview_cols,
                    rows=preview_rows,
                )
                drew_preview = self._draw_shot_review_graphics_preview(
                    image_path=preview_image_path,
                    top_row=preview_top,
                    left_col=preview_left,
                    cols=preview_cols,
                    rows=preview_rows,
                )
                if not drew_preview:
                    self._clear_shot_review_graphics_preview()
                    self._draw_shot_review_color_preview(
                        image_path=preview_image_path,
                        top_row=preview_top,
                        left_col=preview_left,
                        cols=preview_cols,
                        rows=preview_rows,
                    )
                key = win.getch()
                if key in (10, 13, curses.KEY_ENTER, 27):
                    self._clear_shot_review_graphics_preview()
                    return

    def _select_thumbnail_studio_preset(self, current_value: str) -> str | None:
        options = list(THUMBNAIL_STUDIO_PRESET_CHOICES)
        option_details = {option: preset_label(option) for option in options}
        selected = self._select_from_list(
            label="Thumbnail preset",
            options=options,
            current_value=current_value if current_value in options else THUMBNAIL_STUDIO_DEFAULT_PRESET,
            option_details=option_details,
        )
        return cast(str | None, selected)

    def _select_thumbnail_studio_palette(self, current_value: str) -> str | None:
        options = list(THUMBNAIL_STUDIO_PALETTE_CHOICES)
        option_details = {option: palette_label(option) for option in options}
        selected = self._select_from_list(
            label="Thumbnail palette",
            options=options,
            current_value=current_value if current_value in options else THUMBNAIL_STUDIO_DEFAULT_PALETTE,
            option_details=option_details,
        )
        return cast(str | None, selected)

    def _edit_standalone_thumbnail_session(self, session: ThumbnailStudioSession) -> None:
        changed = False
        while True:
            preview_path = thumbnail_studio_session_preview_path(session)
            selected_variant = selected_thumbnail_variant(session)
            actions: list[str] = []
            option_details: dict[str, str] = {}
            if session.variants:
                actions.append("Preview current variant")
                option_details["Preview current variant"] = "Open the selected composited thumbnail in a larger preview modal."
                actions.append("Switch selected variant")
                option_details["Switch selected variant"] = (
                    f"Current: {selected_variant.variant_id if selected_variant is not None else '(none)'}"
                )
            actions.extend(
                [
                    "Edit headline",
                    "Edit badge",
                    "Apply preset",
                    "Edit palette",
                    "Generate 4 variants" if not session.variants else "Regenerate 4 more variants",
                    "Export to Downloads",
                    "Review session details",
                    "Save and return",
                ]
            )
            option_details["Edit headline"] = f"Current: {session.headline_text or '(empty)'}"
            option_details["Edit badge"] = f"Current: {session.badge_text or '(none)'}"
            option_details["Apply preset"] = f"Current: {preset_label(session.preset)}"
            option_details["Edit palette"] = f"Current: {palette_label(session.palette)}"
            generate_label = "Generate 4 variants" if not session.variants else "Regenerate 4 more variants"
            option_details[generate_label] = "Create four fresh prompt-based thumbnail variants with different seeds/presets."
            option_details["Export to Downloads"] = (
                f"Current folder: {self._thumbnail_studio_export_dir()}"
            )
            option_details["Review session details"] = "Inspect prompt, preset, palette, variant count, and export path."
            option_details["Save and return"] = "Save this thumbnail session and close the standalone studio."

            action = self._select_thumbnail_studio_action(
                label=f"Thumbnail Studio ({session.session_id})",
                actions=actions,
                current_value=actions[0],
                option_details=option_details,
                preview_path=preview_path,
                preview_title=f"Session: {session.session_id}",
                preview_details=self._thumbnail_studio_preview_detail_lines(session),
            )
            if action is None or action == "Save and return":
                save_thumbnail_studio_session(session)
                self._set_status("Thumbnail Studio closed.")
                return

            if action == "Preview current variant":
                self._show_standalone_thumbnail_preview_modal(session)
                self._set_status("Thumbnail preview closed.")
                continue

            if action == "Switch selected variant":
                if self._select_thumbnail_studio_variant(session):
                    changed = True
                continue

            if action == "Edit headline":
                updated = self._prompt_input("Thumbnail headline", session.headline_text or "")
                if updated is None:
                    continue
                session.headline_text = str(updated).strip()
                try:
                    rerender_thumbnail_studio_session(session, notify=self._append_log)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Could not rerender thumbnail session: {exc}")
                    self._set_status("Thumbnail rerender failed. See logs.")
                    continue
                changed = True
                continue

            if action == "Edit badge":
                updated = self._prompt_input("Thumbnail badge (CLEAR to remove)", session.badge_text or "")
                if updated is None:
                    continue
                badge_value = str(updated).strip()
                session.badge_text = "" if badge_value.upper() == "CLEAR" else badge_value
                try:
                    rerender_thumbnail_studio_session(session, notify=self._append_log)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Could not rerender thumbnail session: {exc}")
                    self._set_status("Thumbnail rerender failed. See logs.")
                    continue
                changed = True
                continue

            if action == "Apply preset":
                selected_preset = self._select_thumbnail_studio_preset(session.preset)
                if selected_preset is None:
                    continue
                session.preset = selected_preset
                selected_variant = selected_thumbnail_variant(session)
                if selected_variant is not None:
                    selected_variant.preset = selected_preset
                try:
                    rerender_thumbnail_studio_session(session, notify=self._append_log)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Could not rerender thumbnail session: {exc}")
                    self._set_status("Thumbnail rerender failed. See logs.")
                    continue
                changed = True
                continue

            if action == "Edit palette":
                selected_palette = self._select_thumbnail_studio_palette(session.palette)
                if selected_palette is None:
                    continue
                session.palette = selected_palette
                try:
                    rerender_thumbnail_studio_session(session, notify=self._append_log)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Could not rerender thumbnail session: {exc}")
                    self._set_status("Thumbnail rerender failed. See logs.")
                    continue
                changed = True
                continue

            if action in {"Generate 4 variants", "Regenerate 4 more variants"}:
                try:
                    self._generate_thumbnail_studio_batch(session, append=True)
                    changed = True
                    self._set_status("Thumbnail variants generated.")
                except RuntimeError as exc:
                    self._append_log(f"WARN: Thumbnail generation unavailable: {exc}")
                    self._show_thumbnail_studio_setup_help(str(exc))
                continue

            if action == "Export to Downloads":
                try:
                    export_path = self._run_with_spinner_modal(
                        title="Thumbnail Studio",
                        message="Exporting thumbnail",
                        detail_text=session.prompt,
                        task=lambda: export_thumbnail_studio_variant(
                            session,
                            export_dir=self._thumbnail_studio_export_dir(),
                            notify=self._append_log,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Could not export thumbnail: {exc}")
                    self._set_status("Thumbnail export failed. See logs.")
                    continue
                changed = True
                self._set_status(f"Thumbnail exported: {cast(Path, export_path).name}")
                continue

            if action == "Review session details":
                self._show_paginated_text_modal(
                    "Thumbnail Session Details",
                    "\n".join(
                        [
                            f"Session: {session.session_id}",
                            f"Prompt: {session.prompt}",
                            f"Style hint: {session.style_hint or '(none)'}",
                            f"Enhanced prompt: {session.enhanced_prompt or '(none)'}",
                            f"Headline: {session.headline_text or '(empty)'}",
                            f"Badge: {session.badge_text or '(none)'}",
                            f"Preset: {preset_label(session.preset)}",
                            f"Palette: {palette_label(session.palette)}",
                            f"Variants: {len(session.variants)}",
                            f"Selected variant: {session.selected_variant_id or '(none)'}",
                            f"Exported path: {session.exported_path or '(not exported yet)'}",
                            f"ComfyUI URL: {self._thumbnail_studio_comfyui_url()}",
                        ]
                    ),
                )
                continue

            if changed:
                save_thumbnail_studio_session(session)

    def _open_thumbnail_studio_menu(self) -> None:
        if self._is_running():
            self._set_status("Cannot open Thumbnail Studio while a command is running.")
            return

        current_choice = "New from prompt"
        while True:
            choice = self._select_from_list(
                label="Thumbnail Studio",
                options=["New from prompt", "Recent sessions"],
                current_value=current_choice,
                option_details={
                    "New from prompt": "Create a standalone thumbnail session from a prompt and generate modern thumbnail variants.",
                    "Recent sessions": "Reopen a saved thumbnail session from ~/.imagine/thumbnail-studio.",
                },
            )
            if choice is None:
                self._set_status("Thumbnail Studio closed.")
                return
            current_choice = str(choice)
            session: ThumbnailStudioSession | None
            if current_choice == "New from prompt":
                session = self._create_thumbnail_studio_session_flow()
            else:
                session = self._select_recent_thumbnail_studio_session()
            if session is None:
                continue
            self._edit_standalone_thumbnail_session(session)
            return

    def _select_thumbnail_studio_workspace(self) -> Path | None:
        candidates = self._youtube_publish_candidates()
        if not candidates:
            self._show_paginated_text_modal(
                "Thumbnail Studio",
                "No finalized workspace is available yet.\n\nGenerate a video first, then press T to open Thumbnail Studio on that saved project.",
            )
            self._set_status("Thumbnail Studio unavailable: no finalized workspace yet.")
            return None

        option_details: dict[str, str] = {}
        label_to_path: dict[str, Path] = {}
        labels: list[str] = []
        for project_dir in candidates:
            label = project_dir.name
            title = self._project_script_title(project_dir) or project_dir.name
            has_draft = youtube_draft_path(project_dir).exists()
            updated_at = dt.datetime.fromtimestamp(project_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            option_details[label] = (
                f"{title} | updated {updated_at} | {'draft saved' if has_draft else 'no draft yet'}"
            )
            label_to_path[label] = project_dir
            labels.append(label)

        current_value = labels[0]
        if self._active_project_dir is not None and self._active_project_dir.name in label_to_path:
            current_value = self._active_project_dir.name

        selected = self._select_from_list(
            label="Thumbnail Studio Project",
            options=labels,
            current_value=current_value,
            option_details=option_details,
        )
        if selected is None:
            self._set_status("Thumbnail Studio cancelled.")
            return None
        return label_to_path.get(selected)

    def _latest_run_report_path(self) -> Path | None:
        for workspace in self._iter_project_workspaces():
            report_path = workspace / "run_report.json"
            if report_path.exists():
                return report_path
        return None

    def _project_run_report_error(self, project_dir: Path) -> str | None:
        report_path = project_dir.expanduser().resolve() / "run_report.json"
        if not report_path.exists():
            return None
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse run_report for regenerate error: {exc}")
            return None
        if not isinstance(payload, dict):
            return None
        error_text = str(payload.get("error") or "").strip()
        return error_text or None

    def _youtube_publish_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for workspace in self._iter_project_workspaces():
            if (workspace / "output" / "final.mp4").exists():
                candidates.append(workspace)
        return candidates

    def _project_script_title(self, project_dir: Path) -> str | None:
        script_path = project_dir / "script.json"
        if not script_path.exists():
            return None

        try:
            payload = json.loads(script_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse script title for {project_dir.name}: {exc}")
            return None

        if not isinstance(payload, dict):
            return None

        title = str(payload.get("title") or "").strip()
        return title or None

    def _dedupe_text_values(self, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(cleaned)
        return out

    def _detect_project_thumbnail_candidate(self, project_dir: Path) -> Path | None:
        file_candidates = [
            project_dir / "output" / "thumbnail_yt.png",
            project_dir / "thumbnail_yt.png",
            project_dir / "output" / "thumbnail_yt.jpg",
            project_dir / "thumbnail_yt.jpg",
            project_dir / "output" / "thumbnail_yt.jpeg",
            project_dir / "thumbnail_yt.jpeg",
            project_dir / "output" / "thumbnail_yt.webp",
            project_dir / "thumbnail_yt.webp",
        ]
        for candidate in file_candidates:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()

        debug_dir = project_dir / "review" / "debug" / "thumbnail"
        if debug_dir.exists() and debug_dir.is_dir():
            matches = sorted(
                [
                    path
                    for path in debug_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                ],
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if matches:
                return matches[0].resolve()
        return None

    def _select_youtube_publish_workspace(self) -> Path | None:
        candidates = self._youtube_publish_candidates()
        if not candidates:
            self._set_status("No finalized videos found yet. Finish a project first.")
            return None

        option_details: dict[str, str] = {}
        label_to_path: dict[str, Path] = {}
        labels: list[str] = []
        for project_dir in candidates:
            label = project_dir.name
            title = self._project_script_title(project_dir) or project_dir.name
            has_draft = youtube_draft_path(project_dir).exists()
            updated_at = dt.datetime.fromtimestamp(project_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            option_details[label] = (
                f"{title} | updated {updated_at} | {'draft saved' if has_draft else 'no draft yet'}"
            )
            label_to_path[label] = project_dir
            labels.append(label)

        current_value = labels[0]
        if self._active_project_dir is not None and self._active_project_dir.name in label_to_path:
            current_value = self._active_project_dir.name

        selected = self._select_from_list(
            label="YouTube Project",
            options=labels,
            current_value=current_value,
            option_details=option_details,
        )
        if selected is None:
            self._set_status("YouTube publish cancelled.")
            return None
        return label_to_path.get(selected)

    def _save_youtube_draft_safe(self, draft: YouTubePublishDraft) -> Path | None:
        try:
            draft_path = save_youtube_publish_draft(draft)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Could not save YouTube draft for {draft.project_dir}: {exc}")
            self._set_status("YouTube draft save failed. See logs.")
            return None

        self._append_log(f"YouTube draft saved: {draft_path}")
        return draft_path

    def _youtube_upload_progress(self, uploaded: int, total: int) -> None:
        if total <= 0:
            return
        percent = max(0.0, min(100.0, (float(uploaded) / float(total)) * 100.0))
        self._set_status(f"YouTube upload progress: {percent:.1f}%")

    def _open_youtube_publish_menu(self) -> None:
        if self._is_running():
            self._set_status("Cannot open YouTube publish while a command is running.")
            return

        auth_state = self._prepare_youtube_auth_for_publish()
        if auth_state is None:
            return

        project_dir = self._select_youtube_publish_workspace()
        if project_dir is None:
            return

        try:
            draft = self._run_with_spinner_modal(
                title="YouTube Draft",
                message="Preparing publish draft",
                detail_text=project_dir.name,
                task=lambda: ensure_youtube_publish_draft(project_dir, fallback_prompt=self.config.prompt),
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Could not prepare YouTube draft for {project_dir.name}: {exc}")
            self._set_status("YouTube draft setup failed. See logs.")
            return

        self._edit_youtube_publish_draft(project_dir, cast(YouTubePublishDraft, draft), auth_state)

    def _prepare_youtube_auth_for_publish(self) -> YouTubeAuthState | None:
        while True:
            auth_state = detect_youtube_auth_state()
            if not auth_state.linked:
                linked_state = self._link_youtube_auth_flow(force_relink=False)
                if linked_state is None:
                    return None
                return linked_state

            choice = self._select_from_list(
                label="YouTube Account",
                options=[
                    "Use linked account",
                    "Switch linked account/channel",
                    "Disconnect linked account",
                    "Review auth status",
                ],
                current_value="Use linked account",
                option_details={
                    "Use linked account": "Continue with the token that is already linked locally.",
                    "Switch linked account/channel": "Re-run Google sign-in so you can choose a different account or channel.",
                    "Disconnect linked account": "Remove the saved token from this machine.",
                    "Review auth status": "Inspect where the client secrets and token are stored.",
                },
            )
            if choice is None:
                self._set_status("YouTube publish cancelled.")
                return None
            if choice == "Use linked account":
                self._set_status("YouTube auth detected. Preparing publish draft.")
                self._append_log("YouTube auth status: linked.")
                return auth_state
            if choice == "Review auth status":
                self._show_paginated_text_modal("YouTube Auth", auth_review_text(auth_state))
                continue
            if choice == "Disconnect linked account":
                if self._disconnect_youtube_auth_flow():
                    return None
                continue
            if choice == "Switch linked account/channel":
                if not self._prompt_yes_no(
                    "Switch YouTube account",
                    "Disconnect the saved token and choose a different Google account or YouTube channel now?",
                    default_yes=True,
                ):
                    continue
                if not self._disconnect_youtube_auth_flow(confirm=False, show_success_modal=False):
                    continue
                linked_state = self._link_youtube_auth_flow(force_relink=True)
                if linked_state is None:
                    return None
                return linked_state

    def _link_youtube_auth_flow(self, *, force_relink: bool) -> YouTubeAuthState | None:
        prompt_text = "Choose a YouTube account or channel in your browser." if force_relink else "No saved YouTube token was found. Open Google sign-in now?"
        if not force_relink and not self._prompt_yes_no("Link YouTube", prompt_text, default_yes=True):
            self._set_status("YouTube publish cancelled.")
            return None

        auth_cancel = threading.Event()
        try:
            self._run_with_spinner_modal(
                title="YouTube Auth",
                message="Waiting for Google sign-in",
                detail_text="A browser window should open for Google authorization.",
                task=lambda: ensure_youtube_token(
                    force_relink=force_relink,
                    cancel_event=auth_cancel,
                    notify=self._append_log,
                ),
                allow_cancel=True,
                cancel_event=auth_cancel,
            )
        except SpinnerCancelled:
            self._set_status("YouTube auth cancelled.")
            return None
        except Exception as exc:  # noqa: BLE001
            auth_state = detect_youtube_auth_state()
            self._append_log(f"ERROR: YouTube auth failed: {exc}")
            self._show_paginated_text_modal(
                "YouTube Auth",
                auth_review_text(auth_state) + f"\n\nError:\n{exc}",
            )
            self._set_status("YouTube auth failed. See logs.")
            return None

        auth_state = detect_youtube_auth_state()
        if not auth_state.linked:
            self._show_paginated_text_modal("YouTube Auth", auth_review_text(auth_state))
            self._set_status("YouTube auth did not complete.")
            return None

        self._append_log("YouTube auth linked successfully.")
        self._set_status("YouTube auth linked. Preparing publish draft.")
        self._show_paginated_text_modal(
            "YouTube Linked",
            auth_review_text(auth_state)
            + "\n\nYouTube authentication completed successfully. Press Enter or Esc to continue.",
        )
        return auth_state

    def _disconnect_youtube_auth_flow(self, *, confirm: bool = True, show_success_modal: bool = True) -> bool:
        if confirm and not self._prompt_yes_no(
            "Disconnect YouTube",
            "Remove the saved YouTube token from this machine?",
            default_yes=True,
        ):
            self._set_status("YouTube disconnect cancelled.")
            return False

        try:
            result = cast(
                dict[str, Any],
                self._run_with_spinner_modal(
                    title="YouTube Auth",
                    message="Disconnecting linked account",
                    detail_text="Revoking the saved token",
                    task=lambda: disconnect_youtube_auth(notify=self._append_log),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Could not disconnect YouTube auth: {exc}")
            self._show_paginated_text_modal("YouTube Auth", f"Could not disconnect the saved token.\n\n{exc}")
            self._set_status("YouTube disconnect failed. See logs.")
            return False

        lines = [
            "The linked YouTube token was disconnected.",
            "",
            f"Local token removed: {'yes' if result.get('removed_local_token') else 'no'}",
            f"Remote token revoked: {'yes' if result.get('revoked_remote_token') else 'no'}",
            f"Token path: {result.get('token_path')}",
        ]
        warning = str(result.get("warning") or "").strip()
        if warning:
            lines.extend(["", f"Warning: {warning}"])
        if show_success_modal:
            self._show_paginated_text_modal("YouTube Disconnected", "\n".join(lines))
        self._set_status("YouTube account disconnected.")
        return True

    def _edit_youtube_publish_draft(
        self,
        project_dir: Path,
        draft: YouTubePublishDraft,
        auth_state: YouTubeAuthState,
    ) -> None:
        with self._modal_focus():
            while True:
                option_details = {
                    "Review draft": "Open the current publish summary before uploading.",
                    "Review auth status": f"Linked: {'yes' if auth_state.linked else 'no'}.",
                    "Edit title": self._trim_tail(draft.title or "(empty)", 80),
                    "Edit description": self._trim_tail(draft.description.splitlines()[0] if draft.description else "(empty)", 80),
                    "Edit visibility": f"Current: {draft.visibility}",
                    "Edit schedule": f"Current: {draft.schedule_at or 'publish immediately'}",
                    "Edit tags": f"Current: {', '.join(draft.tags) if draft.tags else '(none)'}",
                    "Upload YouTube captions": (
                        f"{'On' if draft.upload_captions else 'Off'}"
                        + (
                            " | Requires youtube.force-ssl scope to complete"
                            if draft.upload_captions
                            else ""
                        )
                    ),
                    "Edit thumbnail": (
                        f"{draft.thumbnail_source}: "
                        f"{Path(draft.thumbnail_path).name if draft.thumbnail_path else 'no thumbnail selected'}"
                    ),
                    "Publish now": "Upload the current draft to the linked YouTube account.",
                    "Save draft": "Write the current draft to the project publish folder.",
                }
                actions = [
                    "Review draft",
                    "Review auth status",
                    "Edit title",
                    "Edit description",
                    "Edit visibility",
                    "Edit schedule",
                    "Edit tags",
                    "Upload YouTube captions",
                    "Edit thumbnail",
                    "Publish now",
                    "Save draft",
                ]
                choice = self._select_from_list(
                    label=f"YouTube Draft ({project_dir.name})",
                    options=actions,
                    current_value="Review draft",
                    option_details=option_details,
                )
                if choice is None:
                    draft_path = self._save_youtube_draft_safe(draft)
                    if draft_path is not None:
                        self._set_status(f"YouTube draft saved: {draft_path.name}")
                    return

                changed = False
                if choice == "Review draft":
                    self._show_paginated_text_modal(
                        "YouTube Draft",
                        draft_review_text(draft)
                        + "\n\nUpload status: OAuth/upload wiring still needs to be connected in a later step.",
                    )
                    continue

                if choice == "Review auth status":
                    self._show_paginated_text_modal("YouTube Auth", auth_review_text(auth_state))
                    continue

                if choice == "Edit title":
                    changed = self._edit_youtube_draft_title(draft)
                elif choice == "Edit description":
                    updated = self._prompt_multiline_input("YouTube description", draft.description)
                    if updated is not None:
                        draft.description = updated.strip()
                        changed = True
                elif choice == "Edit visibility":
                    selected = self._select_from_list(
                        label="YouTube visibility",
                        options=list(YOUTUBE_VISIBILITY_CHOICES),
                        current_value=draft.visibility,
                    )
                    if selected is not None:
                        draft.visibility = selected
                        changed = True
                elif choice == "Edit schedule":
                    changed = self._edit_youtube_draft_schedule(draft)
                elif choice == "Edit tags":
                    changed = self._edit_youtube_draft_tags(draft)
                elif choice == "Upload YouTube captions":
                    draft.upload_captions = not draft.upload_captions
                    changed = True
                elif choice == "Edit thumbnail":
                    changed = self._edit_youtube_thumbnail(project_dir, draft)
                elif choice == "Publish now":
                    draft_path = self._save_youtube_draft_safe(draft)
                    if draft_path is None:
                        return
                    self._publish_youtube_draft_from_tui(project_dir, draft)
                    return
                elif choice == "Save draft":
                    draft_path = self._save_youtube_draft_safe(draft)
                    if draft_path is not None:
                        self._set_status(f"YouTube draft saved: {draft_path.name}")
                    return

                if changed:
                    draft_path = self._save_youtube_draft_safe(draft)
                    if draft_path is not None:
                        self._set_status(f"YouTube draft updated: {draft_path.name}")

    def _publish_youtube_draft_from_tui(self, project_dir: Path, draft: YouTubePublishDraft) -> None:
        if not self._prompt_yes_no(
            "Publish to YouTube",
            f"Upload {Path(draft.video_path).name} to your linked YouTube account now?",
            default_yes=True,
        ):
            self._set_status("YouTube publish cancelled.")
            return

        upload_cancel = threading.Event()
        try:
            report = self._run_with_spinner_modal(
                title="YouTube Upload",
                message="Uploading video",
                detail_text=Path(draft.video_path).name,
                task=lambda: publish_youtube_draft(
                    draft,
                    notify=self._append_log,
                    progress=self._youtube_upload_progress,
                    cancel_event=upload_cancel,
                ),
                allow_cancel=True,
                cancel_event=upload_cancel,
            )
        except SpinnerCancelled:
            self._set_status("YouTube upload cancelled.")
            return
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: YouTube publish failed: {exc}")
            self._show_paginated_text_modal("YouTube Upload Failed", f"Upload failed.\n\n{exc}")
            self._set_status("YouTube publish failed. See logs.")
            return

        report_payload = cast(dict[str, Any], report)
        video_url = str(report_payload.get("video_url") or "").strip()
        video_id = str(report_payload.get("video_id") or "").strip()
        report_path = str(report_payload.get("report_path") or "").strip()
        body = "\n".join(
            [
                "YouTube upload completed successfully.",
                "",
                f"Video ID: {video_id or '(unknown)'}",
                f"Video URL: {video_url or '(not provided)'}",
                f"Report: {report_path or '(not provided)'}",
                f"Visibility: {report_payload.get('visibility') or draft.visibility}",
                f"Thumbnail uploaded: {'yes' if report_payload.get('thumbnail_uploaded') else 'no'}",
                f"Captions uploaded: {'yes' if report_payload.get('captions_uploaded') else 'no'}",
            ]
        )
        if report_payload.get("publish_at"):
            body += f"\nPublish at: {report_payload.get('publish_at')}"
        warnings = report_payload.get("warnings")
        if isinstance(warnings, list) and warnings:
            body += "\n\nWarnings:"
            for warning in warnings:
                body += f"\n- {warning}"

        self._show_paginated_text_modal("YouTube Uploaded", body)
        self._set_status("YouTube upload completed successfully.")

    def _edit_youtube_draft_title(self, draft: YouTubePublishDraft) -> bool:
        previous_title = str(draft.title or "").strip()
        previous_thumbnail_text = str(draft.thumbnail_text or "").strip()
        previous_thumbnail_prompt = str(draft.thumbnail_prompt or "").strip()
        suggestions = self._dedupe_text_values([draft.title, *draft.title_suggestions])
        if suggestions:
            suggestion_options = [*suggestions, "Edit current title"]
            selected = self._select_from_list(
                label="Title suggestion",
                options=suggestion_options,
                current_value=draft.title if draft.title in suggestion_options else suggestion_options[0],
            )
            if selected is None:
                return False
            if selected != "Edit current title":
                draft.title = selected

        updated = self._prompt_input("YouTube title", draft.title)
        if updated is not None:
            draft.title = updated.strip()

        if not draft.title:
            return False

        draft.title_suggestions = self._dedupe_text_values([draft.title, *draft.title_suggestions])
        if not draft.thumbnail_prompt:
            draft.thumbnail_prompt = draft.title
        if previous_thumbnail_text in {"", previous_title, previous_thumbnail_prompt}:
            suggestions = self._youtube_thumbnail_text_suggestions_for_draft(draft)
            draft.thumbnail_text = suggestions[0] if suggestions else draft.title
            draft.thumbnail_prompt = draft.title
        return True

    def _edit_youtube_draft_schedule(self, draft: YouTubePublishDraft) -> bool:
        current_mode = "Schedule for later" if draft.schedule_at else "Publish immediately"
        selected = self._select_from_list(
            label="Publish schedule",
            options=["Publish immediately", "Schedule for later"],
            current_value=current_mode,
        )
        if selected is None:
            return False

        if selected == "Publish immediately":
            draft.schedule_at = None
            return True

        default_time = draft.schedule_at or (
            dt.datetime.now().astimezone() + dt.timedelta(days=1)
        ).strftime("%Y-%m-%d %H:%M")
        updated = self._prompt_input("Schedule time (YYYY-MM-DD HH:MM)", default_time)
        if updated is None:
            return False

        normalized = str(updated).strip().replace("T", " ")
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            self._set_status("Invalid schedule time. Use YYYY-MM-DD HH:MM or ISO format.")
            return False

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
        if parsed < dt.datetime.now().astimezone():
            self._set_status("Scheduled time must be in the future.")
            return False

        draft.schedule_at = parsed.isoformat(timespec="minutes")
        return True

    def _edit_youtube_draft_tags(self, draft: YouTubePublishDraft) -> bool:
        current_value = ", ".join(draft.tags)
        updated = self._prompt_input("YouTube tags (comma-separated, CLEAR to empty)", current_value)
        if updated is None:
            return False

        cleaned = str(updated).strip()
        if cleaned.upper() == "CLEAR":
            draft.tags = []
            return True

        parsed = [part.strip() for part in re.split(r"[,;\n]+", cleaned) if part.strip()]
        draft.tags = self._dedupe_text_values(parsed)[:12]
        return True

    def _youtube_thumbnail_text_suggestions_for_draft(self, draft: YouTubePublishDraft) -> list[str]:
        project_dir = Path(draft.project_dir or self._active_project_dir or self.config.project_dir).expanduser().resolve()
        suggestions = youtube_thumbnail_text_suggestions(
            project_dir,
            title=draft.title,
            fallback_prompt=draft.thumbnail_prompt or self.config.prompt,
            preferred_text=draft.thumbnail_text,
        )
        return self._dedupe_text_values(suggestions)

    def _ensure_youtube_thumbnail_draft_defaults(self, draft: YouTubePublishDraft) -> None:
        suggestions = self._youtube_thumbnail_text_suggestions_for_draft(draft)
        default_text = str(draft.thumbnail_text or (suggestions[0] if suggestions else "") or draft.thumbnail_prompt or draft.title or self.config.prompt or "Why This Matters").strip()
        draft.thumbnail_text = default_text
        draft.thumbnail_badge_text = str(getattr(draft, "thumbnail_badge_text", "") or "").strip()
        if not draft.thumbnail_prompt:
            draft.thumbnail_prompt = draft.title or default_text
        if not draft.thumbnail_font_color:
            draft.thumbnail_font_color = YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR
        if not draft.thumbnail_outline_color:
            draft.thumbnail_outline_color = YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR
        if not draft.thumbnail_font_size_mode:
            draft.thumbnail_font_size_mode = YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE
        if not draft.thumbnail_anchor:
            draft.thumbnail_anchor = YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR
        if not draft.thumbnail_text_align:
            draft.thumbnail_text_align = YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN
        if not getattr(draft, "thumbnail_text_plate", ""):
            draft.thumbnail_text_plate = YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE

    def _youtube_thumbnail_is_generated(self, draft: YouTubePublishDraft) -> bool:
        return draft.thumbnail_source in {"auto", "generated"}

    def _youtube_thumbnail_font_size_label(self, value: str) -> str:
        return self.YOUTUBE_THUMBNAIL_FONT_SIZE_LABELS.get(value, value.replace("-", " ").title())

    def _youtube_thumbnail_anchor_label(self, value: str) -> str:
        return self.YOUTUBE_THUMBNAIL_ANCHOR_LABELS.get(value, value.replace("-", " ").title())

    def _youtube_thumbnail_align_label(self, value: str) -> str:
        return self.YOUTUBE_THUMBNAIL_ALIGN_LABELS.get(value, value.replace("-", " ").title())

    def _youtube_thumbnail_color_label(self, value: str) -> str:
        return value.replace("-", " ").title()

    def _youtube_thumbnail_text_plate_label(self, value: str) -> str:
        return self.YOUTUBE_THUMBNAIL_TEXT_PLATE_LABELS.get(value, value.replace("-", " ").title())

    def _reset_youtube_thumbnail_style(self, draft: YouTubePublishDraft) -> None:
        draft.thumbnail_font_color = YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR
        draft.thumbnail_outline_color = YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR
        draft.thumbnail_font_size_mode = YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE
        draft.thumbnail_anchor = YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR
        draft.thumbnail_offset_x = 0
        draft.thumbnail_offset_y = 0
        draft.thumbnail_text_align = YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN
        draft.thumbnail_text_plate = YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE

    def _render_thumbnail_studio_image(
        self,
        project_dir: Path,
        draft: YouTubePublishDraft,
    ) -> Path | None:
        self._ensure_youtube_thumbnail_draft_defaults(draft)
        try:
            rendered = self._run_with_spinner_modal(
                title="Thumbnail Studio",
                message="Rendering thumbnail",
                detail_text=draft.thumbnail_text,
                task=lambda: render_youtube_thumbnail_for_draft(draft, notify=self._append_log),
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: YouTube thumbnail rendering failed: {exc}")
            self._set_status("YouTube thumbnail rendering failed. See logs.")
            return None
        if rendered is None or not cast(Path, rendered).exists():
            self._set_status("YouTube thumbnail rendering failed. See logs.")
            return None
        rendered_path = cast(Path, rendered)
        draft.thumbnail_path = str(rendered_path.resolve())
        draft.thumbnail_source = "generated"
        return rendered_path.resolve()

    def _youtube_thumbnail_diagnostic_lines(self, draft: YouTubePublishDraft) -> list[str]:
        diagnostics = youtube_thumbnail_diagnostics(
            draft.thumbnail_text,
            font_color=draft.thumbnail_font_color,
            text_plate=getattr(draft, "thumbnail_text_plate", YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE),
        )
        lines = [
            f"CTR check: {str(diagnostics.get('rating') or 'fair').title()}",
            f"Words: {int(diagnostics.get('word_count') or 0)} | Wrapped lines: {int(diagnostics.get('line_count') or 0)}",
        ]
        badge_text = str(getattr(draft, "thumbnail_badge_text", "") or "").strip()
        if badge_text:
            lines.append(f"Badge: {badge_text}")
        for note in diagnostics.get("notes") or []:
            lines.append(f"Note: {note}")
        for warning in diagnostics.get("warnings") or []:
            lines.append(f"Warn: {warning}")
        return lines

    def _show_youtube_thumbnail_preview_modal(
        self,
        *,
        image_path: Path,
        draft: YouTubePublishDraft,
    ) -> None:
        if self._stdscr is None:
            self._play_media_path(
                image_path,
                label=f"youtube-thumbnail-{self._safe_filename_token(image_path.stem)}",
                audio_only=False,
            )
            return

        preview_image_path = self._preview_png_from_media_path(image_path, media_type="image") or image_path
        detail_lines = [
            f"Text: {draft.thumbnail_text}",
            f"Badge: {str(getattr(draft, 'thumbnail_badge_text', '') or '').strip() or '(none)'}",
            f"Font: {self._youtube_thumbnail_font_size_label(draft.thumbnail_font_size_mode)} | "
            f"{self._youtube_thumbnail_color_label(draft.thumbnail_font_color)} on "
            f"{self._youtube_thumbnail_text_plate_label(getattr(draft, 'thumbnail_text_plate', YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE))}",
            (
                f"Layout: {self._youtube_thumbnail_anchor_label(draft.thumbnail_anchor)} | "
                f"{self._youtube_thumbnail_align_label(draft.thumbnail_text_align)} | "
                f"X {int(draft.thumbnail_offset_x):+d}px | Y {int(draft.thumbnail_offset_y):+d}px"
            ),
            *self._youtube_thumbnail_diagnostic_lines(draft),
        ]

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            modal_width = min(max(92, width - 4), max(30, width - 2))
            modal_height = min(max(22, height - 4), max(14, height - 2))
            if modal_width < 30 or modal_height < 14:
                self._show_paginated_text_modal("Thumbnail Preview", "\n".join(detail_lines))
                return

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            preview_box_top = 2
            preview_box_height = max(10, min(16, modal_height - 9))
            preview_inner_width = max(12, modal_width - 8)
            detail_top = preview_box_top + preview_box_height + 1
            detail_rows = max(3, modal_height - detail_top - 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass
                try:
                    win.addstr(0, 2, self._trim_tail(" Thumbnail Preview ", modal_width - 4), self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail("Enter/Esc back", modal_width - 4), self._attr("muted"))
                    win.addstr(preview_box_top, 2, "+" + ("-" * preview_inner_width) + "+", self._attr("muted"))
                    win.addstr(
                        preview_box_top + preview_box_height - 1,
                        2,
                        "+" + ("-" * preview_inner_width) + "+",
                        self._attr("muted"),
                    )
                except curses.error:
                    pass

                for row in range(max(0, preview_box_height - 2)):
                    try:
                        win.addstr(
                            preview_box_top + 1 + row,
                            2,
                            "|" + (" " * preview_inner_width) + "|",
                        )
                    except curses.error:
                        pass

                for row in range(detail_rows):
                    line = detail_lines[row] if row < len(detail_lines) else ""
                    try:
                        win.addstr(detail_top + row, 2, self._trim_tail(line, modal_width - 4), self._attr("muted"))
                    except curses.error:
                        pass

                win.refresh()
                preview_top = top + preview_box_top + 2
                preview_left = left + 4
                preview_rows = max(1, preview_box_height - 2)
                preview_cols = max(1, preview_inner_width)
                self._clear_terminal_region(
                    top_row=preview_top,
                    left_col=preview_left,
                    cols=preview_cols,
                    rows=preview_rows,
                )
                drew_preview = self._draw_shot_review_graphics_preview(
                    image_path=preview_image_path,
                    top_row=preview_top,
                    left_col=preview_left,
                    cols=preview_cols,
                    rows=preview_rows,
                )
                if not drew_preview:
                    self._clear_shot_review_graphics_preview()
                    self._draw_shot_review_color_preview(
                        image_path=preview_image_path,
                        top_row=preview_top,
                        left_col=preview_left,
                        cols=preview_cols,
                        rows=preview_rows,
                    )
                key = win.getch()
                if key in (10, 13, curses.KEY_ENTER, 27):
                    self._clear_shot_review_graphics_preview()
                    return

    def _youtube_thumbnail_preview_detail_lines(self, draft: YouTubePublishDraft, *, current_path: Path | None) -> list[str]:
        lines = [
            f"File: {current_path.name if current_path is not None else '(none selected)'}",
            f"Text: {draft.thumbnail_text}",
        ]
        badge_text = str(getattr(draft, "thumbnail_badge_text", "") or "").strip()
        if badge_text:
            lines.append(f"Badge: {badge_text}")
        lines.extend(
            [
                f"Font: {self._youtube_thumbnail_font_size_label(draft.thumbnail_font_size_mode)} | {self._youtube_thumbnail_color_label(draft.thumbnail_font_color)}",
                (
                    f"Layout: {self._youtube_thumbnail_anchor_label(draft.thumbnail_anchor)} | "
                    f"{self._youtube_thumbnail_align_label(draft.thumbnail_text_align)} | "
                    f"{self._youtube_thumbnail_text_plate_label(getattr(draft, 'thumbnail_text_plate', YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE))}"
                ),
                *self._youtube_thumbnail_diagnostic_lines(draft),
            ]
        )
        return lines

    def _select_thumbnail_studio_action(
        self,
        *,
        label: str,
        actions: list[str],
        current_value: str,
        option_details: dict[str, str],
        preview_path: Path | None,
        preview_title: str,
        preview_details: list[str],
    ) -> str | None:
        if self._stdscr is None or preview_path is None:
            return self._select_from_list(
                label=label,
                options=actions,
                current_value=current_value,
                option_details=option_details,
            )

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            if width < 96 or height < 22:
                return self._select_from_list(
                    label=label,
                    options=actions,
                    current_value=current_value,
                    option_details=option_details,
                )

            normalized = [str(option).strip() for option in actions if str(option).strip()]
            selected = normalized.index(current_value) if current_value in normalized else 0
            max_option_len = max(len(item) for item in normalized)
            modal_width = min(max(108, max_option_len + 44), max(24, width - 2))
            modal_height = min(max(22, height - 2), max(18, height - 2))
            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)

            list_width = min(max(34, max_option_len + 4), max(34, modal_width // 3))
            preview_width = max(28, modal_width - list_width - 5)
            interior_height = modal_height - 2
            preview_box_top = 2
            preview_box_height = max(10, min(16, interior_height - 8))
            detail_top = preview_box_top + preview_box_height + 1
            detail_rows = max(4, interior_height - preview_box_height - 3)
            list_rows = max(1, interior_height)

            preview_image_path = self._preview_png_from_media_path(preview_path, media_type="image") or preview_path
            start_index = max(0, selected - list_rows + 1)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            while True:
                current_label = normalized[selected]
                current_detail = option_details.get(current_label, "")
                detail_lines = self._wrap_shot_review_detail_text(
                    "\n".join([*preview_details, "", f"Action: {current_label}", current_detail]),
                    width=max(18, preview_width - 2),
                    limit=detail_rows,
                )
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {label} ", max(1, modal_width - 4))
                help_text = "Enter choose | Up/Down move | Esc back"
                divider_x = list_width + 2
                preview_inner_width = max(4, preview_width - 2)
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                    for row in range(1, modal_height - 1):
                        win.addch(row, divider_x, ord("|"), self._attr("muted"))
                    win.addstr(1, divider_x + 2, self._trim_tail(preview_title, preview_width), self._attr("muted", bold=True))
                    win.addstr(preview_box_top, divider_x + 2, "+" + ("-" * preview_inner_width) + "+", self._attr("muted"))
                    win.addstr(
                        preview_box_top + preview_box_height - 1,
                        divider_x + 2,
                        "+" + ("-" * preview_inner_width) + "+",
                        self._attr("muted"),
                    )
                except curses.error:
                    pass

                if selected < start_index:
                    start_index = selected
                elif selected >= start_index + list_rows:
                    start_index = selected - list_rows + 1

                for row in range(list_rows):
                    option_index = start_index + row
                    if option_index >= len(normalized):
                        break
                    item = normalized[option_index]
                    prefix = ">" if option_index == selected else " "
                    line = self._trim_tail(f"{prefix} {item}", list_width)
                    attr = curses.A_REVERSE if option_index == selected else 0
                    try:
                        win.addstr(1 + row, 1, line.ljust(list_width), attr)
                    except curses.error:
                        pass

                for row in range(max(0, preview_box_height - 2)):
                    try:
                        win.addstr(
                            preview_box_top + 1 + row,
                            divider_x + 2,
                            "|" + (" " * preview_inner_width) + "|",
                        )
                    except curses.error:
                        pass

                for row in range(detail_rows):
                    line = detail_lines[row] if row < len(detail_lines) else ""
                    try:
                        win.addstr(detail_top + row, divider_x + 2, self._trim_tail(line, preview_width), self._attr("muted"))
                    except curses.error:
                        pass

                win.refresh()
                preview_top = top + preview_box_top + 2
                preview_left = left + divider_x + 4
                preview_rows = max(1, preview_box_height - 2)
                preview_cols = max(1, preview_inner_width)
                self._clear_terminal_region(
                    top_row=preview_top,
                    left_col=preview_left,
                    cols=preview_cols,
                    rows=preview_rows,
                )
                drew_preview = self._draw_shot_review_graphics_preview(
                    image_path=preview_image_path,
                    top_row=preview_top,
                    left_col=preview_left,
                    cols=preview_cols,
                    rows=preview_rows,
                )
                if not drew_preview:
                    self._clear_shot_review_graphics_preview()
                    self._draw_shot_review_color_preview(
                        image_path=preview_image_path,
                        top_row=preview_top,
                        left_col=preview_left,
                        cols=preview_cols,
                        rows=preview_rows,
                    )

                key = win.getch()
                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    selected = (selected - 1) % len(normalized)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    selected = (selected + 1) % len(normalized)
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    self._clear_shot_review_graphics_preview()
                    return normalized[selected]
                if key in (27,):
                    self._clear_shot_review_graphics_preview()
                    return None

    def _select_youtube_thumbnail_text(self, draft: YouTubePublishDraft) -> str | None:
        suggestion_options = self._youtube_thumbnail_text_suggestions_for_draft(draft)
        current_text = str(draft.thumbnail_text or "").strip()
        options = self._dedupe_text_values([current_text, *suggestion_options, "Write custom text"])
        option_details: dict[str, str] = {}
        for suggestion in suggestion_options:
            option_details[suggestion] = "Short hook suggestion tuned for mobile readability."
        if current_text:
            option_details[current_text] = "Current thumbnail text."
        option_details["Write custom text"] = "Type your own custom headline."
        selected = self._select_from_list(
            label="Thumbnail text",
            options=options,
            current_value=current_text if current_text in options else options[0],
            option_details=option_details,
        )
        if selected is None:
            return None
        if selected == "Write custom text":
            updated = self._prompt_input("Thumbnail text", current_text)
            if updated is None:
                return None
            selected = updated.strip()
        return str(selected).strip() or None

    def _select_youtube_thumbnail_badge_text(self, draft: YouTubePublishDraft) -> str | None:
        current_text = str(getattr(draft, "thumbnail_badge_text", "") or "").strip()
        updated = self._prompt_input(
            "Thumbnail badge text (CLEAR to remove)",
            current_text or "e.g. DAY 97 / $599",
        )
        if updated is None:
            return None
        cleaned = str(updated).strip()
        if cleaned.upper() == "CLEAR":
            return ""
        return cleaned[:24].strip()

    def _select_youtube_thumbnail_text_plate(self, current_value: str) -> str | None:
        option_labels = [self._youtube_thumbnail_text_plate_label(choice) for choice in YOUTUBE_THUMBNAIL_TEXT_PLATE_CHOICES]
        label_to_value = {
            self._youtube_thumbnail_text_plate_label(choice): choice
            for choice in YOUTUBE_THUMBNAIL_TEXT_PLATE_CHOICES
        }
        option_details = {
            self._youtube_thumbnail_text_plate_label(choice): self.YOUTUBE_THUMBNAIL_TEXT_PLATE_DETAILS.get(choice, "")
            for choice in YOUTUBE_THUMBNAIL_TEXT_PLATE_CHOICES
        }
        selected = self._select_from_list(
            label="Thumbnail text plate",
            options=option_labels,
            current_value=self._youtube_thumbnail_text_plate_label(current_value),
            option_details=option_details,
        )
        if selected is None:
            return None
        return label_to_value.get(selected)

    def _apply_youtube_thumbnail_style_preset(self, draft: YouTubePublishDraft, preset_key: str) -> bool:
        preset = next(
            (item for item in self.YOUTUBE_THUMBNAIL_STYLE_PRESETS if item.get("key") == preset_key),
            None,
        )
        if preset is None:
            return False
        draft.thumbnail_font_color = str(preset.get("font_color") or YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR)
        draft.thumbnail_outline_color = str(preset.get("outline_color") or YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR)
        draft.thumbnail_font_size_mode = str(preset.get("font_size_mode") or YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE)
        draft.thumbnail_anchor = str(preset.get("anchor") or YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR)
        draft.thumbnail_text_align = str(preset.get("text_align") or YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN)
        draft.thumbnail_text_plate = str(preset.get("text_plate") or YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE)
        draft.thumbnail_offset_x = 0
        draft.thumbnail_offset_y = 0
        return True

    def _select_youtube_thumbnail_style_preset(self) -> str | None:
        options = [str(item["label"]) for item in self.YOUTUBE_THUMBNAIL_STYLE_PRESETS]
        label_to_key = {str(item["label"]): str(item["key"]) for item in self.YOUTUBE_THUMBNAIL_STYLE_PRESETS}
        option_details = {str(item["label"]): str(item["detail"]) for item in self.YOUTUBE_THUMBNAIL_STYLE_PRESETS}
        selected = self._select_from_list(
            label="Thumbnail style preset",
            options=options,
            current_value=options[0],
            option_details=option_details,
        )
        if selected is None:
            return None
        return label_to_key.get(selected)

    def _open_thumbnail_in_external_editor(
        self,
        *,
        project_dir: Path,
        draft: YouTubePublishDraft,
        current_path: Path | None,
    ) -> bool:
        if not str(self.config.external_editor_command).strip():
            self._set_status("External editor is not configured in Settings.")
            return False
        source_path = current_path if current_path is not None and current_path.exists() else None
        work_path = export_youtube_thumbnail_work_image(
            draft,
            source_path=source_path,
            notify=self._append_log,
        )
        if work_path is None or not work_path.exists():
            self._set_status("Could not prepare thumbnail_work.png for external editing.")
            return False
        try:
            prior_mtime = int(work_path.stat().st_mtime_ns)
        except OSError:
            prior_mtime = 0
        if not self._open_path_in_external_editor(work_path):
            return False
        try:
            updated_mtime = int(work_path.stat().st_mtime_ns)
        except OSError:
            updated_mtime = prior_mtime
        if updated_mtime == prior_mtime:
            self._set_status("External editor closed with no thumbnail_work.png change detected.")
            return False
        if not self._prompt_yes_no(
            "Use edited thumbnail",
            f"Use {work_path.name} as the current thumbnail for {project_dir.name}?",
            default_yes=True,
        ):
            self._set_status("Edited thumbnail was kept on disk but not selected.")
            return False
        draft.thumbnail_path = str(work_path.resolve())
        draft.thumbnail_source = "manual"
        self._set_status(f"Using edited thumbnail: {work_path.name}")
        return True

    def _select_youtube_thumbnail_color(
        self,
        *,
        label: str,
        current_value: str,
        choices: tuple[str, ...],
    ) -> str | None:
        option_labels = [self._youtube_thumbnail_color_label(choice) for choice in choices]
        label_to_value = {self._youtube_thumbnail_color_label(choice): choice for choice in choices}
        option_details = {
            self._youtube_thumbnail_color_label(choice): self.YOUTUBE_THUMBNAIL_COLOR_DETAILS.get(choice, "")
            for choice in choices
        }
        selected = self._select_from_list(
            label=label,
            options=option_labels,
            current_value=self._youtube_thumbnail_color_label(current_value),
            option_details=option_details,
        )
        if selected is None:
            return None
        return label_to_value.get(selected)

    def _select_youtube_thumbnail_font_size(self, current_value: str) -> str | None:
        option_labels = [self._youtube_thumbnail_font_size_label(choice) for choice in YOUTUBE_THUMBNAIL_FONT_SIZE_CHOICES]
        label_to_value = {
            self._youtube_thumbnail_font_size_label(choice): choice
            for choice in YOUTUBE_THUMBNAIL_FONT_SIZE_CHOICES
        }
        selected = self._select_from_list(
            label="Thumbnail font size",
            options=option_labels,
            current_value=self._youtube_thumbnail_font_size_label(current_value),
        )
        if selected is None:
            return None
        return label_to_value.get(selected)

    def _select_youtube_thumbnail_anchor(self, current_value: str) -> str | None:
        option_labels = [self._youtube_thumbnail_anchor_label(choice) for choice in YOUTUBE_THUMBNAIL_ANCHOR_CHOICES]
        label_to_value = {self._youtube_thumbnail_anchor_label(choice): choice for choice in YOUTUBE_THUMBNAIL_ANCHOR_CHOICES}
        selected = self._select_from_list(
            label="Thumbnail anchor",
            options=option_labels,
            current_value=self._youtube_thumbnail_anchor_label(current_value),
        )
        if selected is None:
            return None
        return label_to_value.get(selected)

    def _select_youtube_thumbnail_align(self, current_value: str) -> str | None:
        option_labels = [self._youtube_thumbnail_align_label(choice) for choice in YOUTUBE_THUMBNAIL_TEXT_ALIGN_CHOICES]
        label_to_value = {self._youtube_thumbnail_align_label(choice): choice for choice in YOUTUBE_THUMBNAIL_TEXT_ALIGN_CHOICES}
        selected = self._select_from_list(
            label="Thumbnail text alignment",
            options=option_labels,
            current_value=self._youtube_thumbnail_align_label(current_value),
        )
        if selected is None:
            return None
        return label_to_value.get(selected)

    def _select_youtube_thumbnail_offset(self, *, axis: str, current_value: int) -> int | None:
        values = list(range(-YOUTUBE_THUMBNAIL_MAX_OFFSET, YOUTUBE_THUMBNAIL_MAX_OFFSET + 1, 40))
        if 0 not in values:
            values.append(0)
        if current_value not in values:
            values.append(current_value)
        values = sorted(set(values))
        labels: list[str] = []
        label_to_value: dict[str, int] = {}
        for value in values:
            if value == 0:
                label = "0 (No nudge)"
            elif axis == "horizontal":
                label = f"{value:+d}px ({'Right' if value > 0 else 'Left'})"
            else:
                label = f"{value:+d}px ({'Down' if value > 0 else 'Up'})"
            labels.append(label)
            label_to_value[label] = value
        current_label = next((label for label, value in label_to_value.items() if value == current_value), labels[0])
        selected = self._select_from_list(
            label=f"{axis.title()} nudge",
            options=labels,
            current_value=current_label,
        )
        if selected is None:
            return None
        return label_to_value.get(selected)

    def _edit_youtube_thumbnail_position(self, draft: YouTubePublishDraft) -> bool:
        selected_anchor = self._select_youtube_thumbnail_anchor(draft.thumbnail_anchor)
        if selected_anchor is None:
            return False
        selected_offset_x = self._select_youtube_thumbnail_offset(axis="horizontal", current_value=int(draft.thumbnail_offset_x))
        if selected_offset_x is None:
            return False
        selected_offset_y = self._select_youtube_thumbnail_offset(axis="vertical", current_value=int(draft.thumbnail_offset_y))
        if selected_offset_y is None:
            return False
        draft.thumbnail_anchor = selected_anchor
        draft.thumbnail_offset_x = selected_offset_x
        draft.thumbnail_offset_y = selected_offset_y
        return True

    def _edit_youtube_thumbnail(self, project_dir: Path, draft: YouTubePublishDraft) -> bool:
        self._ensure_youtube_thumbnail_draft_defaults(draft)
        changed = False

        while True:
            current_path = Path(draft.thumbnail_path).expanduser().resolve() if draft.thumbnail_path else None
            if current_path is not None and not current_path.exists():
                current_path = None
            detected_path = self._detect_project_thumbnail_candidate(project_dir)
            generated_mode = self._youtube_thumbnail_is_generated(draft)

            if generated_mode:
                rendered_path = self._render_thumbnail_studio_image(project_dir, draft)
                if rendered_path is not None:
                    current_path = rendered_path

            actions: list[str] = []
            option_details: dict[str, str] = {}
            if current_path is not None:
                actions.append("Preview current thumbnail")
                option_details["Preview current thumbnail"] = "Render the current managed settings if needed, then preview the thumbnail inline."
            if detected_path is not None and (current_path is None or detected_path != current_path):
                actions.append(f"Use detected file ({detected_path.name})")
                option_details[f"Use detected file ({detected_path.name})"] = "Use the thumbnail file already found in this project."
            actions.append("Generate thumbnail")
            option_details["Generate thumbnail"] = "Render the managed project thumbnail using the current text and style settings."
            if generated_mode:
                actions.extend(
                    [
                        "Apply style preset",
                        "Edit text",
                        "Edit badge text",
                        "Edit font color",
                        "Edit outline color",
                        "Edit font size",
                        "Edit text plate",
                        "Edit position",
                        "Edit alignment",
                        "Regenerate background thumbnail",
                        "Reset style to defaults",
                    ]
                )
                option_details["Apply style preset"] = "Apply a curated thumbnail layout tuned for higher-contrast YouTube hooks."
                option_details["Edit text"] = "Pick a short hook suggestion or write custom text."
                option_details["Edit badge text"] = (
                    f"Current: {str(getattr(draft, 'thumbnail_badge_text', '') or '').strip() or '(none)'}"
                )
                option_details["Edit font color"] = f"Current: {self._youtube_thumbnail_color_label(draft.thumbnail_font_color)}"
                option_details["Edit outline color"] = f"Current: {self._youtube_thumbnail_color_label(draft.thumbnail_outline_color)}"
                option_details["Edit font size"] = f"Current: {self._youtube_thumbnail_font_size_label(draft.thumbnail_font_size_mode)}"
                option_details["Edit text plate"] = f"Current: {self._youtube_thumbnail_text_plate_label(getattr(draft, 'thumbnail_text_plate', YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE))}"
                option_details["Edit position"] = (
                    f"{self._youtube_thumbnail_anchor_label(draft.thumbnail_anchor)} | "
                    f"X {int(draft.thumbnail_offset_x):+d}px | Y {int(draft.thumbnail_offset_y):+d}px"
                )
                option_details["Edit alignment"] = f"Current: {self._youtube_thumbnail_align_label(draft.thumbnail_text_align)}"
                option_details["Regenerate background thumbnail"] = "Keep the text styling, but switch to a different background/frame choice."
                option_details["Reset style to defaults"] = "Restore the default generated-thumbnail style while keeping the current text."
            else:
                option_details["Generate thumbnail"] = "Switch back to the managed generated thumbnail. Editing tools are only available there."
            actions.append("Pick local thumbnail file")
            option_details["Pick local thumbnail file"] = "Choose a PNG, JPG, JPEG, or WEBP file to use as-is. Styling tools stay disabled for manual files."
            actions.append("Open in external editor")
            option_details["Open in external editor"] = "Export publish/thumbnail_work.png, open it in your configured editor, and optionally reimport the edited file."
            actions.append("Review thumbnail details")
            option_details["Review thumbnail details"] = "Inspect the current source, style, and positioning values."
            actions.append("Save and return")
            option_details["Save and return"] = "Save the current thumbnail selection/settings and return to the YouTube draft."

            action = self._select_thumbnail_studio_action(
                label=f"Thumbnail Studio ({project_dir.name})",
                actions=actions,
                current_value=actions[0],
                option_details=option_details,
                preview_path=current_path,
                preview_title=f"Current thumbnail: {current_path.name if current_path is not None else 'none'}",
                preview_details=self._youtube_thumbnail_preview_detail_lines(draft, current_path=current_path),
            )
            if action is None:
                return changed

            if action == "Save and return":
                return changed

            if action == "Preview current thumbnail":
                if current_path is None:
                    self._set_status("No thumbnail is selected yet.")
                    continue
                if generated_mode:
                    rendered_path = self._render_thumbnail_studio_image(project_dir, draft)
                    if rendered_path is None:
                        continue
                    current_path = rendered_path
                self._show_youtube_thumbnail_preview_modal(image_path=current_path, draft=draft)
                self._set_status("Thumbnail preview closed. Keep editing or press Esc to return.")
                continue

            if action.startswith("Use detected file ("):
                if detected_path is None:
                    self._set_status("No detected thumbnail file is available.")
                    continue
                draft.thumbnail_path = str(detected_path)
                draft.thumbnail_source = "auto" if detected_path.name == "thumbnail_yt.jpg" else "manual"
                changed = True
                continue

            if action == "Apply style preset":
                selected_preset = self._select_youtube_thumbnail_style_preset()
                if selected_preset is None:
                    continue
                if not self._apply_youtube_thumbnail_style_preset(draft, selected_preset):
                    continue
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Edit text":
                selected_text = self._select_youtube_thumbnail_text(draft)
                if selected_text is None:
                    continue
                draft.thumbnail_text = selected_text
                draft.thumbnail_prompt = draft.thumbnail_text
                rendered_path = self._render_thumbnail_studio_image(project_dir, draft)
                if rendered_path is None:
                    continue
                changed = True
                continue

            if action == "Edit badge text":
                selected_badge = self._select_youtube_thumbnail_badge_text(draft)
                if selected_badge is None:
                    continue
                draft.thumbnail_badge_text = selected_badge
                rendered_path = self._render_thumbnail_studio_image(project_dir, draft)
                if rendered_path is None:
                    continue
                changed = True
                continue

            if action == "Edit font color":
                selected_color = self._select_youtube_thumbnail_color(
                    label="Thumbnail font color",
                    current_value=draft.thumbnail_font_color,
                    choices=YOUTUBE_THUMBNAIL_FONT_COLOR_CHOICES,
                )
                if selected_color is None:
                    continue
                draft.thumbnail_font_color = selected_color
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Edit outline color":
                selected_color = self._select_youtube_thumbnail_color(
                    label="Thumbnail outline color",
                    current_value=draft.thumbnail_outline_color,
                    choices=YOUTUBE_THUMBNAIL_OUTLINE_COLOR_CHOICES,
                )
                if selected_color is None:
                    continue
                draft.thumbnail_outline_color = selected_color
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Edit font size":
                selected_size = self._select_youtube_thumbnail_font_size(draft.thumbnail_font_size_mode)
                if selected_size is None:
                    continue
                draft.thumbnail_font_size_mode = selected_size
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Edit text plate":
                selected_plate = self._select_youtube_thumbnail_text_plate(
                    getattr(draft, "thumbnail_text_plate", YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE)
                )
                if selected_plate is None:
                    continue
                draft.thumbnail_text_plate = selected_plate
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Edit position":
                if not self._edit_youtube_thumbnail_position(draft):
                    continue
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Edit alignment":
                selected_align = self._select_youtube_thumbnail_align(draft.thumbnail_text_align)
                if selected_align is None:
                    continue
                draft.thumbnail_text_align = selected_align
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Reset style to defaults":
                self._reset_youtube_thumbnail_style(draft)
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Regenerate background thumbnail":
                draft.thumbnail_background_variant = max(0, int(draft.thumbnail_background_variant)) + 1
                if self._render_thumbnail_studio_image(project_dir, draft) is None:
                    continue
                changed = True
                continue

            if action == "Pick local thumbnail file":
                default_path = str(current_path or (project_dir / "thumbnail_yt.png"))
                updated = self._prompt_input("Thumbnail file path", default_path)
                if updated is None:
                    continue
                selected_path = Path(str(updated).strip()).expanduser().resolve()
                if not selected_path.exists() or not selected_path.is_file():
                    self._set_status("Thumbnail file was not found.")
                    continue
                if selected_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    self._set_status("Thumbnail file must be PNG, JPG, JPEG, or WEBP.")
                    continue
                draft.thumbnail_path = str(selected_path)
                draft.thumbnail_source = "manual"
                changed = True
                continue

            if action == "Open in external editor":
                if self._open_thumbnail_in_external_editor(project_dir=project_dir, draft=draft, current_path=current_path):
                    changed = True
                continue

            if action == "Review thumbnail details":
                self._show_paginated_text_modal(
                    "Thumbnail Details",
                    "\n".join(
                        [
                            f"Source: {draft.thumbnail_source}",
                            f"Current file: {draft.thumbnail_path or '(none selected)'}",
                            f"Detected file: {str(detected_path) if detected_path is not None else '(none)'}",
                            f"Text: {draft.thumbnail_text}",
                            f"Badge text: {str(getattr(draft, 'thumbnail_badge_text', '') or '').strip() or '(none)'}",
                            f"Font color: {self._youtube_thumbnail_color_label(draft.thumbnail_font_color)}",
                            f"Outline color: {self._youtube_thumbnail_color_label(draft.thumbnail_outline_color)}",
                            f"Font size: {self._youtube_thumbnail_font_size_label(draft.thumbnail_font_size_mode)}",
                            f"Text plate: {self._youtube_thumbnail_text_plate_label(getattr(draft, 'thumbnail_text_plate', YOUTUBE_THUMBNAIL_DEFAULT_TEXT_PLATE))}",
                            f"Anchor: {self._youtube_thumbnail_anchor_label(draft.thumbnail_anchor)}",
                            f"Offsets: X {int(draft.thumbnail_offset_x):+d}px | Y {int(draft.thumbnail_offset_y):+d}px",
                            f"Alignment: {self._youtube_thumbnail_align_label(draft.thumbnail_text_align)}",
                            f"Background variant: {int(draft.thumbnail_background_variant)}",
                            "",
                            *self._youtube_thumbnail_diagnostic_lines(draft),
                            (
                                "Editing note: v1 styling tools are only available for generated thumbnails."
                                if draft.thumbnail_source == "manual"
                                else "Editing note: this thumbnail is managed by Thumbnail Studio."
                            ),
                        ]
                    ),
                )
                continue

            generated_path = self._render_thumbnail_studio_image(project_dir, draft)
            if generated_path is None:
                return changed
            changed = True
            self._append_log(f"YouTube thumbnail generated: {generated_path}")
            self._set_status(f"YouTube thumbnail ready: {generated_path.name}")

    def _prepare_run_workspace(self) -> Path:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        prompt_slug = self._slugify(self.config.prompt)[:48]
        base_name = f"{prompt_slug}-{stamp}"
        candidate = self.config.project_dir / base_name
        suffix = 1
        while candidate.exists():
            candidate = self.config.project_dir / f"{base_name}-{suffix:02d}"
            suffix += 1

        candidate.mkdir(parents=True, exist_ok=False)
        self._active_project_dir = candidate
        self._pending_export_path = self._downloads_export_mp4_path(candidate)
        self._append_log(f"Run workspace: {candidate}")
        self._append_log(f"Planned MP4 output: {self._pending_export_path}")
        return candidate

    def _downloads_export_mp4_path(self, project_dir: Path) -> Path:
        downloads_dir = (Path.home() / "Downloads").resolve()
        project_name = self._slugify(project_dir.name)
        return downloads_dir / f"{project_name}.mp4"

    def _downloads_export_thumbnail_path(self, project_dir: Path, source_thumbnail: Path) -> Path:
        downloads_dir = (Path.home() / "Downloads").resolve()
        project_name = self._slugify(project_dir.name)
        suffix = source_thumbnail.suffix.lower() or ".jpg"
        return downloads_dir / f"{project_name}-thumbnail{suffix}"

    def _mp4_output_preview_path(self) -> Path:
        if self._pending_export_path is not None:
            return self._pending_export_path

        base_slug = self._slugify(self.config.prompt)
        return (Path.home() / "Downloads" / f"{base_slug}-<timestamp>.mp4").resolve()

    def _export_project_outputs_to_downloads(self) -> tuple[Path | None, Path | None]:
        project_dir = self._active_project_dir
        if project_dir is None:
            self._append_log("WARN: No active workspace found for MP4 export.")
            return None, None

        source_mp4 = project_dir / "output" / "final.mp4"
        if not source_mp4.exists():
            self._append_log(f"WARN: final.mp4 not found at {source_mp4}")
            return None, None

        target_mp4 = self._downloads_export_mp4_path(project_dir)
        target_mp4.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_mp4, target_mp4)

        self._pending_export_path = target_mp4
        self._append_log(f"Exported final MP4 to {target_mp4}")

        thumbnail_path = ensure_project_youtube_thumbnail(
            project_dir,
            prompt_text="",
            overwrite=False,
            notify=self._append_log,
        )
        if thumbnail_path is None or not thumbnail_path.exists():
            self._append_log("WARN: No thumbnail available for Downloads export.")
            return target_mp4, None

        target_thumbnail = self._downloads_export_thumbnail_path(project_dir, thumbnail_path)
        target_thumbnail.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(thumbnail_path, target_thumbnail)
        self._append_log(f"Exported thumbnail to {target_thumbnail}")
        return target_mp4, target_thumbnail

    def _count_placeholder_scenes(self) -> int:
        project_dir = self._active_project_dir
        if project_dir is None:
            return 0

        timeline_path = project_dir / "timeline.json"
        if not timeline_path.exists():
            return 0

        try:
            payload = json.loads(timeline_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse timeline for placeholder check: {exc}")
            return 0

        clips = payload.get("clips")
        if not isinstance(clips, list):
            return 0

        placeholders = 0
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            scene_id = str(clip.get("scene_id") or "")
            if scene_id in {"__intro", "__outro"}:
                continue

            source_path = clip.get("source_path")
            if not source_path:
                placeholders += 1

        return placeholders

    def _queue_unique_asset_prompt_from_run_report(self) -> bool:
        project_dir = self._active_project_dir
        if project_dir is None:
            return False

        report_path = project_dir / "run_report.json"
        if not report_path.exists():
            return False

        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse run_report for uniqueness prompt: {exc}")
            return False

        asset_stats = payload.get("asset_stats")
        if not isinstance(asset_stats, dict):
            return False

        try:
            shortfall_count = int(asset_stats.get("unique_shortfall_count") or 0)
        except Exception:
            shortfall_count = 0

        if shortfall_count <= 0:
            return False

        clip_names_raw = asset_stats.get("unique_shortfall_clip_names")
        if isinstance(clip_names_raw, list):
            clip_names = [str(item).strip() for item in clip_names_raw if str(item).strip()]
        else:
            clip_names = []

        scene_ids_raw = asset_stats.get("unique_shortfall_scene_ids")
        if isinstance(scene_ids_raw, list):
            scene_ids = [str(item).strip() for item in scene_ids_raw if str(item).strip()]
        else:
            scene_ids = []

        with self._lock:
            self._pending_unique_asset_prompt = {
                "shortfall_count": shortfall_count,
                "clip_names": clip_names,
                "scene_ids": scene_ids,
            }

        self._append_log(
            "WARN: Unique external clips were insufficient for this run. "
            "Please broaden asset keywords and retry."
        )
        if clip_names:
            self._append_log(f"Affected clip names: {', '.join(clip_names[:12])}")
        return True

    def _maybe_prompt_unique_asset_shortfall(self) -> None:
        with self._lock:
            pending = self._pending_unique_asset_prompt
            self._pending_unique_asset_prompt = None

        if not isinstance(pending, dict):
            return

        shortfall_count = int(pending.get("shortfall_count") or 0)
        clip_names = [str(item).strip() for item in pending.get("clip_names") or [] if str(item).strip()]

        if shortfall_count > 0:
            self._set_status(
                f"Need more unique assets ({shortfall_count} clip(s) unresolved). "
                "Please broaden keywords."
            )

        default_keywords = ", ".join(self.config.asset_keywords)
        updated_keywords = self._prompt_input(
            "Asset keywords (broaden for unique clips)",
            default_keywords,
        )

        if updated_keywords is None:
            self._set_status("Unique clip shortage remains. Press E to edit keywords, then R to retry.")
            return
        if not isinstance(updated_keywords, str):
            self._set_status("Unique clip shortage remains. Press E to edit keywords, then R to retry.")
            return

        parsed = [part.strip() for part in re.split(r"[,;\n]+", updated_keywords) if part.strip()]
        normalized = self._normalize_asset_keywords(parsed)
        if not normalized:
            self._set_status("No new keywords provided. Press E to edit and R to retry.")
            return

        self.config.asset_keywords = normalized
        self._append_log(f"Updated asset keywords after uniqueness block: {', '.join(self.config.asset_keywords)}")
        if clip_names:
            self._append_log(f"Retrying may replace these unresolved clips: {', '.join(clip_names[:12])}")
        self._set_status("Keywords updated. Press R to retry generation.")

    def _queue_clip_review_prompt(self, project_dir: Path) -> None:
        with self._lock:
            self._pending_clip_review_prompt = {
                "project_dir": str(project_dir),
            }

    def _queue_stage_transition_prompt(self, *, next_stage: str, title: str, body: str) -> None:
        with self._lock:
            self._pending_stage_transition_prompt = {
                "next_stage": str(next_stage).strip().lower(),
                "title": title,
                "body": body,
            }

    def _queue_source_review_prompt(self, project_dir: Path) -> None:
        with self._lock:
            self._pending_source_review_prompt = {
                "project_dir": str(project_dir),
            }

    def _queue_script_review_prompt(self, project_dir: Path) -> None:
        with self._lock:
            self._pending_script_review_prompt = {
                "project_dir": str(project_dir),
            }

    def _queue_shot_review_prompt(self, project_dir: Path) -> None:
        with self._lock:
            self._pending_shot_review_prompt = {
                "project_dir": str(project_dir),
            }

    def _queue_scene_review_prompt(self, project_dir: Path) -> None:
        with self._lock:
            self._pending_scene_review_prompt = {
                "project_dir": str(project_dir),
            }

    def _script_path_for_review(self, project_dir: Path) -> Path:
        approved_path = project_dir / "review" / "script_approved.json"
        if approved_path.exists():
            return approved_path
        return project_dir / "script.json"

    def _news_candidates_path(self, project_dir: Path) -> Path:
        return project_dir / "review" / "news_source_candidates.json"

    def _news_review_state_path(self, project_dir: Path) -> Path:
        return project_dir / "review" / "news_review_state.json"

    def _load_source_review_entries(self, project_dir: Path) -> list[dict[str, Any]]:
        candidates_path = self._news_candidates_path(project_dir)
        if not candidates_path.exists():
            return []
        try:
            payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse news source candidates: {exc}")
            return []

        raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(raw_candidates, list):
            return []
        entries: list[dict[str, Any]] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "").strip()
            article_url = str(item.get("article_url") or "").strip()
            if not source_id or not article_url:
                continue
            entries.append(
                {
                    "source_id": source_id,
                    "article_url": article_url,
                    "canonical_url": str(item.get("canonical_url") or article_url).strip(),
                    "domain": str(item.get("domain") or "").strip(),
                    "title": str(item.get("title") or article_url).strip(),
                    "publisher": str(item.get("publisher") or "").strip(),
                    "summary": str(item.get("summary") or "").strip(),
                    "published_at": str(item.get("published_at") or "").strip(),
                    "screenshot_path": str(item.get("screenshot_path") or "").strip(),
                    "source_card_path": str(item.get("source_card_path") or "").strip(),
                    "screenshot_available": bool(item.get("screenshot_available")),
                    "screenshot_reason": str(item.get("screenshot_reason") or "").strip(),
                }
            )
        return entries

    def _load_source_review_requirements(self, project_dir: Path) -> dict[str, Any]:
        candidates_path = self._news_candidates_path(project_dir)
        if not candidates_path.exists():
            return {
                "min_approved_sources": 2,
                "jurisdiction": "us",
                "require_manual_source_approval": True,
            }
        try:
            payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse news source review requirements: {exc}")
            return {
                "min_approved_sources": 2,
                "jurisdiction": "us",
                "require_manual_source_approval": True,
            }

        required_sources = max(1, int(payload.get("news_min_approved_sources") or 2))
        return {
            "min_approved_sources": required_sources,
            "jurisdiction": str(payload.get("news_jurisdiction") or "us").strip().lower() or "us",
            "require_manual_source_approval": bool(payload.get("news_require_manual_source_approval", True)),
        }

    def _load_source_review_state(self, project_dir: Path) -> dict[str, dict[str, Any]]:
        state_path = self._news_review_state_path(project_dir)
        if not state_path.exists():
            return {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse source review state: {exc}")
            return {}
        raw_sources = payload.get("sources") if isinstance(payload, dict) else None
        if not isinstance(raw_sources, dict):
            return {}
        state: dict[str, dict[str, Any]] = {}
        for source_id, value in raw_sources.items():
            key = str(source_id).strip()
            if key and isinstance(value, dict):
                state[key] = dict(value)
        return state

    def _save_source_review_state(self, project_dir: Path, state: dict[str, dict[str, Any]]) -> None:
        state_path = self._news_review_state_path(project_dir)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sources": state,
        }
        state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def _source_review_record(self, state: dict[str, dict[str, Any]], source_id: str) -> dict[str, Any]:
        key = str(source_id).strip()
        if key not in state:
            state[key] = {"decision": "pending", "updated_at": None}
        return state[key]

    def _source_review_counts(
        self,
        entries: list[dict[str, Any]],
        state: dict[str, dict[str, Any]],
        *,
        required_sources: int,
    ) -> tuple[int, int, bool]:
        approved = 0
        domains: set[str] = set()
        for entry in entries:
            source_id = str(entry.get("source_id") or "").strip()
            if not source_id:
                continue
            record = self._source_review_record(state, source_id)
            decision = str(record.get("decision") or "").strip().lower()
            if decision not in {"approve-facts", "approve-screenshot"}:
                continue
            approved += 1
            domain = str(entry.get("domain") or "").strip()
            if domain:
                domains.add(domain)
        required = max(1, int(required_sources))
        ready = approved >= required and len(domains) >= required
        return approved, len(domains), ready

    def _source_review_token(self, record: dict[str, Any]) -> str:
        decision = str(record.get("decision") or "pending").strip().lower()
        if decision == "approve-screenshot":
            return "S"
        if decision == "approve-facts":
            return "F"
        if decision == "reject":
            return "R"
        return "-"

    def _source_review_body(self, entry: dict[str, Any], record: dict[str, Any]) -> str:
        lines = [
            f"Publisher: {str(entry.get('publisher') or entry.get('domain') or 'Unknown').strip()}",
            f"Title: {str(entry.get('title') or '').strip()}",
            f"URL: {str(entry.get('article_url') or '').strip()}",
            f"Published: {str(entry.get('published_at') or 'unknown').strip()}",
            f"Decision: {str(record.get('decision') or 'pending').strip()}",
            "",
            "Summary:",
            str(entry.get("summary") or "(no extracted summary)").strip(),
        ]
        if bool(entry.get("screenshot_available")):
            lines.extend(["", f"Screenshot: {str(entry.get('screenshot_path') or '').strip()}"])
        else:
            lines.extend(["", f"Screenshot unavailable: {str(entry.get('screenshot_reason') or 'not captured').strip()}"])
        return "\n".join(lines).strip()

    def _run_source_review_prompt(self, project_dir: Path) -> str:
        current_choice: str | None = None
        while True:
            entries = self._load_source_review_entries(project_dir)
            if not entries:
                self._set_status("No source candidates are available to review.")
                return "cancel"

            requirements = self._load_source_review_requirements(project_dir)
            required_sources = max(1, int(requirements.get("min_approved_sources") or 2))
            jurisdiction = str(requirements.get("jurisdiction") or "us").strip().upper()
            state = self._load_source_review_state(project_dir)
            approved_count, domain_count, ready = self._source_review_counts(
                entries,
                state,
                required_sources=required_sources,
            )
            option_details: dict[str, str] = {}
            options: list[str] = []
            entry_by_label: dict[str, dict[str, Any]] = {}
            for index, entry in enumerate(entries, start=1):
                source_id = str(entry.get("source_id") or "").strip()
                record = self._source_review_record(state, source_id)
                title = self._trim_tail(str(entry.get("title") or "").strip(), 56)
                publisher = str(entry.get("publisher") or entry.get("domain") or "").strip()
                label = f"{index:02d}. [{self._source_review_token(record)}] {publisher} | {title}"
                options.append(label)
                entry_by_label[label] = entry
                option_details[label] = self._source_review_body(entry, record)

            if ready:
                options.append("Continue to draft")
                option_details["Continue to draft"] = (
                    "Gate satisfied: "
                    f"{approved_count}/{required_sources} approved sources across "
                    f"{domain_count}/{required_sources} domains ({jurisdiction})."
                )

            choice = self._select_from_list(
                label=(
                    "Source Review "
                    f"({approved_count}/{required_sources} approved, "
                    f"{domain_count}/{required_sources} domains)"
                ),
                options=options,
                current_value=current_choice if current_choice in options else options[0],
                option_details=option_details,
            )
            if choice is None:
                return "cancel"

            current_choice = cast(str, choice)
            if current_choice == "Continue to draft":
                return "ready"

            entry = entry_by_label.get(current_choice)
            if entry is None:
                continue

            source_id = str(entry.get("source_id") or "").strip()
            record = self._source_review_record(state, source_id)
            actions = ["Read source summary", "Approve facts only", "Reject"]
            if bool(entry.get("screenshot_available")) and str(entry.get("screenshot_path") or "").strip():
                actions.insert(1, "Preview screenshot")
                actions.insert(3, "Approve facts + screenshot")

            action = self._select_from_list(
                label=f"Source {source_id}",
                options=actions,
                current_value="Read source summary",
            )
            if action is None:
                continue
            if action == "Read source summary":
                self._show_paginated_text_modal("Source Review", self._source_review_body(entry, record))
                continue
            if action == "Preview screenshot":
                screenshot_path = str(entry.get("screenshot_path") or "").strip()
                if screenshot_path:
                    self._play_media_path(Path(screenshot_path), label=f"source-{source_id}", audio_only=False)
                continue

            if action == "Approve facts + screenshot" and not bool(entry.get("screenshot_available")):
                self._set_status("Screenshot approval is unavailable for this source.")
                continue

            if action == "Approve facts only":
                record["decision"] = "approve-facts"
            elif action == "Approve facts + screenshot":
                record["decision"] = "approve-screenshot"
            elif action == "Reject":
                record["decision"] = "reject"
            record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            self._save_source_review_state(project_dir, state)

    def _maybe_prompt_source_review(self) -> None:
        with self._lock:
            pending = self._pending_source_review_prompt
            self._pending_source_review_prompt = None

        if not isinstance(pending, dict):
            return

        project_dir_raw = str(pending.get("project_dir") or "").strip()
        if not project_dir_raw:
            return

        project_dir = Path(project_dir_raw).expanduser().resolve()
        if not project_dir.exists():
            self._set_status("Source review workspace was not found.")
            return

        with self._modal_focus():
            outcome = self._run_source_review_prompt(project_dir)

        if outcome == "ready":
            self._hitl_stage = "draft"
            self._start_run_workflow()
            return

        self._set_status("Source review closed. Press R to resume.")

    def _load_script_review_payload(self, project_dir: Path) -> dict[str, Any]:
        script_path = self._script_path_for_review(project_dir)
        if not script_path.exists():
            raise RuntimeError(f"Script file not found for review: {script_path}")

        payload = json.loads(script_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Script JSON is invalid for review")
        return payload

    def _format_script_review_text(self, project_dir: Path) -> str:
        payload = self._load_script_review_payload(project_dir)
        title = str(payload.get("title") or self.config.prompt).strip() or self.config.prompt
        summary = str(payload.get("summary") or "").strip()
        raw_scenes = payload.get("scenes")
        if not isinstance(raw_scenes, list):
            raw_scenes = []

        parts = [f"Title: {title}"]
        if summary:
            parts.append(f"Summary: {summary}")
        parts.append("")

        for index, item in enumerate(raw_scenes, start=1):
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading") or f"Scene {index}").strip() or f"Scene {index}"
            voiceover = str(item.get("voiceover") or "").strip()
            parts.append(f"{index:02d}. {heading}")
            parts.append(voiceover or "(empty)")
            parts.append("")

        return "\n".join(parts).strip()

    def _clear_script_approval_snapshot(self, project_dir: Path) -> None:
        approved_path = project_dir / "review" / "script_approved.json"
        if approved_path.exists():
            approved_path.unlink()

    def _external_editor_summary(self) -> str:
        command = str(self.config.external_editor_command).strip()
        return "configured" if command else "not set"

    def _thumbnail_comfyui_summary(self) -> str:
        reachable = self._probe_comfyui_online(max_age=4.0)
        return "online" if reachable else "offline"

    def _thumbnail_comfyui_install_summary(self) -> str:
        configured = str(getattr(self.config, "thumbnail_comfyui_install_dir", "") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve().name or configured
        detected = self._detect_local_comfyui_install_dir()
        if detected is not None:
            return f"auto:{detected.name}"
        return "not found"

    def _thumbnail_export_dir_summary(self) -> str:
        export_dir = Path(
            str(getattr(self.config, "thumbnail_default_export_dir", THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR) or "").strip()
            or str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR)
        ).expanduser().resolve()
        return export_dir.name or str(export_dir)

    def _thumbnail_ollama_hooks_summary(self) -> str:
        return "On" if bool(getattr(self.config, "thumbnail_use_ollama_hooks", True)) else "Off"

    def _open_path_in_external_editor(self, file_path: Path) -> bool:
        resolved_path = file_path.expanduser().resolve()
        if not resolved_path.exists():
            self._set_status(f"Editor target not found: {resolved_path}")
            return False

        command_template = str(self.config.external_editor_command).strip()
        if not command_template:
            self._set_status("External editor is not configured in Settings.")
            return False
        if "{file}" not in command_template:
            self._append_log("WARN: External editor command is missing {file}.")
            self._set_status("External editor command must include {file}.")
            return False

        command_text = command_template.replace("{file}", shlex.quote(str(resolved_path)))
        self._append_log(f"$ {command_text}")
        try:
            completed = subprocess.run(
                command_text,
                shell=True,
                cwd=str(self._repo_root),
                timeout=7200,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._append_log("ERROR: External editor command timed out after 7200s.")
            self._set_status("External editor timed out.")
            return False
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: External editor launch failed: {exc}")
            self._set_status("Could not launch external editor.")
            return False

        if int(completed.returncode) != 0:
            self._append_log(f"ERROR: External editor exited with code {completed.returncode}.")
            self._set_status(f"External editor exited with code {completed.returncode}.")
            return False
        return True

    def _open_script_in_external_editor(self, project_dir: Path) -> bool:
        script_path = self._script_path_for_review(project_dir)
        if not script_path.exists():
            self._set_status(f"Script file not found: {script_path}")
            return False

        return self._open_path_in_external_editor(script_path)

    def _run_script_review_prompt(self, project_dir: Path) -> str:
        entries = self._load_scene_review_entries(project_dir)
        if not entries:
            self._set_status("No script scenes are available to review.")
            return "cancel"

        current_choice = "Read full script"
        while True:
            entries = self._load_scene_review_entries(project_dir)
            if not entries:
                self._set_status("No script scenes are available to review.")
                return "cancel"

            scene_labels: list[str] = []
            label_to_entry: dict[str, dict[str, Any]] = {}
            for index, entry in enumerate(entries, start=1):
                heading = str(entry.get("heading") or f"Scene {index}").strip() or f"Scene {index}"
                voiceover = re.sub(r"\s+", " ", str(entry.get("voiceover") or "").strip())
                snippet = self._trim_tail(voiceover, 52)
                label = f"Edit {index:02d}. {heading} | {snippet}"
                scene_labels.append(label)
                label_to_entry[label] = entry

            options = [
                "Read full script",
                *scene_labels,
                "Open in external editor",
                "Approve script",
                "Regenerate script",
                "Open prompt + regenerate",
            ]
            if current_choice not in options:
                current_choice = options[0]

            choice = self._select_from_list(
                label=f"Script Review ({len(entries)} scenes)",
                options=options,
                current_value=current_choice,
            )
            if choice is None:
                return "cancel"

            selected = cast(str, choice)
            current_choice = selected
            if selected == "Read full script":
                try:
                    body = self._format_script_review_text(project_dir)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"ERROR: Failed to load script review text: {exc}")
                    self._set_status("Could not open the script review view.")
                    continue
                self._show_paginated_text_modal("Script Review", body)
                continue

            if selected == "Open in external editor":
                self._open_script_in_external_editor(project_dir)
                continue

            if selected == "Approve script":
                if self._prompt_confirm_enter_escape(
                    title="Approve Script",
                    body="Approve this script and continue to shot planning?",
                ):
                    return "approve"
                continue

            if selected == "Regenerate script":
                if self._prompt_yes_no(
                    title="Regenerate Script",
                    body="Discard the current draft script and generate a new one with the current settings?",
                    default_yes=False,
                ):
                    return "regenerate"
                continue

            if selected == "Open prompt + regenerate":
                self._open_prompt_settings_menu()
                if self._prompt_yes_no(
                    title="Regenerate Script",
                    body="Generate a fresh draft script with the current settings?",
                    default_yes=True,
                ):
                    return "regenerate"
                continue

            entry = label_to_entry.get(selected)
            if entry is None:
                continue

            scene_id = str(entry.get("scene_id") or "").strip()
            clip_name = str(entry.get("clip_name") or scene_id or "scene").strip()
            updated_text = self._prompt_multiline_input(
                label=f"Script text ({clip_name})",
                current_value=str(entry.get("voiceover") or ""),
            )
            if updated_text is None:
                continue

            next_text = updated_text.strip()
            if not next_text:
                self._set_status("Scene text cannot be empty.")
                continue

            try:
                self._update_scene_voiceover(
                    project_dir,
                    scene_id=scene_id,
                    new_voiceover=next_text,
                    write_approved=False,
                )
                self._append_log(f"Updated script text for {scene_id}.")
                self._set_status(f"Script scene updated: {clip_name}")
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERROR: Failed to update script scene {scene_id}: {exc}")
                self._set_status("Could not save the script edit.")

        return "cancel"

    def _maybe_prompt_script_review(self) -> None:
        with self._lock:
            pending = self._pending_script_review_prompt
            self._pending_script_review_prompt = None

        if not isinstance(pending, dict):
            return

        project_dir_raw = str(pending.get("project_dir") or "").strip()
        if not project_dir_raw:
            return

        project_dir = Path(project_dir_raw).expanduser().resolve()
        if not project_dir.exists():
            self._set_status("Script review workspace was not found.")
            return

        with self._modal_focus():
            outcome = self._run_script_review_prompt(project_dir)

        if outcome == "approve":
            self._hitl_stage = "shot-plan"
            self._start_run_workflow()
            return

        if outcome == "regenerate":
            self._clear_script_approval_snapshot(project_dir)
            self._hitl_stage = "draft"
            self._start_run_workflow()
            return

        self._set_status("Script review closed. Press R to resume.")

    def _shot_plan_path(self, project_dir: Path) -> Path:
        return project_dir / "review" / "shot_plan.json"

    def _shot_review_state_path(self, project_dir: Path) -> Path:
        return project_dir / "review" / "shot_review_state.json"

    def _shot_candidate_manifest_path(self, project_dir: Path, shot_id: str) -> Path:
        return project_dir / "review" / "shots" / str(shot_id).strip() / "candidate_manifest.json"

    def _load_shot_review_state(self, project_dir: Path) -> dict[str, dict[str, Any]]:
        state_path = self._shot_review_state_path(project_dir)
        if not state_path.exists():
            return {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse shot review state: {exc}")
            return {}
        shots = payload.get("shots") if isinstance(payload, dict) else None
        if not isinstance(shots, dict):
            return {}
        return {
            str(shot_id).strip(): dict(value)
            for shot_id, value in shots.items()
            if str(shot_id).strip() and isinstance(value, dict)
        }

    def _save_shot_review_state(self, project_dir: Path, state: dict[str, dict[str, Any]]) -> None:
        path = self._shot_review_state_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "shots": {shot_id: dict(value) for shot_id, value in sorted(state.items())},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def _load_shot_candidate_manifest(self, project_dir: Path, shot_id: str) -> dict[str, Any] | None:
        path = self._shot_candidate_manifest_path(project_dir, shot_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse shot candidate manifest: {exc}")
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _load_shot_review_entries(self, project_dir: Path) -> list[dict[str, Any]]:
        shot_plan_path = self._shot_plan_path(project_dir)
        if not shot_plan_path.exists():
            return []
        try:
            payload = json.loads(shot_plan_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse shot plan: {exc}")
            return []
        raw_shots = payload.get("shots") if isinstance(payload, dict) else None
        if not isinstance(raw_shots, list):
            return []

        clip_catalog_path = project_dir / "review" / "clip_catalog.json"
        clip_by_shot: dict[str, dict[str, Any]] = {}
        if clip_catalog_path.exists():
            try:
                catalog_payload = json.loads(clip_catalog_path.read_text(encoding="utf-8"))
                clips = catalog_payload.get("clips") if isinstance(catalog_payload, dict) else None
                if isinstance(clips, list):
                    for item in clips:
                        if not isinstance(item, dict):
                            continue
                        shot_id = str(item.get("shot_id") or "").strip()
                        if shot_id:
                            clip_by_shot[shot_id] = item
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"WARN: Could not parse shot clip catalog: {exc}")

        entries: list[dict[str, Any]] = []
        for item in raw_shots:
            if not isinstance(item, dict):
                continue
            shot_id = str(item.get("shot_id") or "").strip()
            if not shot_id:
                continue
            catalog_item = clip_by_shot.get(shot_id, {})
            preview_path = project_dir / "review" / "shots" / shot_id / "preview.mp4"
            entries.append(
                {
                    "shot_id": shot_id,
                    "scene_id": str(item.get("scene_id") or "").strip(),
                    "clip_name": str(item.get("clip_name") or "").strip(),
                    "heading": str(item.get("heading") or "").strip(),
                    "shot_index": int(item.get("shot_index") or 1),
                    "total_shots": int(item.get("total_shots") or 1),
                    "shot_objective": str(item.get("shot_objective") or "").strip(),
                    "key_info": str(item.get("key_info") or "").strip(),
                    "narration_text": str(item.get("narration_text") or "").strip(),
                    "required_entities": list(item.get("required_entities") or [])
                    if isinstance(item.get("required_entities"), list)
                    else [],
                    "search_queries": list(item.get("search_queries") or [])
                    if isinstance(item.get("search_queries"), list)
                    else [],
                    "match_confidence": str(item.get("match_confidence") or "").strip() or "medium",
                    "fallback_level": str(item.get("fallback_level") or "").strip() or "exact",
                    "asset_candidates": list(catalog_item.get("candidates") or [])
                    if isinstance(catalog_item.get("candidates"), list)
                    else [],
                    "asset_path": str(catalog_item.get("asset_path") or "").strip(),
                    "asset_media_type": str(catalog_item.get("asset_media_type") or "").strip(),
                    "preview_path": str(catalog_item.get("preview_path") or str(preview_path)).strip(),
                }
            )
        return entries

    def _media_type_from_path(self, media_path: Path) -> str:
        suffix = media_path.suffix.lower()
        if suffix in self.IMAGE_SUFFIXES:
            return "image"
        if suffix in self.VIDEO_SUFFIXES:
            return "video"
        return "unknown"

    def _resolve_ffmpeg_binary(self) -> str | None:
        from_path = shutil.which("ffmpeg")
        if from_path:
            return from_path
        for candidate in (
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/local/bin/ffmpeg",
        ):
            path = Path(candidate)
            if path.exists() and os.access(str(path), os.X_OK):
                return str(path)
        return None

    def _resolve_ffprobe_binary(self) -> str | None:
        from_path = shutil.which("ffprobe")
        if from_path:
            return from_path
        for candidate in (
            "/opt/homebrew/bin/ffprobe",
            "/usr/local/bin/ffprobe",
            "/opt/local/bin/ffprobe",
        ):
            path = Path(candidate)
            if path.exists() and os.access(str(path), os.X_OK):
                return str(path)
        return None

    def _probe_media_duration_seconds(self, media_path: Path) -> float | None:
        ffprobe_bin = self._resolve_ffprobe_binary()
        if ffprobe_bin is None:
            return None
        try:
            completed = subprocess.run(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(media_path),
                ],
                cwd=str(self._repo_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=6,
            )
        except Exception:
            return None
        if int(completed.returncode or 0) != 0:
            return None
        raw_value = str(completed.stdout or "").strip()
        try:
            duration = float(raw_value)
        except ValueError:
            return None
        if not math.isfinite(duration) or duration <= 0.0:
            return None
        return duration

    def _shot_review_preview_source(self, entry: dict[str, Any]) -> tuple[Path | None, str, str]:
        asset_path_raw = str(entry.get("asset_path") or "").strip()
        asset_media_type = str(entry.get("asset_media_type") or "").strip().lower()
        preview_path_raw = str(entry.get("preview_path") or "").strip()

        candidates: list[tuple[Path, str, str]] = []
        if asset_path_raw:
            asset_path = Path(asset_path_raw).expanduser().resolve()
            candidates.append(
                (
                    asset_path,
                    asset_media_type or self._media_type_from_path(asset_path),
                    "asset",
                )
            )
        if preview_path_raw:
            preview_path = Path(preview_path_raw).expanduser().resolve()
            candidates.append(
                (
                    preview_path,
                    self._media_type_from_path(preview_path),
                    "preview",
                )
            )

        for path, media_type, source_label in candidates:
            if path.exists():
                return path, media_type, source_label
        return None, "unknown", "missing"

    def _shot_review_ascii_placeholder(self, width: int, height: int, message: str) -> list[str]:
        safe_width = max(12, width)
        safe_height = max(4, height)
        wrapped = textwrap.wrap(
            str(message).strip() or "Preview unavailable",
            width=max(8, safe_width - 2),
            replace_whitespace=False,
            drop_whitespace=False,
        )[:safe_height]
        top_padding = max(0, (safe_height - len(wrapped)) // 2)
        lines = [" " * safe_width for _ in range(top_padding)]
        for line in wrapped:
            centered = line.center(safe_width)
            lines.append(centered[:safe_width].ljust(safe_width))
        while len(lines) < safe_height:
            lines.append(" " * safe_width)
        return lines[:safe_height]

    def _graphics_preview_capable(self) -> bool:
        term = str(os.environ.get("TERM") or "").strip().lower()
        term_program = str(os.environ.get("TERM_PROGRAM") or "").strip().lower()
        if os.environ.get("KITTY_WINDOW_ID"):
            return True
        if "kitty" in term or "ghostty" in term or "ghostty" in term_program:
            return True
        if "wezterm" in term_program:
            return True
        if "iterm" in term_program:
            return True
        return False

    def _shot_review_preview_runtime_dir(self) -> Path:
        root = (Path.home() / ".imagine" / "runtime" / "shot-review-preview").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _preview_png_from_media_path(
        self,
        media_path: Path,
        *,
        media_type: str,
    ) -> Path | None:
        if not media_path.exists():
            return None

        ffmpeg_bin = self._resolve_ffmpeg_binary()
        if ffmpeg_bin is None:
            return None

        try:
            mtime_ns = int(media_path.stat().st_mtime_ns)
        except OSError:
            mtime_ns = 0

        cache_key = (str(media_path), str(media_type), mtime_ns)
        cached = self._shot_preview_image_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached

        command = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if media_type == "video":
            duration = self._probe_media_duration_seconds(media_path)
            sample_time = 0.8 if duration is None else min(1.5, max(0.2, duration * 0.35))
            command.extend(["-ss", f"{sample_time:.3f}"])
        cache_name = hashlib.sha1(f"{media_path}:{media_type}:{mtime_ns}".encode("utf-8")).hexdigest()[:20]
        output_path = self._shot_review_preview_runtime_dir() / f"{cache_name}.png"
        command.extend(
            [
                "-i",
                str(media_path),
                "-frames:v",
                "1",
                "-vf",
                (
                    "scale=960:540:force_original_aspect_ratio=decrease,"
                    "pad=960:540:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
                ),
                str(output_path),
            ]
        )

        try:
            completed = subprocess.run(
                command,
                cwd=str(self._repo_root),
                check=False,
                capture_output=True,
                timeout=12,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not render shot review preview for {media_path.name}: {exc}")
            return None

        if int(completed.returncode or 0) != 0:
            stderr_text = str(completed.stderr or b"").strip()
            if stderr_text:
                self._append_log(f"WARN: Shot review preview render failed for {media_path.name}: {stderr_text}")
            return None
        if not output_path.exists():
            return None
        self._shot_preview_image_cache[cache_key] = output_path
        return output_path

    def _write_terminal_escape(self, payload: bytes) -> bool:
        try:
            with open("/dev/tty", "wb", buffering=0) as tty:
                tty.write(payload)
                tty.flush()
            return True
        except Exception:
            return False

    def _clear_terminal_region(self, *, top_row: int, left_col: int, cols: int, rows: int) -> bool:
        if cols <= 0 or rows <= 0:
            return False
        payload = bytearray()
        blank_line = (" " * cols).encode("utf-8")
        for row_index in range(rows):
            payload.extend(f"\x1b[{top_row + row_index};{left_col}H".encode("ascii"))
            payload.extend(blank_line)
        payload.extend(b"\x1b[0m")
        return self._write_terminal_escape(bytes(payload))

    def _clear_shot_review_graphics_preview(self) -> None:
        if not self._graphics_preview_capable():
            return
        sequence = f"\x1b_Ga=d,d=i,i={self.SHOT_REVIEW_PREVIEW_IMAGE_ID},q=2\x1b\\".encode("ascii")
        self._write_terminal_escape(sequence)

    def _draw_shot_review_graphics_preview(
        self,
        *,
        image_path: Path,
        top_row: int,
        left_col: int,
        cols: int,
        rows: int,
    ) -> bool:
        if not self._graphics_preview_capable():
            return False
        encoded_path = base64.standard_b64encode(str(image_path).encode("utf-8")).decode("ascii")
        move_cursor = f"\x1b[{top_row};{left_col}H".encode("ascii")
        draw_image = (
            f"\x1b_Ga=T,i={self.SHOT_REVIEW_PREVIEW_IMAGE_ID},q=2,f=100,t=f,"
            f"c={max(1, cols)},r={max(1, rows)},C=1;{encoded_path}\x1b\\"
        ).encode("ascii")
        return self._write_terminal_escape(move_cursor + draw_image)

    def _color_preview_lines_from_image_path(
        self,
        image_path: Path,
        *,
        width_chars: int,
        height_lines: int,
    ) -> list[bytes] | None:
        ffmpeg_bin = self._resolve_ffmpeg_binary()
        if ffmpeg_bin is None or not image_path.exists():
            return None

        safe_width = max(8, width_chars)
        safe_height = max(4, height_lines)
        source_height = safe_height * 2
        try:
            mtime_ns = int(image_path.stat().st_mtime_ns)
        except OSError:
            mtime_ns = 0
        cache_key = (str(image_path), safe_width, safe_height, mtime_ns)
        cached = self._shot_preview_color_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        command = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(image_path),
            "-frames:v",
            "1",
            "-vf",
            (
                f"scale={safe_width}:{source_height}:force_original_aspect_ratio=decrease,"
                f"pad={safe_width}:{source_height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            ),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self._repo_root),
                check=False,
                capture_output=True,
                timeout=12,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not prepare color shot preview for {image_path.name}: {exc}")
            return None
        if int(completed.returncode or 0) != 0:
            stderr_text = str(completed.stderr or b"").strip()
            if stderr_text:
                self._append_log(f"WARN: Color shot preview render failed for {image_path.name}: {stderr_text}")
            return None

        frame = completed.stdout or b""
        expected_bytes = safe_width * source_height * 3
        if len(frame) < expected_bytes:
            return None

        lines: list[bytes] = []
        for row_index in range(safe_height):
            top_offset = row_index * 2 * safe_width * 3
            bottom_offset = top_offset + (safe_width * 3)
            top_row = frame[top_offset : top_offset + (safe_width * 3)]
            bottom_row = frame[bottom_offset : bottom_offset + (safe_width * 3)]
            line = bytearray()
            for column in range(safe_width):
                top_base = column * 3
                bottom_base = column * 3
                tr, tg, tb = top_row[top_base], top_row[top_base + 1], top_row[top_base + 2]
                br, bg, bb = bottom_row[bottom_base], bottom_row[bottom_base + 1], bottom_row[bottom_base + 2]
                line.extend(f"\x1b[38;2;{tr};{tg};{tb}m\x1b[48;2;{br};{bg};{bb}m▀".encode("utf-8"))
            line.extend(b"\x1b[0m")
            lines.append(bytes(line))

        self._shot_preview_color_cache[cache_key] = list(lines)
        return lines

    def _draw_shot_review_color_preview(
        self,
        *,
        image_path: Path,
        top_row: int,
        left_col: int,
        cols: int,
        rows: int,
    ) -> bool:
        lines = self._color_preview_lines_from_image_path(
            image_path,
            width_chars=cols,
            height_lines=rows,
        )
        if not lines:
            return False
        payload = bytearray()
        for row_index, line in enumerate(lines[:rows]):
            payload.extend(f"\x1b[{top_row + row_index};{left_col}H".encode("ascii"))
            payload.extend(line)
        payload.extend(b"\x1b[0m")
        return self._write_terminal_escape(bytes(payload))

    def _wrap_shot_review_detail_text(self, detail_text: str, *, width: int, limit: int) -> list[str]:
        lines: list[str] = []
        for paragraph in str(detail_text or "").splitlines():
            cleaned = paragraph.strip()
            if not cleaned:
                continue
            wrapped = textwrap.wrap(
                cleaned,
                width=max(16, width),
                replace_whitespace=False,
                drop_whitespace=False,
            )
            if not wrapped:
                continue
            lines.extend(wrapped)
            if len(lines) >= limit:
                return lines[:limit]
        return lines[:limit]

    def _shot_review_preview_block(
        self,
        entry: dict[str, Any] | None,
        *,
        width: int,
        height: int,
    ) -> tuple[str, Path | None, list[str]]:
        if entry is None:
            return "No shot selected", None, self._shot_review_ascii_placeholder(width, height, "Continue when ready")

        preview_path, media_type, source_label = self._shot_review_preview_source(entry)
        if preview_path is None:
            return "Preview unavailable", None, self._shot_review_ascii_placeholder(width, height, "No local preview found")

        normalized_media_type = media_type if media_type in {"image", "video"} else self._media_type_from_path(preview_path)
        source_name = "asset" if source_label == "asset" else "preview"
        title = "Selected image" if normalized_media_type == "image" else "Selected video frame"
        preview_png = self._preview_png_from_media_path(
            preview_path,
            media_type=normalized_media_type,
        )
        if preview_png is not None:
            return title, preview_png, []
        fallback_message = "Inline preview unavailable"
        if normalized_media_type == "video":
            fallback_message = "Could not extract video frame"
        return title, None, self._shot_review_ascii_placeholder(width, height, fallback_message)

    def _select_shot_review_entry(
        self,
        *,
        label: str,
        options: list[str],
        current_value: str,
        option_details: dict[str, str] | None = None,
        entry_by_label: dict[str, dict[str, Any]] | None = None,
    ) -> str | None:
        if self._stdscr is None:
            return None

        normalized = [str(option).strip() for option in options if str(option).strip()]
        if not normalized:
            return None

        normalized_details = {
            str(key).strip(): str(value).strip()
            for key, value in (option_details or {}).items()
            if str(key).strip() and str(value).strip()
        }
        entries = entry_by_label or {}

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            if width < 84 or height < 18:
                return self._select_from_list(
                    label=label,
                    options=normalized,
                    current_value=current_value,
                    option_details=normalized_details,
                )

            max_option_len = max(len(item) for item in normalized)
            modal_width = min(max(96, max_option_len + 40), max(24, width - 2))
            modal_height = min(max(18, min(height - 2, len(normalized) + 8)), max(18, height - 2))
            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)

            list_width = min(max(30, max_option_len + 4), max(30, modal_width // 3))
            preview_width = max(24, modal_width - list_width - 5)
            interior_height = modal_height - 2
            preview_box_top = 2
            preview_box_height = max(8, min(16, interior_height - 7))
            detail_top = preview_box_top + preview_box_height + 1
            detail_rows = max(3, interior_height - preview_box_height - 3)
            list_rows = max(1, interior_height)

            selected = normalized.index(current_value) if current_value in normalized else 0
            start_index = max(0, selected - list_rows + 1)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            last_preview_key: str | None = None
            preview_title = ""
            preview_image_path: Path | None = None
            preview_lines: list[str] = []
            detail_lines: list[str] = []

            while True:
                current_label = normalized[selected]
                if current_label != last_preview_key:
                    entry = entries.get(current_label)
                    preview_title, preview_image_path, preview_lines = self._shot_review_preview_block(
                        entry,
                        width=max(12, preview_width - 4),
                        height=max(6, preview_box_height - 2),
                    )
                    detail_text = normalized_details.get(current_label, "")
                    detail_lines = self._wrap_shot_review_detail_text(
                        detail_text,
                        width=max(16, preview_width - 2),
                        limit=detail_rows,
                    )
                    last_preview_key = current_label

                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {label} ", max(1, modal_width - 4))
                help_text = "Enter open shot | Up/Down move | Esc back"
                divider_x = list_width + 2
                preview_inner_width = max(4, preview_width - 2)
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                    for row in range(1, modal_height - 1):
                        win.addch(row, divider_x, ord("|"), self._attr("muted"))
                    win.addstr(1, divider_x + 2, self._trim_tail(preview_title, preview_width), self._attr("muted", bold=True))
                    win.addstr(preview_box_top, divider_x + 2, "+" + ("-" * preview_inner_width) + "+", self._attr("muted"))
                    win.addstr(
                        preview_box_top + preview_box_height - 1,
                        divider_x + 2,
                        "+" + ("-" * preview_inner_width) + "+",
                        self._attr("muted"),
                    )
                except curses.error:
                    pass

                if selected < start_index:
                    start_index = selected
                elif selected >= start_index + list_rows:
                    start_index = selected - list_rows + 1

                for row in range(list_rows):
                    option_index = start_index + row
                    if option_index >= len(normalized):
                        break
                    item = normalized[option_index]
                    prefix = ">" if option_index == selected else " "
                    line = self._trim_tail(f"{prefix} {item}", list_width)
                    attr = curses.A_REVERSE if option_index == selected else 0
                    try:
                        win.addstr(1 + row, 1, line.ljust(list_width), attr)
                    except curses.error:
                        pass

                for row in range(max(0, preview_box_height - 2)):
                    try:
                        line = preview_lines[row] if row < len(preview_lines) else ""
                        win.addstr(
                            preview_box_top + 1 + row,
                            divider_x + 2,
                            "|" + self._trim_tail(line, preview_inner_width).ljust(preview_inner_width) + "|",
                        )
                    except curses.error:
                        pass

                for row in range(detail_rows):
                    line = detail_lines[row] if row < len(detail_lines) else ""
                    try:
                        win.addstr(detail_top + row, divider_x + 2, self._trim_tail(line, preview_width), self._attr("muted"))
                    except curses.error:
                        pass

                win.refresh()
                preview_top = top + preview_box_top + 2
                preview_left = left + divider_x + 4
                preview_rows = max(1, preview_box_height - 2)
                preview_cols = max(1, preview_inner_width)
                if preview_image_path is not None:
                    self._clear_terminal_region(
                        top_row=preview_top,
                        left_col=preview_left,
                        cols=preview_cols,
                        rows=preview_rows,
                    )
                    drew_preview = self._draw_shot_review_graphics_preview(
                        image_path=preview_image_path,
                        top_row=preview_top,
                        left_col=preview_left,
                        cols=preview_cols,
                        rows=preview_rows,
                    )
                    if not drew_preview:
                        self._clear_shot_review_graphics_preview()
                        self._draw_shot_review_color_preview(
                            image_path=preview_image_path,
                            top_row=preview_top,
                            left_col=preview_left,
                            cols=preview_cols,
                            rows=preview_rows,
                        )
                else:
                    self._clear_shot_review_graphics_preview()
                key = win.getch()

                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    selected = (selected - 1) % len(normalized)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    selected = (selected + 1) % len(normalized)
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    self._clear_shot_review_graphics_preview()
                    return normalized[selected]
                if key in (27,):
                    self._clear_shot_review_graphics_preview()
                    return None

    def _shot_review_token(self, record: dict[str, Any]) -> str:
        cycle = self._normalize_regenerate_cycle(record.get("regenerate_cycle"))
        if bool(cycle.get("regenerated")):
            return "R"
        return ""

    def _normalize_regenerate_cycle(self, payload: Any) -> dict[str, Any]:
        cycle = dict(payload) if isinstance(payload, dict) else {}

        def _int_list(value: Any) -> list[int]:
            items = value if isinstance(value, list) else []
            result: list[int] = []
            seen: set[int] = set()
            for item in items:
                try:
                    parsed = int(item)
                except (TypeError, ValueError):
                    continue
                if parsed in seen:
                    continue
                seen.add(parsed)
                result.append(parsed)
            return result

        phase = str(cycle.get("phase") or "video").strip().lower()
        if phase not in {"video", "image"}:
            phase = "video"
        return {
            "phase": phase,
            "video_tried": _int_list(cycle.get("video_tried")),
            "image_tried": _int_list(cycle.get("image_tried")),
            "exhausted": bool(cycle.get("exhausted")),
            "regenerated": bool(cycle.get("regenerated")),
            "search_queries": [
                str(item).strip()
                for item in cycle.get("search_queries") or []
                if str(item).strip()
            ]
            if isinstance(cycle.get("search_queries"), list)
            else [],
        }

    def _fresh_regenerate_cycle(self, search_queries: list[str] | None = None) -> dict[str, Any]:
        return {
            "phase": "video",
            "video_tried": [],
            "image_tried": [],
            "exhausted": False,
            "regenerated": False,
            "search_queries": [str(item).strip() for item in (search_queries or []) if str(item).strip()],
        }

    def _save_shot_regenerate_cycle(
        self,
        project_dir: Path,
        state: dict[str, dict[str, Any]],
        shot_id: str,
        cycle: dict[str, Any],
    ) -> None:
        record = state.setdefault(shot_id, {"approved": False, "blocked": True, "updated_at": None})
        record["regenerate_cycle"] = self._normalize_regenerate_cycle(cycle)
        self._save_shot_review_state(project_dir, state)

    def _shot_candidate_uniqueness_key(self, payload: dict[str, Any]) -> str:
        platform = str(payload.get("source_platform") or "unknown").strip().lower()
        source_asset_id = str(payload.get("source_asset_id") or "").strip()
        source_url = str(payload.get("source_url") or "").strip().lower()
        download_url = str(payload.get("download_url") or "").strip().lower()
        if source_asset_id:
            return f"{platform}:id:{source_asset_id}"
        if source_url:
            return f"{platform}:url:{source_url}"
        if download_url:
            return f"{platform}:download:{download_url}"
        return ""

    def _next_shot_regenerate_candidate(
        self,
        manifest: dict[str, Any],
        cycle: dict[str, Any],
        *,
        media_type: str,
    ) -> dict[str, Any] | None:
        video_candidates = [
            item
            for item in manifest.get("video_candidates") or []
            if isinstance(item, dict)
        ] if isinstance(manifest.get("video_candidates"), list) else []
        image_candidates = [
            item
            for item in manifest.get("image_candidates") or []
            if isinstance(item, dict)
        ] if isinstance(manifest.get("image_candidates"), list) else []

        tried_video = {int(item) for item in cycle.get("video_tried") or []}
        tried_image = {int(item) for item in cycle.get("image_tried") or []}
        current_asset_key = str(manifest.get("current_asset_key") or "").strip().lower()

        def pick(pool: list[dict[str, Any]], tried: set[int]) -> dict[str, Any] | None:
            for candidate in pool:
                shortlist_index = int(candidate.get("shortlist_index") or -1)
                if shortlist_index < 0:
                    continue
                if shortlist_index in tried:
                    continue
                if bool(candidate.get("selected")):
                    continue
                candidate_key = self._shot_candidate_uniqueness_key(candidate)
                if current_asset_key and candidate_key and candidate_key.lower() == current_asset_key:
                    continue
                return candidate
            return None

        if str(media_type).strip().lower() == "image":
            candidate = pick(image_candidates, tried_image)
            cycle["phase"] = "image"
            cycle["exhausted"] = candidate is None
            return candidate

        candidate = pick(video_candidates, tried_video)
        cycle["phase"] = "video"
        cycle["exhausted"] = candidate is None
        return candidate

    def _run_command_with_spinner(
        self,
        command: list[str],
        *,
        label: str,
        title: str,
        message: str,
        detail_text: str | None = None,
    ) -> int:
        return self._run_with_spinner_modal(
            title=title,
            message=message,
            detail_text=detail_text,
            task=lambda: self._run_and_stream(command, label=label),
        )

    def _prepare_shot_candidates_for_entry(
        self,
        project_dir: Path,
        shot_id: str,
        key_info: str,
        *,
        search_queries: list[str] | None = None,
        use_spinner: bool = False,
        detail_text: str | None = None,
    ) -> bool:
        command = [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "prepare-shot-candidates",
            "--project-dir",
            str(project_dir),
            "--shot-id",
            shot_id,
            "--key-info",
            re.sub(r"\s+", " ", key_info.strip()),
            "--verbose",
        ]
        for query in search_queries or []:
            cleaned = re.sub(r"\s+", " ", str(query).strip())
            if cleaned:
                command.extend(["--search-query", cleaned])
        if use_spinner:
            code = self._run_command_with_spinner(
                command,
                label="run",
                title="Regenerate Shot",
                message="Refreshing shot candidates",
                detail_text=detail_text,
            )
        else:
            code = self._run_and_stream(command, label="run")
        if code != 0:
            self._set_status(f"Candidate refresh failed with exit code {code}.")
            return False
        return True

    def _browse_shot_candidate_group(
        self,
        project_dir: Path,
        shot_id: str,
        *,
        group_label: str,
        candidates: list[dict[str, Any]],
        selected_shortlist_index: int | None,
    ) -> int | None:
        if not candidates:
            self._set_status(f"No {group_label.lower()} candidates are available for this shot.")
            return None

        current_choice: str | None = None
        while True:
            options: list[str] = []
            option_details: dict[str, str] = {}
            candidate_by_label: dict[str, dict[str, Any]] = {}
            for display_index, candidate in enumerate(candidates, start=1):
                shortlist_index = int(candidate.get("shortlist_index") or 0)
                selected_token = "*" if selected_shortlist_index == shortlist_index else " "
                preview_token = "preview" if bool(candidate.get("preview_available")) else "no preview"
                label = (
                    f"{display_index:02d}. [{selected_token}] "
                    f"{str(candidate.get('source_platform') or 'unknown')} | "
                    f"rank {float(candidate.get('ranking_score') or 0.0):.2f} | "
                    f"{preview_token}"
                )
                options.append(label)
                candidate_by_label[label] = candidate
                option_details[label] = (
                    f"Type: {str(candidate.get('media_type') or 'unknown')}\n\n"
                    f"Description: {str(candidate.get('description') or candidate.get('query') or '').strip()}\n\n"
                    f"Source: {str(candidate.get('source_url') or '').strip()}\n\n"
                    f"{str(candidate.get('preview_note') or '').strip()}"
                ).strip()

            choice = self._select_from_list(
                label=group_label,
                options=options,
                current_value=current_choice if current_choice in options else options[0],
                option_details=option_details,
            )
            if choice is None:
                return None

            current_choice = cast(str, choice)
            candidate = candidate_by_label.get(current_choice)
            if candidate is None:
                continue

            action = self._select_from_list(
                label=f"{group_label} candidate",
                options=["Preview", "Select", "Back"],
                current_value="Preview",
            )
            if action is None or action == "Back":
                continue
            if action == "Preview":
                preview_path_raw = str(candidate.get("preview_local_path") or "").strip()
                preview_path = Path(preview_path_raw).expanduser().resolve() if preview_path_raw else None
                if preview_path is None or not preview_path.exists():
                    self._set_status(
                        str(candidate.get("preview_note") or "Preview unavailable for this candidate.").strip()
                        or "Preview unavailable for this candidate."
                    )
                    continue
                played = self._play_media_path(preview_path, label=f"{shot_id}-candidate", audio_only=False)
                if not played:
                    self._set_status("Could not play candidate preview.")
                continue
            if action == "Select":
                return int(candidate.get("shortlist_index") or 0)

    def _run_shot_review_prompt(self, project_dir: Path) -> str:
        entries = self._load_shot_review_entries(project_dir)
        if not entries:
            self._set_status("No shot previews are available to review.")
            return "cancel"

        state = self._load_shot_review_state(project_dir)
        current_choice: str | None = None
        current_shot_id: str | None = None
        while True:
            blocked_remaining = 0
            options: list[str] = []
            option_details: dict[str, str] = {}
            entry_by_label: dict[str, dict[str, Any]] = {}
            label_by_shot_id: dict[str, str] = {}
            ordered_entries = sorted(
                entries,
                key=lambda item: (
                    0 if bool((state.get(str(item.get("shot_id") or "")) or {}).get("blocked")) and not bool((state.get(str(item.get("shot_id") or "")) or {}).get("approved")) else 1,
                    str(item.get("scene_id") or ""),
                    int(item.get("shot_index") or 1),
                ),
            )
            for index, entry in enumerate(ordered_entries, start=1):
                shot_id = str(entry.get("shot_id") or "").strip()
                record = state.get(shot_id) or {}
                if bool(record.get("blocked")) and not bool(record.get("approved")):
                    blocked_remaining += 1
                heading = self._trim_tail(str(entry.get("heading") or "").strip(), 40)
                marker = self._shot_review_token(record)
                prefix = f"{index:02d}. [{marker}] " if marker else f"{index:02d}. "
                label = prefix + f"{heading} | shot {int(entry.get('shot_index') or 1)}/{int(entry.get('total_shots') or 1)}"
                options.append(label)
                entry_by_label[label] = entry
                if shot_id:
                    label_by_shot_id[shot_id] = label
                option_details[label] = (
                    f"Objective: {str(entry.get('shot_objective') or '').strip()}\n\n"
                    f"Key info: {str(entry.get('key_info') or '').strip()}\n\n"
                    f"Confidence: {str(entry.get('match_confidence') or 'medium').strip()} | "
                    f"Fallback: {str(entry.get('fallback_level') or 'exact').strip()}"
                )

            if blocked_remaining == 0:
                options.append("Continue")
                option_details["Continue"] = "All blocked shots are approved. Continue to the next stage."

            preferred_choice = (
                label_by_shot_id.get(current_shot_id, "")
                if current_shot_id
                else (current_choice if current_choice in options else "")
            )
            choice = self._select_shot_review_entry(
                label=f"Shot Review ({blocked_remaining} blocked)",
                options=options,
                current_value=preferred_choice if preferred_choice in options else options[0],
                option_details=option_details,
                entry_by_label=entry_by_label,
            )
            if choice is None:
                return "cancel"

            current_choice = cast(str, choice)
            if current_choice == "Continue":
                current_shot_id = None
                return "ready"

            entry = entry_by_label.get(current_choice)
            if entry is None:
                continue
            shot_id = str(entry.get("shot_id") or "").strip()
            current_shot_id = shot_id or current_shot_id
            record = state.setdefault(shot_id, {"approved": False, "blocked": True, "updated_at": None})

            action_choice = "Play"
            while True:
                refreshed_entries = self._load_shot_review_entries(project_dir)
                record = state.setdefault(shot_id, {"approved": False, "blocked": True, "updated_at": None})
                refreshed_entry = next(
                    (
                        item
                        for item in refreshed_entries
                        if str(item.get("shot_id") or "").strip() == shot_id
                    ),
                    entry,
                )
                action = self._select_from_list(
                    label=f"Shot {shot_id}",
                    options=["Play", "Approve", "Regenerate", "Try still image", "Refine keywords"],
                    current_value=action_choice,
                )
                if action is None:
                    break
                action_choice = cast(str, action)
                if action == "Play":
                    preview_path = Path(str(refreshed_entry.get("preview_path") or "").strip()).expanduser().resolve()
                    played = preview_path.exists() and self._play_media_path(
                        preview_path,
                        label=f"shot-{shot_id}",
                        audio_only=False,
                    )
                    if not played:
                        self._set_status("Could not play shot preview.")
                    continue
                if action == "Approve":
                    if bool(record.get("approved")):
                        break
                    record["approved"] = True
                    record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    self._save_shot_review_state(project_dir, state)
                    self._set_status(f"Shot approved: {shot_id}")
                    continue
                if action == "Regenerate":
                    if self._regenerate_shot_entry(project_dir, refreshed_entry, mode="video"):
                        state = self._load_shot_review_state(project_dir)
                        entries = self._load_shot_review_entries(project_dir)
                        current_shot_id = shot_id
                        continue
                    continue
                if action == "Try still image":
                    if self._regenerate_shot_entry(project_dir, refreshed_entry, mode="image"):
                        state = self._load_shot_review_state(project_dir)
                        entries = self._load_shot_review_entries(project_dir)
                        current_shot_id = shot_id
                        continue
                    continue
                if action == "Refine keywords":
                    if self._regenerate_shot_entry(project_dir, refreshed_entry, mode="refine"):
                        state = self._load_shot_review_state(project_dir)
                        entries = self._load_shot_review_entries(project_dir)
                        current_shot_id = shot_id
                        continue
                    continue

    def _regenerate_shot_entry(self, project_dir: Path, entry: dict[str, Any], *, mode: str) -> bool:
        shot_id = str(entry.get("shot_id") or "").strip()
        if not shot_id:
            self._set_status("Shot id is missing.")
            return False

        current_key_info = str(entry.get("key_info") or "").strip()
        current_search_queries = [
            str(item).strip()
            for item in entry.get("search_queries") or []
            if str(item).strip()
        ] if isinstance(entry.get("search_queries"), list) else []
        detail_text = self._trim_tail(
            f"{str(entry.get('heading') or shot_id).strip()} | {current_key_info}",
            80,
        )

        state = self._load_shot_review_state(project_dir)
        record = state.setdefault(shot_id, {"approved": False, "blocked": True, "updated_at": None})
        cycle = self._normalize_regenerate_cycle(record.get("regenerate_cycle"))
        if not cycle.get("search_queries") and current_search_queries:
            cycle["search_queries"] = list(current_search_queries)

        desired_queries = list(cycle.get("search_queries") or current_search_queries)
        if mode == "refine":
            updated_key_info, updated_queries = self._prompt_shot_regenerate_refinement(
                shot_id,
                current_key_info,
                desired_queries,
            )
            if updated_key_info is None:
                return False
            current_key_info = updated_key_info
            desired_queries = list(updated_queries)
            cycle = self._fresh_regenerate_cycle(desired_queries)
            self._save_shot_regenerate_cycle(project_dir, state, shot_id, cycle)

        if not self._prepare_shot_candidates_for_entry(
            project_dir,
            shot_id,
            current_key_info,
            search_queries=desired_queries,
            use_spinner=True,
            detail_text=detail_text,
        ):
            return False
        manifest = self._load_shot_candidate_manifest(project_dir, shot_id) or {}

        if bool(manifest.get("editorial_locked")):
            if mode != "refine":
                self._set_status("Editorial shot is locked. Use Refine keywords for this shot.")
                return False
            command = [
                sys.executable,
                "-m",
                "local_video_mvp.cli",
                "regenerate-shot",
                "--project-dir",
                str(project_dir),
                "--shot-id",
                shot_id,
                "--key-info",
                re.sub(r"\s+", " ", current_key_info.strip()),
                "--verbose",
            ]
            for query in desired_queries:
                command.extend(["--search-query", re.sub(r"\s+", " ", query.strip())])
            code = self._run_command_with_spinner(
                command,
                label="run",
                title="Regenerate Shot",
                message="Rendering updated preview",
                detail_text=detail_text,
            )
            if code != 0:
                error_text = self._project_run_report_error(project_dir)
                if error_text:
                    self._append_log(f"ERROR: Shot regeneration failed for {shot_id}: {error_text}")
                    self._set_status(self._trim_tail(f"Shot regeneration failed: {error_text}", 140))
                else:
                    self._set_status(f"Shot regeneration failed with exit code {code}.")
                return False
            return self._play_regenerated_shot_preview(project_dir, shot_id, fallback_entry=entry)

        requested_media_type = "image" if mode == "image" else "video"
        candidate = self._next_shot_regenerate_candidate(
            manifest,
            cycle,
            media_type=requested_media_type,
        )
        if candidate is None:
            prompt_body = (
                "Reached the end of the still image list. Press Enter to restart from the first image, or Esc to go back."
                if requested_media_type == "image"
                else "Reached the end of the video clip list. Press Enter to restart from the first clip, or Esc to go back."
            )
            if not self._prompt_confirm_enter_escape("Restart Candidate Cycle", prompt_body):
                self._save_shot_regenerate_cycle(project_dir, state, shot_id, cycle)
                self._set_status(
                    "Still image list exhausted. Press Try still image again to restart."
                    if requested_media_type == "image"
                    else "Video clip list exhausted. Press Regenerate again to restart."
                )
                return False
            if requested_media_type == "image":
                cycle["image_tried"] = []
            else:
                cycle["video_tried"] = []
            cycle["exhausted"] = False
            self._save_shot_regenerate_cycle(project_dir, state, shot_id, cycle)
            candidate = self._next_shot_regenerate_candidate(
                manifest,
                cycle,
                media_type=requested_media_type,
            )
            if candidate is None:
                self._set_status("No replacement candidates are available for this shot.")
                return False

        media_type = str(candidate.get("media_type") or "").strip().lower()
        shortlist_index = int(candidate.get("shortlist_index") or -1)
        if shortlist_index < 0:
            self._set_status("Selected shot candidate is invalid.")
            return False

        if media_type == "image":
            tried = list(cycle.get("image_tried") or [])
            if shortlist_index not in tried:
                tried.append(shortlist_index)
            cycle["image_tried"] = tried
            cycle["phase"] = "image"
        else:
            tried = list(cycle.get("video_tried") or [])
            if shortlist_index not in tried:
                tried.append(shortlist_index)
            cycle["video_tried"] = tried
            cycle["phase"] = "video"
        cycle["exhausted"] = False
        cycle["regenerated"] = True
        cycle["search_queries"] = list(desired_queries)
        self._save_shot_regenerate_cycle(project_dir, state, shot_id, cycle)

        command = [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "regenerate-shot",
            "--project-dir",
            str(project_dir),
            "--shot-id",
            shot_id,
            "--key-info",
            re.sub(r"\s+", " ", current_key_info.strip()),
            "--candidate-index",
            str(shortlist_index),
            "--verbose",
        ]
        for query in desired_queries:
            command.extend(["--search-query", re.sub(r"\s+", " ", query.strip())])
        code = self._run_command_with_spinner(
            command,
            label="run",
            title="Regenerate Shot",
            message="Rendering updated preview",
            detail_text=detail_text,
        )
        if code != 0:
            error_text = self._project_run_report_error(project_dir)
            if error_text:
                self._append_log(f"ERROR: Shot regeneration failed for {shot_id}: {error_text}")
                self._set_status(self._trim_tail(f"Shot regeneration failed: {error_text}", 140))
            else:
                self._set_status(f"Shot regeneration failed with exit code {code}.")
            return False
        refreshed_state = self._load_shot_review_state(project_dir)
        self._save_shot_regenerate_cycle(project_dir, refreshed_state, shot_id, cycle)
        return self._play_regenerated_shot_preview(project_dir, shot_id, fallback_entry=entry, media_type=media_type)

    def _play_regenerated_shot_preview(
        self,
        project_dir: Path,
        shot_id: str,
        *,
        fallback_entry: dict[str, Any],
        media_type: str | None = None,
    ) -> bool:
        refreshed_entries = self._load_shot_review_entries(project_dir)
        refreshed_entry = next(
            (item for item in refreshed_entries if str(item.get("shot_id") or "").strip() == shot_id),
            fallback_entry,
        )
        preview_path = Path(str(refreshed_entry.get("preview_path") or "").strip()).expanduser().resolve()
        played = preview_path.exists() and self._play_media_path(preview_path, label=f"shot-{shot_id}", audio_only=False)
        if played:
            label = media_type or "candidate"
            self._set_status(f"Shot regenerated with next {label}: {shot_id}")
        else:
            self._set_status(f"Shot regenerated: {shot_id}")
        return True

    def _prompt_shot_regenerate_refinement(
        self,
        shot_id: str,
        current_key_info: str,
        current_search_queries: list[str],
    ) -> tuple[str | None, list[str]]:
        keywords_value = self._prompt_input(
            f"Shot keywords ({shot_id})",
            ", ".join(current_search_queries),
        )
        if keywords_value is None:
            return None, []
        if not str(keywords_value).strip():
            return current_key_info, list(current_search_queries)
        search_queries = [
            part.strip()
            for part in re.split(r"[,;\n]+", str(keywords_value))
            if part.strip()
        ]
        return current_key_info, search_queries or list(current_search_queries)

    def _maybe_prompt_shot_review(self) -> None:
        with self._lock:
            pending = self._pending_shot_review_prompt
            self._pending_shot_review_prompt = None

        if not isinstance(pending, dict):
            return

        project_dir_raw = str(pending.get("project_dir") or "").strip()
        if not project_dir_raw:
            return
        project_dir = Path(project_dir_raw).expanduser().resolve()
        if not project_dir.exists():
            self._set_status("Shot review workspace was not found.")
            return

        with self._modal_focus():
            outcome = self._run_shot_review_prompt(project_dir)

        if outcome == "ready":
            self._hitl_stage = "preview"
            self._start_run_workflow()
            return

        self._set_status("Shot review closed. Press R to resume.")

    def _scene_review_state_path(self, project_dir: Path) -> Path:
        return project_dir / "review" / "scene_review_state.json"

    def _load_scene_review_state(self, project_dir: Path) -> dict[str, dict[str, Any]]:
        state_path = self._scene_review_state_path(project_dir)
        if not state_path.exists():
            return {}

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse scene review state: {exc}")
            return {}

        scenes = payload.get("scenes") if isinstance(payload, dict) else None
        if isinstance(scenes, dict):
            out: dict[str, dict[str, Any]] = {}
            for scene_id, value in scenes.items():
                scene_key = str(scene_id).strip()
                if not scene_key or not isinstance(value, dict):
                    continue
                out[scene_key] = dict(value)
            return out

        return {}

    def _save_scene_review_state(self, project_dir: Path, state: dict[str, dict[str, Any]]) -> None:
        state_path = self._scene_review_state_path(project_dir)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scenes": state,
        }
        state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def _scene_review_record(self, state: dict[str, dict[str, Any]], scene_id: str) -> dict[str, Any]:
        key = str(scene_id).strip()
        if key not in state:
            state[key] = {
                "text_approved": False,
                "narration_approved": False,
                "clip_approved": False,
                "updated_at": None,
            }
        return state[key]

    def _scene_review_complete(self, record: dict[str, Any]) -> bool:
        return bool(record.get("text_approved") and record.get("narration_approved") and record.get("clip_approved"))

    def _approve_all_pending_scene_reviews(
        self,
        state: dict[str, dict[str, Any]],
        entries: list[dict[str, Any]],
    ) -> int:
        updated = 0
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        for entry in entries:
            scene_id = str(entry.get("scene_id") or "").strip()
            if not scene_id:
                continue

            record = self._scene_review_record(state, scene_id)
            if self._scene_review_complete(record):
                continue

            record["text_approved"] = True
            record["narration_approved"] = True
            record["clip_approved"] = True
            record["updated_at"] = timestamp
            updated += 1

        return updated

    def _has_pending_scene_reviews(self, project_dir: Path) -> bool:
        entries = self._load_scene_review_entries(project_dir)
        if not entries:
            return False

        state = self._load_scene_review_state(project_dir)
        for entry in entries:
            scene_id = str(entry.get("scene_id") or "")
            if not scene_id:
                continue
            record = self._scene_review_record(state, scene_id)
            if not self._scene_review_complete(record):
                return True
        return False

    def _load_scene_review_entries(self, project_dir: Path) -> list[dict[str, Any]]:
        script_path = project_dir / "review" / "script_approved.json"
        if not script_path.exists():
            script_path = project_dir / "script.json"
        if not script_path.exists():
            return []

        try:
            script_payload = json.loads(script_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse script for scene review: {exc}")
            return []

        raw_scenes = script_payload.get("scenes") if isinstance(script_payload, dict) else None
        if not isinstance(raw_scenes, list):
            return []

        timeline_map: dict[str, dict[str, Any]] = {}
        intro_shift = 0.0
        timeline_path = project_dir / "timeline.json"
        if timeline_path.exists():
            try:
                timeline_payload = json.loads(timeline_path.read_text(encoding="utf-8"))
                clips = timeline_payload.get("clips") if isinstance(timeline_payload, dict) else None
                if isinstance(clips, list):
                    for clip in clips:
                        if not isinstance(clip, dict):
                            continue
                        scene_id = str(clip.get("scene_id") or "").strip()
                        if not scene_id:
                            continue
                        if scene_id == "__intro":
                            try:
                                intro_shift = max(0.0, float(clip.get("seconds") or 0.0))
                            except Exception:
                                intro_shift = 0.0
                            continue
                        timeline_map[scene_id] = clip
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"WARN: Could not parse timeline for scene review: {exc}")

        catalog_map: dict[str, dict[str, Any]] = {}
        clip_catalog_path = project_dir / "review" / "clip_catalog.json"
        if clip_catalog_path.exists():
            try:
                catalog_payload = json.loads(clip_catalog_path.read_text(encoding="utf-8"))
                clips = catalog_payload.get("clips") if isinstance(catalog_payload, dict) else None
                if isinstance(clips, list):
                    for item in clips:
                        if not isinstance(item, dict):
                            continue
                        scene_id = str(item.get("scene_id") or "").strip()
                        if scene_id:
                            catalog_map[scene_id] = item
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"WARN: Could not parse clip catalog for scene review: {exc}")

        entries: list[dict[str, Any]] = []
        fallback_preview_cursor = intro_shift
        for index, scene in enumerate(raw_scenes, start=1):
            if not isinstance(scene, dict):
                continue

            scene_id = str(scene.get("scene_id") or f"scene_{index:03d}").strip()
            if not scene_id:
                continue

            clip_name = str(scene.get("clip_name") or "").strip() or scene_id
            heading = str(scene.get("heading") or f"Scene {index}").strip() or f"Scene {index}"
            voiceover = str(scene.get("voiceover") or "").strip()
            search_terms_raw = scene.get("search_terms")
            if isinstance(search_terms_raw, list):
                search_terms = [str(item).strip() for item in search_terms_raw if str(item).strip()]
            else:
                search_terms = []

            try:
                seconds = max(0.2, float(scene.get("seconds") or 0.0))
            except Exception:
                seconds = 0.2

            timeline_clip = timeline_map.get(scene_id, {})
            try:
                preview_start = float(timeline_clip.get("start") or fallback_preview_cursor)
            except Exception:
                preview_start = fallback_preview_cursor

            try:
                preview_end = float(timeline_clip.get("end") or (preview_start + seconds))
            except Exception:
                preview_end = preview_start + seconds
            if preview_end <= preview_start:
                preview_end = preview_start + seconds

            fallback_preview_cursor = preview_end

            source_path = str(timeline_clip.get("source_path") or scene.get("asset_path") or "").strip() or None
            catalog_item = catalog_map.get(scene_id, {})

            try:
                narration_start = float(timeline_clip.get("narration_start"))
            except Exception:
                narration_start = max(0.0, preview_start - intro_shift)
            try:
                narration_end = float(timeline_clip.get("narration_end"))
            except Exception:
                narration_end = max(narration_start + 0.1, preview_end - intro_shift)
            if narration_end <= narration_start:
                narration_end = max(narration_start + 0.1, narration_start + seconds)

            entries.append(
                {
                    "scene_id": scene_id,
                    "clip_name": clip_name,
                    "heading": heading,
                    "voiceover": voiceover,
                    "search_terms": search_terms,
                    "seconds": seconds,
                    "source_path": source_path,
                    "asset_provider": str(catalog_item.get("asset_provider") or scene.get("asset_provider") or "").strip(),
                    "asset_media_type": str(catalog_item.get("asset_media_type") or "").strip(),
                    "asset_width": catalog_item.get("asset_width"),
                    "asset_height": catalog_item.get("asset_height"),
                    "asset_duration_seconds": catalog_item.get("asset_duration_seconds"),
                    "license_name": str(catalog_item.get("license_name") or "").strip(),
                    "license_url": str(catalog_item.get("license_url") or "").strip(),
                    "attribution_required": bool(catalog_item.get("attribution_required")),
                    "attribution_text": str(catalog_item.get("attribution_text") or "").strip(),
                    "restriction_flags": list(catalog_item.get("restriction_flags") or [])
                    if isinstance(catalog_item.get("restriction_flags"), list)
                    else [],
                    "candidate_count": int(catalog_item.get("candidate_count") or 0),
                    "asset_candidates": list(catalog_item.get("candidates") or [])
                    if isinstance(catalog_item.get("candidates"), list)
                    else [],
                    "source_url": str(catalog_item.get("source_url") or "").strip(),
                    "preview_start": preview_start,
                    "preview_end": preview_end,
                    "narration_start": narration_start,
                    "narration_end": narration_end,
                }
            )

        return entries

    def _maybe_prompt_scene_review(self) -> None:
        with self._lock:
            pending = self._pending_scene_review_prompt
            self._pending_scene_review_prompt = None

        if not isinstance(pending, dict):
            return

        project_dir_raw = str(pending.get("project_dir") or "").strip()
        if not project_dir_raw:
            return

        project_dir = Path(project_dir_raw).expanduser().resolve()
        entries = self._load_scene_review_entries(project_dir)
        if not entries:
            self._set_status("No scenes available for review.")
            return

        preview_exists = (project_dir / "review" / "preview.mp4").exists()
        approved_exists = (project_dir / "review" / "script_approved.json").exists()
        state = self._load_scene_review_state(project_dir)
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        for entry in entries:
            scene_id = str(entry.get("scene_id") or "").strip()
            if scene_id:
                record = self._scene_review_record(state, scene_id)
                if approved_exists and not bool(record.get("text_approved")):
                    record["text_approved"] = True
                    if not record.get("updated_at"):
                        record["updated_at"] = timestamp

        self._save_scene_review_state(project_dir, state)
        with self._modal_focus():
            outcome = self._run_scene_review_hub(project_dir, state, preview_exists=preview_exists)
        self._save_scene_review_state(project_dir, state)

        if outcome == "ready":
            self._queue_stage_transition_prompt(
                next_stage="finalize" if preview_exists else "preview",
                title="Scene Review Complete",
                body="All scenes are approved. Finalize now?" if preview_exists else "All scenes are approved. Generate preview now?",
            )
            self._set_status("All scenes approved. Finalize checkpoint pending." if preview_exists else "All scenes approved. Preview checkpoint pending.")
            return

        if outcome in {"force_preview", "force_finalize"}:
            self._hitl_stage = "finalize" if outcome == "force_finalize" else "preview"
            self._mark_command_start(workflow_kind="run")
            self._set_running(True)
            workspace_name = project_dir.name
            self._set_status(f"Starting {self._display_stage_name(self._hitl_stage)} stage: {workspace_name}")
            self._worker = threading.Thread(target=self._run_workflow, daemon=True)
            self._worker.start()
            return

        self._set_status("Scene review closed. Press R to resume.")

    def _run_scene_review_hub(
        self,
        project_dir: Path,
        state: dict[str, dict[str, Any]],
        *,
        preview_exists: bool = False,
    ) -> str:
        if self._stdscr is None:
            return "cancel"

        stdscr = self._stdscr
        cursor = -1

        while True:
            entries = self._load_scene_review_entries(project_dir)
            if not entries:
                self._set_status("No scenes available for review.")
                return "cancel"

            scene_ids = [str(item.get("scene_id") or "").strip() for item in entries]
            pending_indexes: list[int] = []
            for idx, scene_id in enumerate(scene_ids):
                if not scene_id:
                    continue
                record = self._scene_review_record(state, scene_id)
                if not self._scene_review_complete(record):
                    pending_indexes.append(idx)

            if not pending_indexes:
                return "ready"

            if cursor < 0 or cursor >= len(entries):
                cursor = pending_indexes[0]

            self._set_status(f"Scene review: {len(pending_indexes)} scene(s) pending.")
            self._save_scene_review_state(project_dir, state)

            while True:
                self._draw()
                height, width = stdscr.getmaxyx()

                if width < 42 or height < 10:
                    self._set_status("Terminal is too small for scene review. Resize and press R to continue.")
                    return "cancel"

                modal_width = max(36, width - 2)
                max_modal_height = max(8, height - 2)
                list_rows = max(1, max_modal_height - 4)
                modal_height = min(max_modal_height, list_rows + 4)

                top = max(0, (height - modal_height) // 2)
                left = max(0, (width - modal_width) // 2)

                win = curses.newwin(modal_height, modal_width, top, left)
                win.keypad(True)
                win.nodelay(False)
                win.timeout(-1)

                start_index = 0
                if cursor >= list_rows:
                    start_index = cursor - list_rows + 1

                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title = self._trim_tail(
                    f" Scene Review Hub ({len(pending_indexes)} pending) ",
                    max(1, modal_width - 4),
                )
                footer_action = "G finalize" if preview_exists else "G generate"
                footer = f"Up/Down move | Enter edit | {footer_action} | Esc back"
                try:
                    win.addstr(0, 2, title, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(footer, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                for row in range(list_rows):
                    index = start_index + row
                    if index >= len(entries):
                        break

                    entry = entries[index]
                    scene_id = str(entry.get("scene_id") or "").strip()
                    record = self._scene_review_record(state, scene_id) if scene_id else {}
                    status = self._scene_review_status_token(record)
                    heading = str(entry.get("heading") or scene_id or f"Scene {index + 1}").strip()
                    clip_name = str(entry.get("clip_name") or scene_id or "").strip()
                    voiceover = re.sub(r"\s+", " ", str(entry.get("voiceover") or "").strip())
                    snippet = self._trim_tail(voiceover, 36)
                    line = self._trim_tail(
                        f"{index + 1:02d}. [{status}] {heading} | {clip_name} | {snippet}",
                        modal_width - 3,
                    )
                    attr = curses.A_REVERSE if index == cursor else 0
                    try:
                        win.addstr(1 + row, 1, line, attr)
                    except curses.error:
                        pass

                win.refresh()
                key = win.getch()

                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    cursor = (cursor - 1) % len(entries)
                    break
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    cursor = (cursor + 1) % len(entries)
                    break
                if key in (10, 13, curses.KEY_ENTER):
                    entry = entries[cursor]
                    outcome = self._review_scene_from_hub(
                        project_dir,
                        entry,
                        state,
                        scene_index=cursor + 1,
                        scene_total=len(entries),
                    )
                    self._save_scene_review_state(project_dir, state)
                    if outcome == "cancel":
                        return "cancel"
                    break
                if key in (ord("g"), ord("G")):
                    confirmed = self._prompt_yes_no(
                        title="Finalize Now" if preview_exists else "Generate Preview",
                        body="Approve all pending scenes and finalize now?"
                        if preview_exists
                        else "Approve all pending scenes and generate preview now?",
                        default_yes=True,
                    )
                    if not confirmed:
                        break

                    approved_count = self._approve_all_pending_scene_reviews(state, entries)
                    self._save_scene_review_state(project_dir, state)
                    self._append_log(f"Auto-approved {approved_count} pending scene(s) from review hub.")
                    return "force_finalize" if preview_exists else "force_preview"
                if key == 27:
                    return "cancel"

    def _scene_review_status_token(self, record: dict[str, Any]) -> str:
        text_flag = "T" if bool(record.get("text_approved")) else "-"
        narration_flag = "N" if bool(record.get("narration_approved")) else "-"
        clip_flag = "C" if bool(record.get("clip_approved")) else "-"
        return f"{text_flag}{narration_flag}{clip_flag}"

    def _review_scene_from_hub(
        self,
        project_dir: Path,
        entry: dict[str, Any],
        state: dict[str, dict[str, Any]],
        *,
        scene_index: int,
        scene_total: int,
    ) -> str:
        scene_id = str(entry.get("scene_id") or "").strip()
        if not scene_id:
            return "next"

        record = self._scene_review_record(state, scene_id)
        clip_name = str(entry.get("clip_name") or scene_id)

        updated_text = self._prompt_multiline_input(
            label=f"Scene {scene_index}/{scene_total} text ({clip_name})",
            current_value=str(entry.get("voiceover") or ""),
        )
        if updated_text is not None:
            current_text = str(entry.get("voiceover") or "").strip()
            next_text = updated_text.strip()
            changed = next_text != current_text
            if changed:
                self._update_scene_voiceover(project_dir, scene_id=scene_id, new_voiceover=next_text)
                entry["voiceover"] = next_text
                record["narration_approved"] = False
                record["clip_approved"] = False
                self._append_log(f"Updated scene text for {scene_id}.")

            record["text_approved"] = True
            record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        elif not bool(record.get("text_approved")):
            self._set_status("Scene text is not approved yet. Open the scene and save text to continue.")
            return "back"

        scene_sample_path: Path | None = None
        while not bool(record.get("narration_approved")):
            action = self._select_from_list(
                label=f"Scene {scene_index} Narration",
                options=["Approve narration", "Play narration", "Regenerate sample"],
                current_value="Approve narration",
            )
            if action is None:
                return "back"
            if action == "Regenerate sample":
                scene_sample_path = self._synthesize_scene_narration_sample(project_dir, scene_id, str(entry.get("voiceover") or ""))
                if scene_sample_path is not None:
                    self._play_media_path(scene_sample_path, label=f"scene-{scene_id}-narration", audio_only=True)
                continue
            if action == "Play narration":
                if scene_sample_path is None or not scene_sample_path.exists():
                    scene_sample_path = self._synthesize_scene_narration_sample(project_dir, scene_id, str(entry.get("voiceover") or ""))
                played = self._play_scene_narration(project_dir, entry, sample_path=scene_sample_path)
                if not played:
                    self._set_status("Could not play narration preview for this scene.")
                continue

            record["narration_approved"] = True
            record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

        while not bool(record.get("clip_approved")):
            action = self._select_from_list(
                label=f"Scene {scene_index} Clip",
                options=[
                    "Approve clip",
                    "Play clip",
                    "Choose stored candidate",
                    "View asset candidates",
                    "Replace clip (same keywords)",
                    "Replace clip (new keywords)",
                ],
                current_value="Approve clip",
            )
            if action is None:
                return "back"
            if action == "Approve clip":
                record["clip_approved"] = True
                record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                continue
            if action == "Play clip":
                played = self._play_scene_clip(project_dir, entry)
                if not played:
                    self._set_status("Could not play clip preview for this scene.")
                continue
            if action == "Choose stored candidate":
                replaced = self._choose_scene_asset_candidate(project_dir, entry, clip_name=clip_name)
                if replaced:
                    updated = self._load_scene_review_entries(project_dir)
                    for item in updated:
                        if str(item.get("scene_id") or "").strip() == scene_id:
                            entry.update(item)
                            break
                    record["clip_approved"] = False
                continue
            if action == "View asset candidates":
                self._show_scene_asset_candidates(entry, scene_index=scene_index, scene_total=scene_total)
                continue
            if action == "Replace clip (same keywords)":
                replaced = self._replace_single_scene_clip(project_dir, clip_name=clip_name, replacement_keywords=None)
                if replaced:
                    updated = self._load_scene_review_entries(project_dir)
                    for item in updated:
                        if str(item.get("scene_id") or "").strip() == scene_id:
                            entry.update(item)
                            break
                    record["clip_approved"] = False
                continue
            if action == "Replace clip (new keywords)":
                keyword_default = ", ".join(self.config.asset_keywords)
                keyword_value = self._prompt_input("Replacement keywords", keyword_default)
                if keyword_value is None:
                    continue
                if not isinstance(keyword_value, str):
                    continue
                parsed = [part.strip() for part in re.split(r"[,;\n]+", keyword_value) if part.strip()]
                normalized = self._normalize_asset_keywords(parsed)
                if not normalized:
                    self._set_status("No replacement keywords provided.")
                    continue
                replaced = self._replace_single_scene_clip(project_dir, clip_name=clip_name, replacement_keywords=normalized)
                if replaced:
                    self.config.asset_keywords = normalized
                    updated = self._load_scene_review_entries(project_dir)
                    for item in updated:
                        if str(item.get("scene_id") or "").strip() == scene_id:
                            entry.update(item)
                            break
                    record["clip_approved"] = False
                continue

        self._append_log(f"Scene approved: {scene_id} ({clip_name})")
        return "next"

    def _show_scene_asset_candidates(
        self,
        entry: dict[str, Any],
        *,
        scene_index: int,
        scene_total: int,
    ) -> None:
        candidates_raw = entry.get("asset_candidates")
        candidates = [item for item in candidates_raw if isinstance(item, dict)] if isinstance(candidates_raw, list) else []

        current_provider = str(entry.get("asset_provider") or "").strip() or "placeholder"
        current_media_type = str(entry.get("asset_media_type") or "").strip() or "unknown"
        current_width = self._safe_int(entry.get("asset_width"))
        current_height = self._safe_int(entry.get("asset_height"))
        current_duration = self._safe_float(entry.get("asset_duration_seconds"), default=0.0)
        current_resolution = (
            f"{current_width}x{current_height}"
            if current_width is not None and current_height is not None
            else "unknown resolution"
        )
        current_duration_text = f"{current_duration:.1f}s" if current_duration > 0.0 else "n/a"
        license_name = str(entry.get("license_name") or "").strip() or "unknown license"
        attribution_required = bool(entry.get("attribution_required"))
        attribution_text = str(entry.get("attribution_text") or "").strip()
        restriction_flags = [
            str(flag).strip()
            for flag in entry.get("restriction_flags") or []
            if str(flag).strip()
        ] if isinstance(entry.get("restriction_flags"), list) else []

        lines = [
            f"Scene {scene_index}/{scene_total}",
            f"Current asset: {current_provider} | {current_media_type} | {current_resolution} | {current_duration_text}",
            f"License: {license_name} | Attribution required: {'yes' if attribution_required else 'no'}",
        ]
        if attribution_text:
            lines.append(f"Attribution text: {attribution_text}")
        if restriction_flags:
            lines.append(f"Restrictions: {', '.join(restriction_flags)}")

        source_url = str(entry.get("source_url") or "").strip()
        if source_url:
            lines.append(f"Source: {source_url}")

        if not candidates:
            lines.append("")
            lines.append("No ranked candidate shortlist was stored for this scene yet.")
        else:
            lines.append("")
            lines.append(f"Stored candidates: {len(candidates)}")
            for index, candidate in enumerate(candidates, start=1):
                provider = str(candidate.get("source_platform") or "unknown").strip()
                media_type = str(candidate.get("media_type") or "unknown").strip()
                width = self._safe_int(candidate.get("width"))
                height = self._safe_int(candidate.get("height"))
                resolution = f"{width}x{height}" if width is not None and height is not None else "unknown resolution"
                duration = self._safe_float(candidate.get("duration_seconds"), default=0.0)
                duration_text = f"{duration:.1f}s" if duration > 0.0 else "n/a"
                ranking_score = self._safe_float(candidate.get("ranking_score"), default=0.0)
                quality_score = self._safe_float(candidate.get("quality_score"), default=0.0)
                query = str(candidate.get("query") or "").strip()
                description = str(candidate.get("description") or "").strip()
                candidate_license = str(candidate.get("license_name") or "").strip() or "unknown license"
                candidate_attribution = bool(candidate.get("attribution_required"))
                candidate_attribution_text = str(candidate.get("attribution_text") or "").strip()
                candidate_restrictions = [
                    str(flag).strip()
                    for flag in candidate.get("restriction_flags") or []
                    if str(flag).strip()
                ] if isinstance(candidate.get("restriction_flags"), list) else []
                selected = bool(candidate.get("selected"))

                lines.append(
                    f"{index:02d}. {'selected' if selected else 'candidate'} | "
                    f"{provider} | {media_type} | {resolution} | {duration_text} | "
                    f"rank {ranking_score:.2f} | quality {quality_score:.2f}"
                )
                if query:
                    lines.append(f"Query: {query}")
                if description:
                    lines.append(f"Description: {description}")
                lines.append(
                    f"License: {candidate_license} | Attribution required: {'yes' if candidate_attribution else 'no'}"
                )
                if candidate_attribution_text:
                    lines.append(f"Attribution text: {candidate_attribution_text}")
                if candidate_restrictions:
                    lines.append(f"Restrictions: {', '.join(candidate_restrictions)}")
                candidate_source = str(candidate.get("source_url") or "").strip()
                if candidate_source:
                    lines.append(f"Source: {candidate_source}")
                lines.append("")

        body = "\n".join(lines).strip()
        self._show_paginated_text_modal("Asset Candidates", body)

    def _asset_selection_overrides_path(self, project_dir: Path) -> Path:
        return project_dir / "review" / "asset_selection_overrides.json"

    def _load_asset_selection_overrides(self, project_dir: Path) -> dict[str, dict[str, Any]]:
        path = self._asset_selection_overrides_path(project_dir)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse asset selection overrides: {exc}")
            return {}

        scenes = payload.get("scenes") if isinstance(payload, dict) else None
        if not isinstance(scenes, dict):
            return {}

        overrides: dict[str, dict[str, Any]] = {}
        for raw_scene_id, raw_value in scenes.items():
            scene_id = str(raw_scene_id).strip()
            if not scene_id or not isinstance(raw_value, dict):
                continue
            overrides[scene_id] = dict(raw_value)
        return overrides

    def _save_asset_selection_overrides(self, project_dir: Path, overrides: dict[str, dict[str, Any]]) -> None:
        path = self._asset_selection_overrides_path(project_dir)
        if not overrides:
            path.unlink(missing_ok=True)
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scenes": {scene_id: overrides[scene_id] for scene_id in sorted(overrides)},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def _choose_scene_asset_candidate(self, project_dir: Path, entry: dict[str, Any], *, clip_name: str) -> bool:
        candidates_raw = entry.get("asset_candidates")
        candidates = [item for item in candidates_raw if isinstance(item, dict)] if isinstance(candidates_raw, list) else []
        if not candidates:
            self._set_status("No stored asset candidates are available for this scene.")
            return False

        scene_id = str(entry.get("scene_id") or "").strip()
        if not scene_id:
            self._set_status("Scene id is missing for candidate selection.")
            return False

        options: list[str] = []
        option_details: dict[str, str] = {}
        current_value: str | None = None
        option_map: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates, start=1):
            provider = str(candidate.get("source_platform") or "unknown").strip()
            media_type = str(candidate.get("media_type") or "unknown").strip()
            width = self._safe_int(candidate.get("width"))
            height = self._safe_int(candidate.get("height"))
            resolution = f"{width}x{height}" if width is not None and height is not None else "unknown resolution"
            duration = self._safe_float(candidate.get("duration_seconds"), default=0.0)
            duration_text = f"{duration:.1f}s" if duration > 0.0 else "n/a"
            ranking_score = self._safe_float(candidate.get("ranking_score"), default=0.0)
            label = f"{index:02d}. {provider} | {media_type} | {resolution} | {duration_text} | rank {ranking_score:.2f}"
            options.append(label)
            option_map[label] = candidate

            description = str(candidate.get("description") or "").strip()
            query = str(candidate.get("query") or "").strip()
            license_name = str(candidate.get("license_name") or "").strip() or "unknown license"
            attribution_required = bool(candidate.get("attribution_required"))
            attribution_text = str(candidate.get("attribution_text") or "").strip()
            restriction_flags = [
                str(flag).strip()
                for flag in candidate.get("restriction_flags") or []
                if str(flag).strip()
            ] if isinstance(candidate.get("restriction_flags"), list) else []
            details_parts = [
                f"License: {license_name}",
                f"Attribution: {'yes' if attribution_required else 'no'}",
            ]
            if query:
                details_parts.append(f"Query: {query}")
            if description:
                details_parts.append(f"Description: {description}")
            if attribution_text:
                details_parts.append(f"Credit text: {attribution_text}")
            if restriction_flags:
                details_parts.append(f"Restrictions: {', '.join(restriction_flags)}")
            option_details[label] = " | ".join(details_parts)

            if bool(candidate.get("selected")) and current_value is None:
                current_value = label

        selected_label = self._select_from_list(
            label="Choose Stored Candidate",
            options=options,
            current_value=current_value or options[0],
            option_details=option_details,
            marked_value=current_value,
        )
        if selected_label is None:
            return False

        selected_candidate = option_map.get(selected_label)
        if not isinstance(selected_candidate, dict):
            self._set_status("Selected candidate could not be resolved.")
            return False

        if bool(selected_candidate.get("selected")):
            self._set_status("That candidate is already active for this scene.")
            return False

        overrides = self._load_asset_selection_overrides(project_dir)
        selected_payload = {key: value for key, value in selected_candidate.items() if key != "selected"}
        overrides[scene_id] = selected_payload
        self._save_asset_selection_overrides(project_dir, overrides)
        self._append_log(
            f"Queued stored candidate override for {scene_id}: "
            f"{selected_payload.get('source_platform', 'unknown')} / {selected_payload.get('media_type', 'unknown')}"
        )
        return self._replace_single_scene_clip(project_dir, clip_name=clip_name, replacement_keywords=None)

    def _update_scene_voiceover(
        self,
        project_dir: Path,
        *,
        scene_id: str,
        new_voiceover: str,
        write_approved: bool = True,
    ) -> None:
        approved_path = project_dir / "review" / "script_approved.json"
        script_path = project_dir / "script.json"
        source_path = approved_path if write_approved and approved_path.exists() else script_path
        if not source_path.exists():
            raise RuntimeError(f"Script file not found for scene edit: {source_path}")

        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Script JSON is invalid for scene edit")

        scenes = payload.get("scenes")
        if not isinstance(scenes, list):
            raise RuntimeError("Script JSON has no scenes list")

        updated = False
        for item in scenes:
            if not isinstance(item, dict):
                continue
            item_scene_id = str(item.get("scene_id") or "").strip()
            if item_scene_id != scene_id:
                continue
            item["voiceover"] = new_voiceover
            updated = True
            break

        if not updated:
            raise RuntimeError(f"Scene not found for edit: {scene_id}")

        serialized = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
        script_path.write_text(serialized, encoding="utf-8")
        if write_approved:
            approved_path.parent.mkdir(parents=True, exist_ok=True)
            approved_path.write_text(serialized, encoding="utf-8")

    def _replace_single_scene_clip(
        self,
        project_dir: Path,
        *,
        clip_name: str,
        replacement_keywords: list[str] | None,
    ) -> bool:
        self._mark_command_start(workflow_kind="replace")
        self._set_running(True)
        try:
            command = self._build_replace_clips_command(
                [clip_name],
                project_dir=project_dir,
                asset_keywords=replacement_keywords,
            )
            code = self._run_and_stream(command, label="replace")
            if code != 0:
                queued_unique_prompt = self._queue_unique_asset_prompt_from_run_report()
                if queued_unique_prompt:
                    self._set_status("Replacement paused: broaden asset keywords and retry.")
                else:
                    self._set_status(f"Replacement failed with exit code {code}.")
                return False

            inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
            if inspect_code != 0:
                self._set_status(f"Replacement succeeded but inspect failed with exit code {inspect_code}.")
            else:
                self._set_status("Replacement succeeded.")
            return True
        finally:
            self._mark_command_stop()
            self._set_running(False)

    def _show_paginated_text_modal(self, title: str, body: str) -> None:
        if self._stdscr is None:
            return

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            modal_width = min(max(70, min(width - 2, 120)), max(22, width - 2))
            modal_height = min(max(12, height - 4), max(8, height - 2))
            if modal_width < 22 or modal_height < 8:
                return

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            content_width = max(12, modal_width - 4)
            wrapped_lines: list[str] = []
            for raw_line in str(body).splitlines() or [""]:
                line = raw_line.rstrip()
                if not line:
                    wrapped_lines.append("")
                    continue
                wrapped = textwrap.wrap(line, width=content_width, replace_whitespace=False, drop_whitespace=False)
                if not wrapped:
                    wrapped_lines.append("")
                else:
                    wrapped_lines.extend(wrapped)

            view_rows = max(1, modal_height - 4)
            offset = 0

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                page = 1 + (offset // max(1, view_rows))
                page_total = max(1, math.ceil(len(wrapped_lines) / max(1, view_rows)))
                title_text = self._trim_tail(f" {title} [{page}/{page_total}] ", max(1, modal_width - 4))
                footer = "Up/Down scroll | Esc back"
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(footer, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                for row in range(view_rows):
                    idx = offset + row
                    if idx >= len(wrapped_lines):
                        break
                    line = self._trim_tail(wrapped_lines[idx], modal_width - 4)
                    try:
                        win.addstr(1 + row, 2, line)
                    except curses.error:
                        pass

                win.refresh()
                key = win.getch()
                if key in (10, 13, curses.KEY_ENTER, 27):
                    return
                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    offset = max(0, offset - 1)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    offset = min(max(0, len(wrapped_lines) - view_rows), offset + 1)
                    continue
                if key == curses.KEY_NPAGE:
                    offset = min(max(0, len(wrapped_lines) - view_rows), offset + view_rows)
                    continue
                if key == curses.KEY_PPAGE:
                    offset = max(0, offset - view_rows)
                    continue

    def _prompt_multiline_input(self, label: str, current_value: str) -> str | None:
        if self._stdscr is None:
            return None

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            modal_width = min(max(72, width - 8), max(24, width - 2))
            modal_height = min(max(14, height - 6), max(10, height - 2))
            if modal_width < 24 or modal_height < 10:
                return None

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            box_height = modal_height - 4
            box_width = modal_width - 4
            text_win = curses.newwin(box_height, box_width, top + 2, left + 2)
            text_win.keypad(True)

            initial_lines = (current_value or "").splitlines() or [""]
            prefilled_lines: list[str] = []
            wrap_width = max(1, box_width - 1)
            for line in initial_lines:
                if not line:
                    prefilled_lines.append("")
                    continue

                wrapped = textwrap.wrap(
                    line,
                    width=wrap_width,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
                if wrapped:
                    prefilled_lines.extend(wrapped)
                else:
                    prefilled_lines.append("")

            if not prefilled_lines:
                prefilled_lines = [""]

            for row, line in enumerate(prefilled_lines[:box_height]):
                safe_line = line[:wrap_width]
                try:
                    text_win.addstr(row, 0, safe_line)
                except curses.error:
                    pass

            textbox = textpad.Textbox(text_win, insert_mode=True)
            cancelled = False

            def validator(ch: int) -> int:
                nonlocal cancelled
                if ch == 27:
                    cancelled = True
                    return 7
                if ch == curses.KEY_ENTER:
                    return 7
                return ch

            try:
                curses.curs_set(1)
            except curses.error:
                pass

            try:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {label} ", max(1, modal_width - 4))
                help_text = self._multiline_submit_help_text()
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass
                win.refresh()

                value = textbox.edit(validator)
                if cancelled:
                    return None

                cleaned = self._normalize_scene_voiceover_text(value, wrap_width=wrap_width)
                if not cleaned:
                    return None
                return cleaned
            finally:
                try:
                    curses.curs_set(0)
                except curses.error:
                    pass

    def _multiline_submit_help_text(self) -> str:
        return "Ctrl-Enter submit | Esc back"

    def _normalize_scene_voiceover_text(self, raw_text: str, *, wrap_width: int | None = None) -> str:
        text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")

        paragraphs: list[str] = []
        paragraph_text = ""
        previous_raw_line: str | None = None

        for raw_line in lines:
            if not raw_line.strip():
                if paragraph_text:
                    paragraphs.append(paragraph_text.strip())
                    paragraph_text = ""
                previous_raw_line = None
                continue

            line_text = re.sub(r"\s+", " ", raw_line.strip())
            if not paragraph_text:
                paragraph_text = line_text
                previous_raw_line = raw_line
                continue

            join_without_space = False
            if wrap_width is not None and previous_raw_line is not None:
                prev_raw = previous_raw_line
                prev_len = len(prev_raw.rstrip("\n\r"))
                prev_trimmed = prev_raw.rstrip()
                if (
                    prev_len >= max(1, wrap_width)
                    and not prev_raw.endswith(" ")
                    and prev_trimmed
                    and prev_trimmed[-1].isalnum()
                    and line_text
                    and line_text[0].isalnum()
                ):
                    join_without_space = True

            separator = "" if join_without_space else " "
            paragraph_text = (paragraph_text + separator + line_text).strip()
            previous_raw_line = raw_line

        if paragraph_text:
            paragraphs.append(paragraph_text.strip())

        return "\n\n".join(part for part in paragraphs if part)

    def _pipeline_for_project(self, project_dir: Path) -> VideoPipeline:
        width, height = _parse_resolution(self._normalize_resolution_value(self.config.resolution))
        outro_text, outro_tagline, outro_spoken_text = self._resolved_channel_outro_copy()
        config = PipelineConfig(
            prompt=self.config.prompt,
            project_dir=project_dir,
            asset_keywords=list(self.config.asset_keywords),
            minutes=max(1, int(self.config.minutes)),
            width=width,
            height=height,
            fps=30,
            script_engine="template",
            ollama_model="qwen2.5:14b",
            require_ollama=False,
            script_tone=self.config.script_tone,
            target_audience=self.config.target_audience,
            hook_style=self.config.hook_style,
            narrative_mode=self.config.narrative_mode,
            example_density=self.config.example_density,
            tts_engine=self.config.tts_engine,
            piper_voice_id=(self.config.piper_voice_id or None),
            piper_speaker_id=self.config.piper_speaker_id,
            caption_engine="heuristic",
            caption_style="engagement",
            burn_subtitles=bool(self.config.burn_subtitles),
            subtitle_preset=self._normalized_subtitle_preset(),
            subtitle_position=self._normalized_subtitle_position(),
            subtitle_accent_color=self._normalized_subtitle_accent_color(),
            subtitle_box_color=self._normalized_subtitle_box_color(),
            caption_font_scale=self._normalized_caption_font_scale(),
            subtitle_bold=self._normalized_subtitle_bold(),
            subtitle_outline=self._normalized_subtitle_outline(),
            strict_commercial_safe=True,
            enable_pexels_provider=bool(self.config.enable_pexels_provider),
            enable_pixabay_provider=bool(self.config.enable_pixabay_provider),
            enable_coverr_provider=bool(self.config.enable_coverr_provider),
            enable_vecteezy_provider=bool(self.config.enable_vecteezy_provider),
            pexels_api_key=self._stock_api_keys.get("PEXELS_API_KEY"),
            pixabay_api_key=self._stock_api_keys.get("PIXABAY_API_KEY"),
            coverr_api_key=self._stock_api_keys.get("COVERR_API_KEY"),
            coverr_app_id=self._stock_api_keys.get("COVERR_APP_ID"),
            vecteezy_account_id=self._stock_api_keys.get("VECTEEZY_ACCOUNT_ID"),
            vecteezy_api_key=self._stock_api_keys.get("VECTEEZY_API_KEY"),
            require_external_assets=True,
            allow_image_assets=bool(self.config.allow_image_assets),
            allow_attribution_required_assets=bool(self.config.allow_attribution_required_assets),
            asset_mode=self._normalized_asset_mode(),
            video_effects="clean",
            image_motion_style=self._normalized_image_motion_style(),
            include_intro=bool(self.config.include_intro),
            include_outro=True,
            intro_seconds=2.8,
            outro_seconds=4.2,
            outro_text=outro_text,
            outro_spoken_text=outro_spoken_text,
            bookend_style="corner-fade",
            outro_tagline=outro_tagline,
            voice_profile=self.config.voice_profile,
            voice_speed=self.config.voice_speed,
            melo_language=self.config.melo_language,
            melo_speaker=self.config.melo_speaker,
            kokoro_lang_code=normalize_kokoro_lang_code(self.config.kokoro_lang_code),
            kokoro_voice=(str(self.config.kokoro_voice).strip() or default_kokoro_voice(self.config.kokoro_lang_code)),
            script_language=self.config.script_language,
            max_scenes=40,
            min_scene_seconds=5.0,
            verbose=True,
        )
        _apply_default_brand_bookends(config)
        return VideoPipeline(config)

    def _synthesize_scene_narration_sample(self, project_dir: Path, scene_id: str, text: str) -> Path | None:
        try:
            pipeline = self._pipeline_for_project(project_dir)
            report = pipeline.synthesize_scene_narration_preview(scene_id=scene_id, text=text)
            wav_path = Path(str(report.get("wav_path") or "")).expanduser().resolve()
            if wav_path.exists():
                self._append_log(f"Narration sample ready: {wav_path}")
                return wav_path
            self._append_log("WARN: Scene narration sample path was not created.")
            return None
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Failed to generate scene narration sample: {exc}")
            return None

    def _play_scene_narration(self, project_dir: Path, entry: dict[str, Any], sample_path: Path | None = None) -> bool:
        if sample_path is not None and sample_path.exists():
            return self._play_media_path(sample_path, label="scene-narration-sample", audio_only=True)

        narration_path = project_dir / "narration.wav"
        if not narration_path.exists():
            self._append_log(f"WARN: narration.wav not found for scene preview: {narration_path}")
            return False

        start = self._safe_float(entry.get("narration_start"), default=0.0)
        end = self._safe_float(entry.get("narration_end"), default=0.0)
        return self._play_media_path(
            narration_path,
            label=f"scene-{entry.get('scene_id')}-narration",
            start_seconds=start,
            end_seconds=end,
            audio_only=True,
        )

    def _safe_float(self, raw_value: Any, *, default: float = 0.0) -> float:
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return float(default)

    def _safe_int(self, raw_value: Any) -> int | None:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    def _play_scene_clip(self, project_dir: Path, entry: dict[str, Any]) -> bool:
        source_path_raw = str(entry.get("source_path") or "").strip()
        if source_path_raw:
            source_path = Path(source_path_raw).expanduser().resolve()
            if source_path.exists():
                return self._play_media_path(source_path, label=f"scene-{entry.get('scene_id')}-source", audio_only=False)

        preview_path = project_dir / "review" / "preview.mp4"
        start = self._safe_float(entry.get("preview_start"), default=0.0)
        end = self._safe_float(entry.get("preview_end"), default=0.0)
        if preview_path.exists():
            return self._play_media_path(
                preview_path,
                label=f"scene-{entry.get('scene_id')}-preview",
                start_seconds=start,
                end_seconds=end,
                audio_only=False,
            )

        self._append_log(f"WARN: No playable clip source found for scene {entry.get('scene_id')}.")
        return False

    def _play_project_preview(self, project_dir: Path) -> bool:
        preview_path = project_dir / "review" / "preview.mp4"
        if not preview_path.exists():
            self._append_log(f"WARN: Preview video not found: {preview_path}")
            return False

        return self._play_media_path(preview_path, label=f"preview-{project_dir.name}", audio_only=False)

    def _video_backend_candidates(self) -> list[str]:
        term = (os.environ.get("TERM") or "").lower()
        term_program = (os.environ.get("TERM_PROGRAM") or "").lower()

        ordered: list[str] = []
        if os.environ.get("KITTY_WINDOW_ID") or "kitty" in term or "ghostty" in term or "ghostty" in term_program:
            ordered.append("kitty")
        if "iterm" in term_program or "wezterm" in term_program:
            ordered.append("sixel")
        ordered.append("tct")

        dedup: list[str] = []
        seen: set[str] = set()
        for item in ordered:
            if item in seen:
                continue
            seen.add(item)
            dedup.append(item)
        return dedup

    def _mpv_supported_terminal_vos(self) -> set[str] | None:
        if self._mpv_vo_probe_attempted:
            cached = self._mpv_supported_vos
            return set(cached) if cached is not None else None

        self._mpv_vo_probe_attempted = True
        mpv_bin = self._resolve_mpv_binary()
        if mpv_bin is None:
            self._mpv_supported_vos = set()
            return set()

        try:
            completed = subprocess.run(
                [mpv_bin, "--no-config", "--vo=help"],
                cwd=str(self._repo_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=6,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not probe mpv backends: {exc}")
            self._mpv_supported_vos = None
            return None

        raw = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        detected: set[str] = set()
        for token in re.split(r"[^a-z0-9_-]+", raw.lower()):
            value = token.strip()
            if value in {"kitty", "sixel", "tct"}:
                detected.add(value)

        self._mpv_supported_vos = detected
        return set(detected)

    def _resolved_video_backends(self) -> list[str]:
        candidates = self._video_backend_candidates()
        supported = self._mpv_supported_terminal_vos()
        if supported is None:
            return candidates

        filtered = [backend for backend in candidates if backend in supported]
        return filtered

    def _play_media_path(
        self,
        media_path: Path,
        *,
        label: str,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        audio_only: bool,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        target = media_path.expanduser().resolve()
        if not target.exists():
            self._append_log(f"WARN: Media file not found: {target}")
            return False

        mpv_bin = self._resolve_mpv_binary()

        if audio_only:
            if mpv_bin is not None:
                command = [
                    mpv_bin,
                    "--no-config",
                    "--really-quiet",
                    "--no-video",
                    "--no-terminal",
                    "--force-window=no",
                    "--keep-open=no",
                ]
                if start_seconds is not None:
                    command.append(f"--start={max(0.0, float(start_seconds)):.3f}")
                if end_seconds is not None:
                    end_value = max(0.0, float(end_seconds))
                    if start_seconds is None or end_value > max(0.0, float(start_seconds)):
                        command.append(f"--end={end_value:.3f}")
                command.append(str(target))
                code = self._run_inline_subprocess(
                    command,
                    label=f"mpv-{label}",
                    timeout_seconds=180,
                    cancel_event=cancel_event,
                )
                return code == 0

            ffplay_bin = shutil.which("ffplay")
            if ffplay_bin is not None:
                command = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-nostats", "-hide_banner", "-nostdin"]
                start = max(0.0, float(start_seconds or 0.0))
                if start > 0.0:
                    command.extend(["-ss", f"{start:.3f}"])

                if end_seconds is not None:
                    end = max(0.0, float(end_seconds))
                    if end > start:
                        command.extend(["-t", f"{(end - start):.3f}"])

                command.append(str(target))
                code = self._run_inline_subprocess(
                    command,
                    label=f"ffplay-{label}",
                    timeout_seconds=180,
                    cancel_event=cancel_event,
                )
                return code == 0

            if sys.platform == "darwin" and shutil.which("afplay") is not None:
                if start_seconds is not None or end_seconds is not None:
                    self._append_log("WARN: afplay fallback does not support scene seek bounds; install mpv or ffplay.")
                    return False
                code = self._run_inline_subprocess(
                    ["afplay", str(target)],
                    label=f"afplay-{label}",
                    timeout_seconds=180,
                    cancel_event=cancel_event,
                )
                return code == 0

            self._append_log("WARN: No terminal audio player found (need mpv, ffplay, or afplay).")
            return False

        if mpv_bin is None:
            self._append_log("WARN: mpv not found; install with `brew install mpv` for terminal video playback.")
            return False

        backends = self._resolved_video_backends()
        if not backends:
            self._append_log("WARN: mpv has no supported terminal video backend (need kitty/sixel/tct).")
            return False

        mpv_input_conf = self._ensure_mpv_input_conf()

        for backend in backends:
            base_command = [
                mpv_bin,
                "--no-config",
                "--really-quiet",
                "--terminal=yes",
                "--input-terminal=yes",
                "--force-window=no",
                "--keep-open=no",
                "--hwdec=auto-safe",
                "--video-sync=audio",
                "--framedrop=vo",
                "--osd-level=0",
                "--msg-level=all=warn,vo=info",
                f"--vo={backend}",
            ]
            if mpv_input_conf is not None:
                base_command.append(f"--input-conf={mpv_input_conf}")
                base_command.append("--input-default-bindings=yes")

            variant_options: list[tuple[str, list[str]]] = [(backend, [])]
            if backend == "kitty":
                common = [
                    "--vo-kitty-alt-screen=no",
                ]
                if os.environ.get("TMUX"):
                    common.append("--vo-kitty-auto-multiplexer-passthrough=yes")

                if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
                    shm_order = ["no"]
                else:
                    shm_order = ["no", "yes"]

                variant_options = [
                    (f"kitty-shm-{shm_mode}", common + [f"--vo-kitty-use-shm={shm_mode}"])
                    for shm_mode in shm_order
                ]
            elif backend == "sixel":
                variant_options = [("sixel", ["--vo-sixel-alt-screen=yes"])]

            for variant_name, variant_flags in variant_options:
                command = list(base_command)
                command.extend(variant_flags)
                if start_seconds is not None:
                    command.append(f"--start={max(0.0, float(start_seconds)):.3f}")
                if end_seconds is not None:
                    end_value = max(0.0, float(end_seconds))
                    if start_seconds is None or end_value > max(0.0, float(start_seconds)):
                        command.append(f"--end={end_value:.3f}")
                command.append(str(target))

                self._set_status("Terminal playback active. Press Esc to return to TUI.")
                code = self._run_interactive_subprocess(
                    command,
                    label=f"mpv-{label}-{variant_name}",
                )
                if code == 0:
                    return True

                self._append_log(
                    f"WARN: Terminal backend `{variant_name}` failed for {target.name}; trying next option."
                )

        self._append_log("WARN: Could not play in-terminal video preview with available backends.")
        return False

    def _resolve_mpv_binary(self) -> str | None:
        from_path = shutil.which("mpv")
        if from_path:
            return from_path

        for candidate in (
            "/opt/homebrew/bin/mpv",
            "/usr/local/bin/mpv",
            "/opt/local/bin/mpv",
        ):
            path = Path(candidate)
            if path.exists() and os.access(str(path), os.X_OK):
                return str(path)

        return None

    def _ensure_mpv_input_conf(self) -> Path | None:
        existing = self._mpv_input_conf_path
        if existing is not None and existing.exists():
            return existing

        conf_dir = (Path.home() / ".imagine" / "runtime").resolve()
        conf_path = conf_dir / "mpv-input.conf"
        conf_body = "ESC quit\nq quit\nQ quit-watch-later\n"
        try:
            conf_dir.mkdir(parents=True, exist_ok=True)
            if not conf_path.exists() or conf_path.read_text(encoding="utf-8") != conf_body:
                conf_path.write_text(conf_body, encoding="utf-8")
            self._mpv_input_conf_path = conf_path
            return conf_path
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not prepare mpv input config: {exc}")
            return None

    def _run_inline_subprocess(
        self,
        command: list[str],
        *,
        label: str,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        self._append_log(f"$ {shlex.join(command)}")
        code = 1
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self._repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            started = time.monotonic()
            while True:
                return_code = process.poll()
                if return_code is not None:
                    code = int(return_code)
                    break

                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    self._append_log(f"[{label}] cancelled by user.")
                    code = 130
                    break

                if timeout_seconds is not None and (time.monotonic() - started) > float(timeout_seconds):
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    self._append_log(f"ERROR: Command timed out after {timeout_seconds}s.")
                    code = 124
                    break

                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Command failed: {exc}")
            code = 1

        self._append_log(f"[{label}] exited with code {code}")
        return code

    def _run_interactive_subprocess(
        self,
        command: list[str],
        *,
        label: str,
        timeout_seconds: int | None = None,
    ) -> int:
        self._append_log(f"$ {shlex.join(command)}")
        stdscr = self._stdscr
        if stdscr is not None:
            try:
                curses.def_prog_mode()
                curses.endwin()
            except Exception:
                pass

        code = 1
        try:
            completed = subprocess.run(command, cwd=str(self._repo_root), check=False, timeout=timeout_seconds)
            code = int(completed.returncode)
        except subprocess.TimeoutExpired:
            self._append_log(f"ERROR: Interactive command timed out after {timeout_seconds}s.")
            code = 124
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Interactive command failed: {exc}")
            code = 1
        finally:
            if stdscr is not None:
                try:
                    curses.reset_prog_mode()
                    stdscr.refresh()
                    curses.flushinp()
                except Exception:
                    pass

        self._append_log(f"[{label}] exited with code {code}")
        return code

    def _maybe_prompt_clip_review(self) -> None:
        with self._lock:
            pending = self._pending_clip_review_prompt
            self._pending_clip_review_prompt = None

        if not isinstance(pending, dict):
            return

        project_dir_raw = str(pending.get("project_dir") or "").strip()
        if not project_dir_raw:
            return

        project_dir = Path(project_dir_raw).expanduser().resolve()
        entries = self._load_clip_catalog_entries(project_dir)
        if not entries:
            return

        with self._modal_focus():
            should_review = self._prompt_yes_no(
                title="Review Clip Catalog",
                body=f"{len(entries)} clips available. Replace any mismatched clips now?",
                default_yes=False,
            )
            if not should_review:
                return

            selected = self._select_multiple_clips(entries)
            if selected is None:
                self._set_status("Clip review cancelled.")
                return
            if not selected:
                self._set_status("No clips selected for replacement.")
                return

            chosen_names = [str(entries[index].get("clip_name") or "").strip() for index in selected]
            chosen_names = [name for name in chosen_names if name]
            if not chosen_names:
                self._set_status("No valid clip names selected for replacement.")
                return

            keywords_default = ", ".join(self.config.asset_keywords)
            keywords_value = self._prompt_input("Replacement keywords (optional)", keywords_default)
            if keywords_value is not None:
                if not isinstance(keywords_value, str):
                    keywords_value = None
            if keywords_value is not None:
                parsed = [part.strip() for part in re.split(r"[,;\n]+", keywords_value) if part.strip()]
                normalized = self._normalize_asset_keywords(parsed)
                if normalized:
                    self.config.asset_keywords = normalized
                    self._append_log(f"Replacement keywords set: {', '.join(self.config.asset_keywords)}")

        self._start_replace_clips_workflow(project_dir, chosen_names)

    def _maybe_prompt_stage_transition(self) -> None:
        with self._lock:
            pending = self._pending_stage_transition_prompt
            self._pending_stage_transition_prompt = None

        if not isinstance(pending, dict):
            return

        if self._is_running():
            with self._lock:
                self._pending_stage_transition_prompt = pending
            return

        next_stage = str(pending.get("next_stage") or "").strip().lower()
        if not next_stage:
            return

        title = str(pending.get("title") or "Continue")
        body = str(pending.get("body") or "Continue to next stage now?")
        if next_stage == "finalize":
            should_continue = self._handle_preview_ready_checkpoint()
        else:
            should_continue = self._prompt_yes_no(title=title, body=body, default_yes=True)

        self._hitl_stage = next_stage
        if should_continue:
            self._start_run_workflow()
            return

        if next_stage == "finalize":
            return

        self._set_status(f"{self._display_stage_name(next_stage).title()} stage ready. Press R when you are ready.")

    def _handle_preview_ready_checkpoint(self) -> bool:
        project_dir = self._active_project_dir
        if project_dir is None:
            self._set_status("Preview complete. Workspace not found. Press R to finalize.")
            return False

        preview_path = project_dir / "review" / "preview.mp4"
        self._append_log(f"Preview artifact: {preview_path}")
        if not preview_path.exists():
            self._append_log(f"WARN: Preview video not found: {preview_path}")
            self._set_status("Preview complete, but review/preview.mp4 was not found. Press R to finalize.")
            return False

        self._set_status(f"Preview ready: {preview_path}. Finalizing...")
        return True

    def _open_media_in_default_app(self, media_path: Path) -> bool:
        target = media_path.expanduser().resolve()
        if not target.exists():
            self._append_log(f"WARN: Media file not found: {target}")
            return False

        if sys.platform == "darwin":
            command = ["open", str(target)]
        elif os.name == "nt":
            command = ["cmd", "/c", "start", "", str(target)]
        else:
            opener = shutil.which("xdg-open")
            if opener is None:
                self._append_log("WARN: Could not open preview automatically; xdg-open is not available.")
                return False
            command = [opener, str(target)]

        self._append_log(f"$ {shlex.join(command)}")
        try:
            subprocess.Popen(
                command,
                cwd=str(self._repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not open preview in default app: {exc}")
            return False

    def _config_snapshot(self) -> tuple[Any, ...]:
        return (
            bool(self._hitl_enabled),
            bool(self.config.fast_mode),
            self.config.prompt,
            self._content_mode(),
            tuple(self.config.asset_keywords),
            tuple(self.config.news_feed_urls),
            int(self.config.minutes),
            self.config.resolution,
            bool(self.config.burn_subtitles),
            self._normalized_subtitle_preset(),
            self._normalized_subtitle_position(),
            self._normalized_subtitle_accent_color(),
            self._normalized_subtitle_box_color(),
            round(self._normalized_caption_font_scale(), 3),
            bool(self._normalized_subtitle_bold()),
            bool(self._normalized_subtitle_outline()),
            self.config.script_tone,
            self.config.target_audience,
            self.config.hook_style,
            self.config.narrative_mode,
            self.config.example_density,
            self.config.tts_engine,
            self.config.piper_voice_id,
            self.config.piper_speaker_id,
            self.config.voice_profile,
            round(float(self.config.voice_speed), 3),
            self.config.melo_language,
            self.config.melo_speaker,
            normalize_kokoro_lang_code(self.config.kokoro_lang_code),
            self.config.kokoro_voice,
            bool(self.config.enable_pexels_provider),
            bool(self.config.enable_pixabay_provider),
            bool(self.config.enable_coverr_provider),
            bool(self.config.enable_vecteezy_provider),
            bool(self.config.allow_image_assets),
            self._normalized_asset_mode(),
            self._normalized_image_motion_style(),
            bool(self.config.allow_attribution_required_assets),
            bool(self.config.include_intro),
            str(self.config.external_editor_command).strip(),
            str(getattr(self.config, "thumbnail_comfyui_url", THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL) or "").strip(),
            str(getattr(self.config, "thumbnail_comfyui_install_dir", "") or "").strip(),
            str(getattr(self.config, "thumbnail_default_export_dir", THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR) or "").strip(),
            bool(getattr(self.config, "thumbnail_use_ollama_hooks", True)),
        )

    def _commit_config_changes(self, before: tuple[Any, ...]) -> bool:
        changed = self._config_snapshot() != before
        if changed:
            self._refresh_prior_total_seconds()
            self._refresh_stock_key_cache()
            self._save_persisted_settings()
        return changed

    def _parse_escaped_modal_result(self, value: Any) -> tuple[str | None, bool]:
        if isinstance(value, tuple) and len(value) == 2:
            raw_value, raw_escaped = value
            parsed_value = raw_value if isinstance(raw_value, str) else None
            return parsed_value, bool(raw_escaped)
        if isinstance(value, str):
            return value, False
        return None, False

    def _report_config_edit(self, *, context: str, changed: bool, had_warning: bool) -> None:
        if had_warning:
            if changed:
                self._set_status(f"{context} updated with warnings.")
            else:
                self._set_status(f"{context} unchanged. Warnings kept previous value.")
            return
        if changed:
            self._set_status(f"{context} updated.")
        else:
            self._set_status(f"{context} unchanged.")

    def _config_summary_text(self) -> str:
        keywords = ",".join(self.config.asset_keywords) if self.config.asset_keywords else "auto"
        return (
            f"mode={self._content_mode()}, "
            f"minutes={self.config.minutes}, "
            f"resolution={self._resolution_label()}, "
            f"subtitles={'on' if self.config.burn_subtitles else 'off'}:{self._subtitle_preset_label()}, "
            f"script_profile={self.config.script_tone}, "
            f"audience={self.config.target_audience}, "
            f"hook={self.config.hook_style}, "
            f"narrative={self.config.narrative_mode}, "
            f"examples={self.config.example_density}, "
            f"keywords={keywords}, "
            f"voice={self._voice_display_value()}, "
            f"profile={self.config.voice_profile}, "
            f"speed={self.config.voice_speed:.2f}"
        )

    def _edit_prompt_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        prompt_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input("Video brief", self.config.prompt, return_escaped=allow_escape)
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if prompt_value is None:
            return ConfigEditResult()
        candidate = prompt_value.strip()
        if not candidate:
            self._append_log("WARN: Video brief cannot be empty. Keeping previous value.")
            return ConfigEditResult(had_warning=True)
        self.config.prompt = candidate
        return ConfigEditResult()

    def _edit_lucky_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        """
        Handle "I'm feeling lucky" selection.

        Fetches trending topics from Google Trends and uses LLM to transform
        the trend into a complete video concept.
        """
        # Check if Ollama is available
        if not self._probe_ollama_online(timeout=1.5):
            self._append_log("WARN: Ollama is not available. Start Ollama to use 'I'm feeling lucky' feature.")
            self._set_status("Ollama required. Start Ollama to use this feature.")
            return ConfigEditResult(had_warning=True)

        # Generate lucky prompt in background with spinner
        while True:
            try:
                result = self._run_with_spinner_modal(
                    title="I'm feeling lucky",
                    message="Fetching trending topics and generating concept",
                    detail_text="This may take 30-60 seconds",
                    task=self._generate_lucky_prompt,
                    allow_cancel=True,
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERROR: Failed to generate lucky prompt: {exc}")
                self._set_status("Could not generate concept. See logs.")
                return ConfigEditResult(had_warning=True)

            if result is None:
                # User cancelled or error occurred
                return ConfigEditResult()

            # Show confirmation modal
            action = self._show_lucky_confirmation_modal(result)

            if action == "accept":
                self._apply_lucky_prompt_result(result)
                self._set_status("Video concept applied from trending topic.")
                return ConfigEditResult()
            elif action == "retry":
                continue  # Loop again to get a different trend
            else:  # cancel
                return ConfigEditResult()

    def _generate_lucky_prompt(self) -> dict[str, Any] | None:
        """
        Worker function to fetch trends and generate video concept.

        Returns:
            Dict with trend, video_brief, asset_keywords, script_profile, rationale
            Returns None on error
        """
        # Fetch trending topics
        topics = trends.fetch_trending_topics()

        if not topics:
            raise RuntimeError("No trending topics available. Check your internet connection.")

        # Filter out already-used trends
        available_topics = [t for t in topics if t not in self._used_trends]

        # If we've used all trends, reset the list
        if not available_topics:
            self._used_trends.clear()
            available_topics = topics.copy()

        # Pick the first available trend
        trend = available_topics[0]

        # Mark as used
        self._used_trends.append(trend)

        # Generate concept from trend using Ollama
        # Use the same hardcoded model as the pipeline
        concept = trends.generate_video_concept_from_trend(
            trend=trend,
            ollama_model="qwen2.5:14b",
            timeout=120,
        )

        if concept is None:
            # Fallback: use trend directly as prompt
            self._append_log(f"WARN: LLM concept generation failed for '{trend}'. Using fallback.")
            concept = {
                "video_brief": f"Explain: {trend}",
                "asset_keywords": trends.extract_keywords_from_text(trend),
                "script_profile": "conversational",
                "rationale": "Fallback concept (LLM unavailable)",
            }

        # Add the original trend to the result
        concept["trend"] = trend

        return concept

    def _show_lucky_confirmation_modal(self, result: dict[str, Any]) -> str:
        """
        Show generated concept to user with 3 options: Accept, Try again, Cancel.

        Args:
            result: Dict with trend, video_brief, asset_keywords, script_profile, rationale

        Returns:
            "accept" | "retry" | "cancel"
        """
        if self._stdscr is None:
            return "accept"

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()

            # Extract data from result
            trend = result.get("trend", "Unknown trend")
            video_brief = result.get("video_brief", "")
            keywords = result.get("asset_keywords", [])
            profile = result.get("script_profile", "conversational")
            rationale = result.get("rationale", "")

            # Format keywords
            keywords_text = ", ".join(keywords[:7]) if keywords else "none"

            # Build modal content
            modal_width = min(max(60, len(video_brief) + 10), max(40, width - 4))
            modal_height = min(22, height - 2)

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)

            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            # Prepare wrapped text
            content_width = modal_width - 4
            brief_lines = textwrap.wrap(video_brief, content_width)
            keywords_lines = textwrap.wrap(f"Keywords: {keywords_text}", content_width)
            rationale_lines = textwrap.wrap(rationale, content_width) if rationale else []

            selected = 0
            options = ["Accept", "Try again", "Cancel"]

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                try:
                    # Title
                    win.addstr(0, 2, " I'm feeling lucky ", self._attr("accent", bold=True))

                    # Trend
                    row = 2
                    trend_text = self._trim_tail(f"Trend: {trend}", content_width)
                    win.addstr(row, 2, trend_text, self._attr("muted"))
                    row += 2

                    # Video brief
                    win.addstr(row, 2, "Video concept:", self._attr("ok", bold=True))
                    row += 1
                    for line in brief_lines[:4]:  # Limit to 4 lines
                        if row >= modal_height - 8:
                            break
                        win.addstr(row, 2, self._trim_tail(line, content_width))
                        row += 1
                    row += 1

                    # Keywords
                    if row < modal_height - 7:
                        for line in keywords_lines[:2]:  # Limit to 2 lines
                            if row >= modal_height - 7:
                                break
                            win.addstr(row, 2, self._trim_tail(line, content_width), self._attr("muted"))
                            row += 1
                        row += 1

                    # Profile
                    if row < modal_height - 6:
                        profile_text = self._trim_tail(f"Profile: {profile}", content_width)
                        win.addstr(row, 2, profile_text, self._attr("muted"))
                        row += 1

                    # Options (at bottom)
                    options_row = modal_height - 4
                    for idx, option in enumerate(options):
                        if idx == selected:
                            win.addstr(options_row + idx, 4, f"> {option}", curses.A_REVERSE)
                        else:
                            win.addstr(options_row + idx, 4, f"  {option}")

                    # Help text
                    win.addstr(
                        modal_height - 1,
                        2,
                        self._trim_tail("Enter select | ↑↓ navigate | Esc cancel", content_width),
                        self._attr("muted"),
                    )

                except curses.error:
                    pass

                win.refresh()
                key = win.getch()

                if key in (27,):  # Esc
                    return "cancel"
                elif key in (curses.KEY_UP, ord("k")):
                    selected = (selected - 1) % len(options)
                elif key in (curses.KEY_DOWN, ord("j")):
                    selected = (selected + 1) % len(options)
                elif key in (10, 13, curses.KEY_ENTER):
                    if selected == 0:
                        return "accept"
                    elif selected == 1:
                        return "retry"
                    else:
                        return "cancel"

    def _apply_lucky_prompt_result(self, result: dict[str, Any]) -> None:
        """
        Apply generated concept to config.

        Updates prompt, asset_keywords, script profile, and content_mode.
        """
        # Apply video brief
        video_brief = str(result.get("video_brief", "")).strip()
        if video_brief:
            self.config.prompt = video_brief

        # Apply asset keywords
        keywords = result.get("asset_keywords", [])
        if isinstance(keywords, list):
            self.config.asset_keywords = [str(k).strip() for k in keywords if str(k).strip()]

        # Apply script profile (map to valid tone)
        profile = str(result.get("script_profile", "conversational")).strip().lower()
        # Map LLM profile to TUI script tone
        profile_map = {
            "conversational": "conversational",
            "educational": "documentary",
            "narrative": "curiosity-driven",
            "energetic": "conversational",
        }
        tone = profile_map.get(profile, "conversational")
        if tone in self.SCRIPT_TONE_CHOICES:
            self._apply_script_profile(tone)

        # Force content mode to explainer
        self.config.content_mode = "explainer"

        # Log the application
        trend = result.get("trend", "unknown")
        self._append_log(f"Applied 'I'm feeling lucky' concept from trend: {trend}")

    def _apply_channel_profile(self, channel_key: str) -> None:
        profile = self.CHANNEL_PROFILES.get(channel_key)
        if profile is None:
            return
        self.config.tts_engine = profile["tts_engine"]
        self.config.kokoro_lang_code = normalize_kokoro_lang_code(profile["kokoro_lang_code"])
        self.config.kokoro_voice = profile["kokoro_voice"]
        self.config.script_tone = profile["script_tone"]
        self.config.target_audience = profile["target_audience"]
        self.config.hook_style = profile["hook_style"]
        self.config.narrative_mode = profile["narrative_mode"]
        self.config.example_density = profile["example_density"]
        self.config.voice_profile = profile["voice_profile"]
        self.config.script_language = profile["script_language"]
        self.config.active_channel = channel_key

    def _edit_channel_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        keys = list(self.CHANNEL_PROFILES.keys())
        labels = [self.CHANNEL_PROFILES[k]["label"] for k in keys]
        descriptions = {self.CHANNEL_PROFILES[k]["label"]: self.CHANNEL_PROFILES[k]["description"] for k in keys}
        current_label = self.CHANNEL_PROFILES.get(self.config.active_channel, self.CHANNEL_PROFILES["general"])["label"]
        selected_label, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Channel",
                options=labels,
                current_value=current_label,
                option_details=descriptions,
                marked_value=current_label,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if selected_label is not None:
            selected_key = next((k for k in keys if self.CHANNEL_PROFILES[k]["label"] == selected_label), "general")
            self._apply_channel_profile(selected_key)
            self._set_status(f"Channel set to {selected_label} — voice, language and tone updated.")
        return ConfigEditResult()

    def _generate_channel_lucky_prompt(self) -> dict[str, Any] | None:
        profile = self.CHANNEL_PROFILES.get(self.config.active_channel)
        if profile is None:
            return None
        channel_name = profile["channel_name"]
        theme_context = profile.get("theme_context") or ""
        target_audience = profile["target_audience"]
        script_language = profile["script_language"]
        lang_label = {"pt-br": "Brazilian Portuguese", "es": "Spanish", "fr": "French"}.get(
            script_language, script_language
        )
        prompt = (
            f'You are a content strategist for "{channel_name}".\n'
            f"Channel theme: {theme_context}\n"
            f"Audience: {target_audience}\n"
            f"Language: {lang_label} — respond entirely in that language.\n\n"
            f"Suggest a compelling YouTube video topic for this channel.\n"
            "For asset_keywords: provide 10-12 short stock footage search terms. "
            "1-2 words each — single words preferred. These are search terms, not descriptions. "
            "No underscores, no hyphens. Think: nouns, emotions, objects, places.\n"
            "Return ONLY valid JSON (no markdown, no code blocks).\n"
            'JSON schema: { "brief": "2-3 sentence video concept", "asset_keywords": ["keyword one", "keyword two", ...], "rationale": "why this works" }'
        )
        try:
            completed = subprocess.run(
                ["ollama", "run", "qwen2.5:14b", prompt],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=120,
                check=False,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        text = completed.stdout.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None

    def _resolved_channel_outro_copy(self) -> tuple[str, str, str]:
        profile = self.CHANNEL_PROFILES.get(self.config.active_channel, self.CHANNEL_PROFILES["general"])
        outro_text = str(profile.get("outro_text") or "").strip()
        outro_tagline = str(profile.get("outro_tagline") or "").strip()
        outro_spoken_text = str(profile.get("outro_spoken_text") or "").strip()
        if not outro_text:
            outro_text = "Obrigado por assistir" if self.config.script_language == "pt-br" else "Thanks for watching"
        if not outro_tagline:
            outro_tagline = (
                "Lembre-se de curtir, compartilhar e se inscrever"
                if self.config.script_language == "pt-br"
                else "Remember to like, share and subscribe"
            )
        if not outro_spoken_text:
            outro_spoken_text = f"{outro_text}. {outro_tagline}".strip(". ")
        return outro_text, outro_tagline, outro_spoken_text

    def _show_channel_lucky_confirmation_modal(self, result: dict[str, Any], channel_label: str) -> str:
        if self._stdscr is None:
            return "accept"
        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            brief = result.get("brief", "")
            keywords = result.get("asset_keywords", [])
            rationale = result.get("rationale", "")
            keywords_text = ", ".join(keywords[:7]) if keywords else "none"
            modal_width = min(max(60, len(brief) + 10), max(40, width - 4))
            modal_height = min(22, height - 2)
            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)
            content_width = modal_width - 4
            brief_lines = textwrap.wrap(brief, content_width)
            keywords_lines = textwrap.wrap(f"Keywords: {keywords_text}", content_width)
            rationale_lines = textwrap.wrap(rationale, content_width) if rationale else []
            selected = 0
            options = ["Accept", "Try again", "Cancel"]
            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass
                try:
                    title = self._trim_tail(f" Suggest topic for {channel_label} ", modal_width - 4)
                    win.addstr(0, 2, title, self._attr("accent", bold=True))
                    row = 2
                    win.addstr(row, 2, "Video concept:", self._attr("ok", bold=True))
                    row += 1
                    for line in brief_lines[:4]:
                        if row >= modal_height - 8:
                            break
                        win.addstr(row, 2, self._trim_tail(line, content_width))
                        row += 1
                    row += 1
                    if row < modal_height - 7:
                        for line in keywords_lines[:2]:
                            if row >= modal_height - 7:
                                break
                            win.addstr(row, 2, self._trim_tail(line, content_width), self._attr("muted"))
                            row += 1
                        row += 1
                    if rationale_lines and row < modal_height - 6:
                        for line in rationale_lines[:2]:
                            if row >= modal_height - 6:
                                break
                            win.addstr(row, 2, self._trim_tail(line, content_width), self._attr("muted"))
                            row += 1
                    options_row = modal_height - 4
                    for idx, option in enumerate(options):
                        if idx == selected:
                            win.addstr(options_row + idx, 4, f"> {option}", curses.A_REVERSE)
                        else:
                            win.addstr(options_row + idx, 4, f"  {option}")
                    win.addstr(modal_height - 1, 2, "↑↓ navigate  Enter select", self._attr("muted"))
                except curses.error:
                    pass
                win.refresh()
                key = win.getch()
                if key in (curses.KEY_UP, ord("k")) and selected > 0:
                    selected -= 1
                elif key in (curses.KEY_DOWN, ord("j")) and selected < len(options) - 1:
                    selected += 1
                elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                    return options[selected].lower().replace(" ", "_") if options[selected] != "Try again" else "retry"
                elif key in (27, ord("q")):
                    return "cancel"

    def _edit_channel_lucky_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        if self.config.active_channel == "general":
            self._set_status("Select a non-general channel first.")
            return ConfigEditResult(had_warning=True)
        if not self._probe_ollama_online(timeout=1.5):
            self._set_status("Ollama required. Start Ollama to use this feature.")
            return ConfigEditResult(had_warning=True)
        profile = self.CHANNEL_PROFILES.get(self.config.active_channel, self.CHANNEL_PROFILES["general"])
        channel_label = profile["label"]
        while True:
            try:
                result = self._run_with_spinner_modal(
                    title=f"Suggest topic for {channel_label}",
                    message="Generating channel topic concept",
                    detail_text="This may take 30-60 seconds",
                    task=self._generate_channel_lucky_prompt,
                    allow_cancel=True,
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERROR: Failed to generate channel topic: {exc}")
                self._set_status("Could not generate topic. See logs.")
                return ConfigEditResult(had_warning=True)
            if result is None:
                return ConfigEditResult()
            action = self._show_channel_lucky_confirmation_modal(result, channel_label)
            if action == "accept":
                brief = result.get("brief", "")
                keywords = result.get("asset_keywords", [])
                if brief:
                    self.config.prompt = brief
                if keywords:
                    self.config.asset_keywords = self._normalize_asset_keywords(
                        [str(k).strip().replace("_", " ") for k in keywords if str(k).strip()]
                    )
                self._set_status(f"Topic applied for {channel_label}.")
                return ConfigEditResult()
            elif action == "retry":
                continue
            else:
                return ConfigEditResult()

    def _edit_content_mode_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        selected, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Content mode",
                options=list(self.CONTENT_MODE_CHOICES),
                current_value=self._content_mode(),
                option_details=self.CONTENT_MODE_DETAILS,
                marked_value=self._content_mode(),
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if selected is not None:
            self.config.content_mode = normalize_content_mode(selected, "explainer")
            self._apply_default_news_feeds_if_needed()
            self._enforce_news_hitl_policy()
        return ConfigEditResult()

    def _edit_news_feed_urls_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        feed_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input(
                "News feeds (one or comma-separated)",
                "\n".join(self.config.news_feed_urls),
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if feed_value is None:
            return ConfigEditResult()
        parsed = [part.strip() for part in re.split(r"[,;\n]+", feed_value) if part.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(item)
        self.config.news_feed_urls = deduped
        return ConfigEditResult()

    def _edit_target_audience_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        audience_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input("Target audience", self.config.target_audience, return_escaped=allow_escape)
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if audience_value is None:
            return ConfigEditResult()
        candidate = audience_value.strip()
        if not candidate:
            self._append_log("WARN: Target audience cannot be empty. Keeping previous value.")
            return ConfigEditResult(had_warning=True)
        self.config.target_audience = candidate
        return ConfigEditResult()

    def _script_profile_bundle(self, profile: str) -> dict[str, str]:
        bundles = {
            "conversational": {
                "script_tone": "conversational",
                "target_audience": "curious general audience",
                "narrative_mode": "story-led",
                "hook_style": "surprising-fact",
                "example_density": "balanced",
            },
            "documentary": {
                "script_tone": "documentary",
                "target_audience": "curious general audience",
                "narrative_mode": "explainer",
                "hook_style": "question",
                "example_density": "light",
            },
            "curiosity-driven": {
                "script_tone": "curiosity-driven",
                "target_audience": "curious general audience",
                "narrative_mode": "story-led",
                "hook_style": "surprising-fact",
                "example_density": "heavy",
            },
            "analytical": {
                "script_tone": "analytical",
                "target_audience": "curious general audience",
                "narrative_mode": "argument-led",
                "hook_style": "problem-first",
                "example_density": "light",
            },
        }
        return dict(bundles.get(profile, bundles["conversational"]))

    def _apply_script_profile(self, profile: str) -> None:
        bundle = self._script_profile_bundle(profile)
        self.config.script_tone = bundle["script_tone"]
        self.config.target_audience = bundle["target_audience"]
        self.config.narrative_mode = bundle["narrative_mode"]
        self.config.hook_style = bundle["hook_style"]
        self.config.example_density = bundle["example_density"]

    def _edit_script_tone_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        tone_value, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Script Profile",
                options=list(self.SCRIPT_TONE_CHOICES),
                current_value=self.config.script_tone,
                option_details=self.SCRIPT_PROFILE_DETAILS,
                marked_value=self.config.script_tone,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if tone_value is not None:
            self._apply_script_profile(tone_value)
        return ConfigEditResult()

    def _edit_narrative_mode_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        narrative_value, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Narrative mode",
                options=list(self.NARRATIVE_MODE_CHOICES),
                current_value=self.config.narrative_mode,
                option_details=self.NARRATIVE_MODE_DETAILS,
                marked_value=self.config.narrative_mode,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if narrative_value is not None:
            self.config.narrative_mode = narrative_value
        return ConfigEditResult()

    def _edit_hook_style_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        hook_value, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Hook style",
                options=list(self.HOOK_STYLE_CHOICES),
                current_value=self.config.hook_style,
                option_details=self.HOOK_STYLE_DETAILS,
                marked_value=self.config.hook_style,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if hook_value is not None:
            self.config.hook_style = hook_value
        return ConfigEditResult()

    def _edit_example_density_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        density_value, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Example density",
                options=list(self.EXAMPLE_DENSITY_CHOICES),
                current_value=self.config.example_density,
                option_details=self.EXAMPLE_DENSITY_DETAILS,
                marked_value=self.config.example_density,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if density_value is not None:
            self.config.example_density = density_value
        return ConfigEditResult()

    def _edit_asset_keywords_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        with self._modal_focus():
            while True:
                current_preview = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(none)"
                action, escaped = self._parse_escaped_modal_result(
                    self._select_from_list(
                        label="Asset keywords",
                        options=["Edit all", "Add keyword", "Auto-add from brief"],
                        current_value="Edit all",
                        option_details={
                            "Edit all": f"Replace the full list. Current: {current_preview}",
                            "Add keyword": "Type one keyword to append to the existing list.",
                            "Auto-add from brief": "Ask Ollama to suggest keywords from the video brief and add them.",
                        },
                        return_escaped=allow_escape,
                    )
                )
                if escaped:
                    return ConfigEditResult(escaped=True)
                if action is None:
                    return ConfigEditResult()

                if action == "Edit all":
                    keyword_value, escaped2 = self._parse_escaped_modal_result(
                        self._edit_text_inline(
                            "Asset keywords (comma-separated)",
                            ", ".join(self.config.asset_keywords),
                            return_escaped=True,
                        )
                    )
                    if escaped2:
                        continue
                    if keyword_value is not None:
                        self.config.asset_keywords = self._normalize_asset_keywords(
                            [p.strip() for p in re.split(r"[,;\n]+", keyword_value) if p.strip()]
                        )
                    continue

                if action == "Add keyword":
                    while True:
                        current_kw_preview = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(none)"
                        new_kw, escaped2 = self._parse_escaped_modal_result(
                            self._prompt_input("Add keyword", current_kw_preview, return_escaped=True)
                        )
                        if escaped2:
                            break
                        if new_kw and new_kw.strip():
                            before = len(self.config.asset_keywords)
                            combined = list(self.config.asset_keywords) + [new_kw.strip()]
                            self.config.asset_keywords = self._normalize_asset_keywords(combined)
                            if len(self.config.asset_keywords) > before:
                                self._set_status(f"Added \"{new_kw.strip()}\". Total: {len(self.config.asset_keywords)}.")
                            else:
                                self._set_status(f"\"{new_kw.strip()}\" already in list.")
                    continue

                if action == "Auto-add from brief":
                    if not self.config.prompt.strip():
                        self._set_status("Set a video brief first.")
                        continue
                    if not self._probe_ollama_online(timeout=1.5):
                        self._set_status("Ollama required. Start Ollama to use auto-add.")
                        continue
                    try:
                        suggested = self._run_with_spinner_modal(
                            title="Auto-add keywords",
                            message="Generating keyword suggestions from brief",
                            detail_text="This may take 15-30 seconds",
                            task=self._suggest_keywords_from_brief,
                            allow_cancel=True,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._append_log(f"ERROR: Keyword auto-add failed: {exc}")
                        self._set_status("Could not generate keywords. See logs.")
                        curses.flushinp()
                        continue
                    curses.flushinp()
                    if suggested:
                        combined = list(self.config.asset_keywords) + suggested
                        self.config.asset_keywords = self._normalize_asset_keywords(combined)
                        self._set_status(f"Added {len(suggested)} keyword(s). Total: {len(self.config.asset_keywords)}.")
                    continue

    def _suggest_keywords_from_brief(self) -> list[str]:
        profile = self.CHANNEL_PROFILES.get(self.config.active_channel, self.CHANNEL_PROFILES["general"])
        script_language = profile.get("script_language", "en")
        lang_label = {"pt-br": "Brazilian Portuguese", "es": "Spanish", "fr": "French"}.get(
            script_language, "English"
        )
        lang_instruction = (
            f"Keywords must be in {lang_label}. "
            if script_language != "en"
            else ""
        )
        prompt = (
            f"Video brief: {self.config.prompt.strip()}\n\n"
            f"Generate 10-12 short search keywords for stock footage for this video.\n"
            f"{lang_instruction}"
            f"Rules:\n"
            f"- Each keyword must be 1 or 2 words maximum — single words are preferred\n"
            f"- These are search terms people type, not descriptions or phrases\n"
            f"- No underscores, no hyphens\n"
            f"- Think: topic nouns, emotions, objects, places — not sentences\n"
            f"Return ONLY a JSON array of strings. No markdown, no explanation.\n"
            f'Example: ["jesus", "amor", "bíblia", "oração", "fé", "tempestade", "floresta"]'
        )
        try:
            completed = subprocess.run(
                ["ollama", "run", "qwen2.5:14b", prompt],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=60,
                check=False,
            )
        except Exception:
            return []
        if completed.returncode != 0:
            return []
        text = completed.stdout.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        try:
            items = json.loads(text[start : end + 1])
            # Strip underscores from any keyword the LLM returns despite instructions
            return [str(item).strip().replace("_", " ") for item in items if str(item).strip()]
        except Exception:
            return []

    def _edit_minutes_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        minutes_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input("Minutes", str(self.config.minutes), return_escaped=allow_escape)
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if minutes_value is None:
            return ConfigEditResult()
        try:
            minutes = int(minutes_value)
            if minutes <= 0:
                raise ValueError
        except ValueError:
            self._append_log("WARN: Minutes must be a positive integer. Keeping previous value.")
            return ConfigEditResult(had_warning=True)
        self.config.minutes = minutes
        return ConfigEditResult()

    def _edit_resolution_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        selected, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Resolution",
                options=list(self.RESOLUTION_CHOICES),
                current_value=self._resolution_label(),
                marked_value=self._resolution_label(),
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if selected is None:
            return ConfigEditResult()
        self.config.resolution = self.RESOLUTION_PRESETS.get(str(selected).strip(), self.RESOLUTION_PRESETS["720p"])
        return ConfigEditResult()

    def _edit_voice_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        try:
            voice_entries, entry_by_label, current_label, load_warning = self._run_with_spinner_modal(
                title="Voice",
                message="Loading voices",
                task=self._combined_voice_entries_for_selection,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Voice picker failed while loading voices: {exc}")
            self._set_status("Voice picker unavailable right now. Keeping previous voice.")
            return ConfigEditResult(had_warning=True)

        had_warning = bool(load_warning)
        if not voice_entries:
            self._append_log("WARN: Could not load any selectable voices. Keeping previous voice.")
            return ConfigEditResult(had_warning=True)

        selected_label, escaped = self._parse_escaped_modal_result(
            self._select_voice_entry_with_preview(
                voice_entries,
                entry_by_label=entry_by_label,
                current_label=current_label,
                allow_escape=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True, had_warning=had_warning)
        if selected_label is None:
            return ConfigEditResult(had_warning=had_warning)
        selected_entry = entry_by_label.get(selected_label)
        if selected_entry is not None and not self._apply_voice_selection_entry(selected_entry):
            had_warning = True
        return ConfigEditResult(had_warning=had_warning)

    def _select_voice_entry_with_preview(
        self,
        voice_entries: list[dict[str, Any]],
        *,
        entry_by_label: dict[str, dict[str, Any]],
        current_label: str,
        allow_escape: bool,
    ) -> str | None | tuple[str | None, bool]:
        if self._stdscr is None:
            return (None, True) if allow_escape else None

        labels = [str(item.get("label") or "").strip() for item in voice_entries if str(item.get("label") or "").strip()]
        if not labels:
            return (None, True) if allow_escape else None

        option_details = {
            str(item.get("label") or ""): self._voice_selection_option_detail(item)
            for item in voice_entries
            if str(item.get("label") or "").strip()
        }
        selected = labels.index(current_label) if current_label in labels else 0
        start_index = 0

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            max_option_len = max(len(item) for item in labels)
            modal_width = min(max(42, max_option_len + 12), max(12, width - 2))
            detail_rows = 3
            max_modal_height = max(6 + detail_rows, height - 2)
            max_list_rows = max(1, max_modal_height - 4 - detail_rows)
            list_rows = min(len(labels), max_list_rows)
            modal_height = max(6 + detail_rows, list_rows + 4 + detail_rows)
            if modal_width < 12 or modal_height < (6 + detail_rows):
                return (None, True) if allow_escape else None

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            project_dir = self._resolve_debug_voice_project_dir()
            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(" Voice ", max(1, modal_width - 4))
                help_text = "Enter select | Space preview | Esc back"
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                if selected < start_index:
                    start_index = selected
                elif selected >= start_index + list_rows:
                    start_index = selected - list_rows + 1

                for row in range(list_rows):
                    option_index = start_index + row
                    if option_index >= len(labels):
                        break
                    item = labels[option_index]
                    marker = "*" if item == current_label else " "
                    line = self._trim_tail(f"{'>' if option_index == selected else ' '} {marker} {item}", modal_width - 2)
                    attr = curses.A_REVERSE if option_index == selected else 0
                    try:
                        win.addstr(1 + row, 1, line, attr)
                    except curses.error:
                        pass

                detail_text = option_details.get(labels[selected], "")
                detail_width = max(20, modal_width - 4)
                detail_lines = textwrap.wrap(
                    detail_text,
                    width=detail_width,
                    replace_whitespace=False,
                    drop_whitespace=False,
                )[:detail_rows]
                detail_top = 1 + list_rows
                for row in range(detail_rows):
                    line = detail_lines[row] if row < len(detail_lines) else ""
                    try:
                        win.addstr(detail_top + row, 2, self._trim_tail(line, modal_width - 4), self._attr("muted"))
                    except curses.error:
                        pass

                win.refresh()
                key = win.getch()
                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    selected = (selected - 1) % len(labels)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    selected = (selected + 1) % len(labels)
                    continue
                if key == ord(" "):
                    selected_label = labels[selected]
                    selected_entry = entry_by_label.get(selected_label)
                    if selected_entry is not None:
                        _lang = str(selected_entry.get("lang_code") or "")
                        _phrase = self.DEBUG_VOICE_TEST_PHRASES.get(_lang, self.DEBUG_VOICE_TEST_PHRASE)
                        self._preview_debug_voice_entry(
                            project_dir,
                            selected_label,
                            selected_entry,
                            _phrase,
                        )
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    value = labels[selected]
                    return (value, False) if allow_escape else value
                if key == 27:
                    return (None, True) if allow_escape else None

    def _edit_voice_profile_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        profile_value, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Voice profile",
                options=list(self.VOICE_PROFILE_CHOICES),
                current_value=self.config.voice_profile,
                marked_value=self.config.voice_profile,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if profile_value is not None:
            self.config.voice_profile = profile_value
        return ConfigEditResult()

    def _edit_voice_speed_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        speed_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input(
                "Voice speed (0.5-2.0)",
                f"{self.config.voice_speed:.2f}",
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if speed_value is None:
            return ConfigEditResult()
        try:
            speed = float(speed_value)
            if speed < 0.5 or speed > 2.0:
                raise ValueError
        except ValueError:
            self._append_log("WARN: Voice speed must be between 0.5 and 2.0. Keeping previous value.")
            return ConfigEditResult(had_warning=True)
        self.config.voice_speed = speed
        return ConfigEditResult()

    def _edit_intro_title_card_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        selected, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Intro title card",
                options=["Off", "On"],
                current_value="On" if self.config.include_intro else "Off",
                marked_value="On" if self.config.include_intro else "Off",
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if selected is not None:
            self.config.include_intro = str(selected).strip().lower() == "on"
        return ConfigEditResult()

    def _edit_external_editor_command_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        current_value = str(self.config.external_editor_command).strip() or "code --wait {file}"
        command_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input("External editor command", current_value, return_escaped=allow_escape)
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if command_value is None:
            return ConfigEditResult()
        candidate = str(command_value).strip()
        if candidate and "{file}" not in candidate:
            self._append_log("WARN: External editor command must include {file}. Keeping previous value.")
            return ConfigEditResult(had_warning=True)
        self.config.external_editor_command = candidate
        return ConfigEditResult()

    def _edit_thumbnail_comfyui_url_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        current_value = str(getattr(self.config, "thumbnail_comfyui_url", THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL) or "").strip()
        updated_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input("Thumbnail ComfyUI URL", current_value or THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL, return_escaped=allow_escape)
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if updated_value is None:
            return ConfigEditResult()
        candidate = str(updated_value).strip().rstrip("/")
        if not candidate:
            candidate = THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL
        self.config.thumbnail_comfyui_url = candidate
        return ConfigEditResult()

    def _edit_thumbnail_comfyui_install_dir_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        current_value = str(getattr(self.config, "thumbnail_comfyui_install_dir", "") or "").strip()
        detected = self._detect_local_comfyui_install_dir()
        default_value = current_value or (str(detected) if detected is not None else "")
        updated_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input(
                "Thumbnail ComfyUI install dir (CLEAR for auto-detect)",
                default_value,
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if updated_value is None:
            return ConfigEditResult()
        candidate = str(updated_value).strip()
        if candidate.upper() == "CLEAR":
            candidate = ""
        if candidate:
            resolved = Path(candidate).expanduser().resolve()
            if not self._is_comfyui_install_dir(resolved):
                self._append_log(f"WARN: ComfyUI install dir is invalid: {resolved}")
                return ConfigEditResult(had_warning=True)
            self.config.thumbnail_comfyui_install_dir = str(resolved)
            self._comfyui_detected_install_dir = resolved
            self._comfyui_detection_attempted = True
            return ConfigEditResult()
        self.config.thumbnail_comfyui_install_dir = ""
        self._comfyui_detected_install_dir = None
        self._comfyui_detection_attempted = False
        return ConfigEditResult()

    def _edit_thumbnail_export_dir_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        current_value = str(getattr(self.config, "thumbnail_default_export_dir", THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR) or "").strip()
        updated_value, escaped = self._parse_escaped_modal_result(
            self._prompt_input("Thumbnail export folder", current_value or str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR), return_escaped=allow_escape)
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if updated_value is None:
            return ConfigEditResult()
        candidate = Path(str(updated_value).strip() or str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR)).expanduser().resolve()
        self.config.thumbnail_default_export_dir = str(candidate)
        return ConfigEditResult()

    def _edit_thumbnail_ollama_hooks_field(self, *, allow_escape: bool = False) -> ConfigEditResult:
        selected, escaped = self._parse_escaped_modal_result(
            self._select_from_list(
                label="Thumbnail Ollama hooks",
                options=["On", "Off"],
                current_value="On" if bool(getattr(self.config, "thumbnail_use_ollama_hooks", True)) else "Off",
                marked_value="On" if bool(getattr(self.config, "thumbnail_use_ollama_hooks", True)) else "Off",
                return_escaped=allow_escape,
            )
        )
        if escaped:
            return ConfigEditResult(escaped=True)
        if selected is not None:
            self.config.thumbnail_use_ollama_hooks = str(selected).strip().lower() == "on"
        return ConfigEditResult()

    def _edit_named_config_field(self, field_name: str, *, allow_escape: bool = False) -> ConfigEditResult:
        if field_name == "lucky":
            return self._edit_lucky_field(allow_escape=allow_escape)
        if field_name == "channel":
            return self._edit_channel_field(allow_escape=allow_escape)
        if field_name == "channel_lucky":
            return self._edit_channel_lucky_field(allow_escape=allow_escape)
        if field_name == "prompt":
            return self._edit_prompt_field(allow_escape=allow_escape)
        if field_name == "content_mode":
            return self._edit_content_mode_field(allow_escape=allow_escape)
        if field_name == "news_feeds":
            return self._edit_news_feed_urls_field(allow_escape=allow_escape)
        if field_name == "audience":
            return self._edit_target_audience_field(allow_escape=allow_escape)
        if field_name == "tone":
            return self._edit_script_tone_field(allow_escape=allow_escape)
        if field_name == "narrative":
            return self._edit_narrative_mode_field(allow_escape=allow_escape)
        if field_name == "hook":
            return self._edit_hook_style_field(allow_escape=allow_escape)
        if field_name == "examples":
            return self._edit_example_density_field(allow_escape=allow_escape)
        if field_name == "keywords":
            return self._edit_asset_keywords_field(allow_escape=allow_escape)
        if field_name == "minutes":
            return self._edit_minutes_field(allow_escape=allow_escape)
        if field_name == "resolution":
            return self._edit_resolution_field(allow_escape=allow_escape)
        if field_name == "voice":
            return self._edit_voice_field(allow_escape=allow_escape)
        if field_name == "profile":
            return self._edit_voice_profile_field(allow_escape=allow_escape)
        if field_name == "speed":
            return self._edit_voice_speed_field(allow_escape=allow_escape)
        if field_name == "intro":
            return self._edit_intro_title_card_field(allow_escape=allow_escape)
        if field_name == "external_editor":
            return self._edit_external_editor_command_field(allow_escape=allow_escape)
        if field_name == "thumbnail_comfyui":
            return self._edit_thumbnail_comfyui_url_field(allow_escape=allow_escape)
        if field_name == "thumbnail_comfyui_install":
            return self._edit_thumbnail_comfyui_install_dir_field(allow_escape=allow_escape)
        if field_name == "thumbnail_export_dir":
            return self._edit_thumbnail_export_dir_field(allow_escape=allow_escape)
        if field_name == "thumbnail_ollama_hooks":
            return self._edit_thumbnail_ollama_hooks_field(allow_escape=allow_escape)
        raise RuntimeError(f"Unknown config field editor: {field_name}")

    def _open_prompt_settings_menu(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        current_option: str | None = None

        with self._modal_focus():
            while True:
                active_channel = self.config.active_channel
                channel_label = self.CHANNEL_PROFILES.get(active_channel, self.CHANNEL_PROFILES["general"])["label"]
                is_channel = active_channel != "general"
                top_entry = f"Suggest topic for {channel_label}" if is_channel else "I'm feeling lucky"
                top_field = "channel_lucky" if is_channel else "lucky"
                field_map = {
                    top_entry: top_field,
                    f"Channel: {channel_label}": "channel",
                    "Video brief": "prompt",
                    "Content mode": "content_mode",
                    "Script Profile": "tone",
                    "Asset keywords": "keywords",
                    "News sources": "news_feeds",
                }
                options = list(field_map.keys())
                if self._content_mode() != "news":
                    options = [value for value in options if value != "News sources"]
                choice = self._select_from_list(
                    label="Prompt",
                    options=options,
                    current_value=current_option if current_option in options else top_entry,
                )
                if choice is None:
                    self._set_status("Prompt settings closed.")
                    return

                current_option = cast(str, choice)
                before = self._config_snapshot()
                result = self._edit_named_config_field(field_map[current_option], allow_escape=False)
                changed = self._commit_config_changes(before)
                self._report_config_edit(context="Prompt settings", changed=changed, had_warning=result.had_warning)

    def _open_settings_menu(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        current_option = f"Minutes: {self.config.minutes}"

        with self._modal_focus():
            while True:
                options = [
                    f"Minutes: {self.config.minutes}",
                    f"Resolution: {self._resolution_label()}",
                    "Subtitles",
                    f"Voice: {self._voice_display_value()}",
                    f"Voice profile: {self.config.voice_profile}",
                    f"Voice speed: {self.config.voice_speed:.2f}",
                    f"Intro title card: {'On' if self.config.include_intro else 'Off'}",
                    f"External editor: {self._external_editor_summary()}",
                    f"Thumbnail ComfyUI: {self._thumbnail_comfyui_summary()}",
                    f"Thumbnail export folder: {self._thumbnail_export_dir_summary()}",
                    f"Thumbnail Ollama hooks: {self._thumbnail_ollama_hooks_summary()}",
                    f"HITL: {'On' if self._hitl_enabled else 'Off'}",
                    f"Fast mode: {'On' if self.config.fast_mode else 'Off'}",
                    f"Narration voice policy: {self._tts_policy_summary()}",
                    "Visual Assets",
                ]
                choice = self._select_from_list(
                    label="Settings",
                    options=options,
                    current_value=current_option if current_option in options else options[0],
                )
                if choice is None:
                    self._set_status("Settings closed.")
                    return

                choice_value = cast(str, choice)
                current_option = choice_value

                if choice_value.startswith("Minutes:"):
                    before = self._config_snapshot()
                    result = self._edit_minutes_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("Resolution:"):
                    before = self._config_snapshot()
                    result = self._edit_resolution_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value == "Subtitles":
                    self._open_subtitle_settings_menu()
                    continue

                if choice_value.startswith("Voice:"):
                    before = self._config_snapshot()
                    result = self._edit_voice_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("Voice profile:"):
                    before = self._config_snapshot()
                    result = self._edit_voice_profile_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("Voice speed:"):
                    before = self._config_snapshot()
                    result = self._edit_voice_speed_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("Intro title card:"):
                    before = self._config_snapshot()
                    result = self._edit_intro_title_card_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("External editor:"):
                    before = self._config_snapshot()
                    result = self._edit_external_editor_command_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("Thumbnail ComfyUI:"):
                    self._open_thumbnail_comfyui_manager()
                    continue

                if choice_value.startswith("Thumbnail export folder:"):
                    before = self._config_snapshot()
                    result = self._edit_thumbnail_export_dir_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("Thumbnail Ollama hooks:"):
                    before = self._config_snapshot()
                    result = self._edit_thumbnail_ollama_hooks_field()
                    changed = self._commit_config_changes(before)
                    self._report_config_edit(context="Settings", changed=changed, had_warning=result.had_warning)
                    continue

                if choice_value.startswith("HITL:"):
                    if self._content_mode() == "news" and self._hitl_enabled:
                        self._set_status("News mode requires HITL for source review. Keep HITL On or switch Content mode.")
                        self._append_log("Settings: HITL remains On because news mode requires manual source review.")
                        continue
                    self._hitl_enabled = not self._hitl_enabled
                    mode = "On" if self._hitl_enabled else "Off"
                    if not self._hitl_enabled:
                        with self._lock:
                            self._pending_unique_asset_prompt = None
                            self._pending_clip_review_prompt = None
                            self._pending_stage_transition_prompt = None
                            self._pending_source_review_prompt = None
                            self._pending_script_review_prompt = None
                            self._pending_shot_review_prompt = None
                            self._pending_scene_review_prompt = None
                    self._save_persisted_settings()
                    self._set_status(f"Settings updated: HITL {mode}.")
                    self._append_log(f"Settings: HITL set to {mode}.")
                    continue

                if choice_value.startswith("Fast mode:"):
                    self.config.fast_mode = not self.config.fast_mode
                    mode = "On" if self.config.fast_mode else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Settings updated: fast mode {mode}.")
                    self._append_log(
                        "Settings: fast mode set to "
                        f"{mode} (shorter run, lower resolution, shorter bookends, burned subtitles kept on)."
                    )
                    continue

                if choice_value.startswith("Narration voice policy:"):
                    self._show_paginated_text_modal("Narration Voice Policy", self._tts_policy_notes_text())
                    continue

                if choice_value == "Visual Assets":
                    self._open_asset_policy_menu()
                    continue

    def _run_preflight(self) -> bool:
        if self._is_running():
            self._set_status("A command is already running.")
            return False

        live_config = self.config
        working_config = copy.deepcopy(self.config)
        before = self._config_snapshot()
        steps = ["minutes", "resolution", "subtitles"]
        step_index = 0
        had_warning = False

        self.config = working_config
        self._set_status("Run preflight: Enter applies/keeps, Esc goes back one step.")

        try:
            while 0 <= step_index < len(steps):
                step = steps[step_index]
                if step == "subtitles":
                    action = self._open_subtitle_settings_menu(
                        preflight_mode=True,
                        continue_label="Finish preflight",
                    )
                    if action == "back":
                        if step_index == 0:
                            self.config = live_config
                            self._set_status("Run cancelled.")
                            return False
                        step_index -= 1
                        continue
                    step_index += 1
                    continue

                result = self._edit_named_config_field(step, allow_escape=True)
                had_warning = had_warning or result.had_warning
                if result.escaped:
                    if step_index == 0:
                        self.config = live_config
                        self._set_status("Run cancelled.")
                        return False
                    step_index -= 1
                    continue
                step_index += 1
        finally:
            if self.config is working_config:
                self.config = live_config

        self.config = working_config
        changed = self._commit_config_changes(before)
        if changed:
            self._append_log(f"Updated config: {self._config_summary_text()}")
        if had_warning:
            self._set_status("Run preflight updated settings with warnings. Starting run...")
        elif changed:
            self._set_status("Run preflight complete. Starting run...")
        else:
            self._set_status("Run preflight complete. Starting run...")
        return True


    def _open_subtitle_settings_menu(
        self,
        *,
        preflight_mode: bool = False,
        continue_label: str = "Continue",
    ) -> Literal["closed", "back"]:
        with self._modal_focus():
            current_option = continue_label if preflight_mode else f"Burned subtitles: {'On' if self.config.burn_subtitles else 'Off'}"
            while True:
                options = [continue_label] if preflight_mode else []
                preset = self._normalized_subtitle_preset()
                highlight_text_label = f"Highlight text color: {self._subtitle_accent_color_label()}"
                box_background_label = f"Box background color: {self._subtitle_box_color_label()}"
                options.extend(
                    [
                        f"Burned subtitles: {'On' if self.config.burn_subtitles else 'Off'}",
                        f"Subtitle preset: {self._subtitle_preset_label()}",
                        f"Subtitle position: {self._subtitle_position_label()}",
                    ]
                )
                if preset == "highlight-box-follow":
                    options.extend([box_background_label, highlight_text_label])
                elif preset == "highlight-follow":
                    options.append(highlight_text_label)
                options.extend(
                    [
                        f"Font size: {self._subtitle_font_size_label()}",
                        f"Bold: {self._subtitle_bold_label()}",
                        f"Outline: {self._subtitle_outline_label()}",
                    ]
                )

                option_details = {
                    f"Burned subtitles: {'On' if self.config.burn_subtitles else 'Off'}": (
                        "Turn subtitle burn-in on or off. When burn-in is on, YouTube caption upload defaults to off."
                    ),
                    f"Subtitle preset: {self._subtitle_preset_label()}": self.SUBTITLE_PRESET_DETAILS.get(
                        self._normalized_subtitle_preset(),
                        "",
                    ),
                    f"Subtitle position: {self._subtitle_position_label()}": self.SUBTITLE_POSITION_DETAILS.get(
                        self._normalized_subtitle_position(),
                        "",
                    ),
                    f"Font size: {self._subtitle_font_size_label()}": self.SUBTITLE_FONT_SIZE_DETAILS.get(
                        self._subtitle_font_size_label(),
                        "",
                    ),
                    f"Bold: {self._subtitle_bold_label()}": "Render subtitle text in bold when on.",
                    f"Outline: {self._subtitle_outline_label()}": (
                        "Render subtitle text with a stroke outline when on. When off, a lighter shadow is kept."
                    ),
                }
                if preflight_mode:
                    option_details[continue_label] = "Finish run preflight with the current subtitle settings."
                if box_background_label in options:
                    option_details[box_background_label] = (
                        "Background color used by the active-word box in highlight box follow mode."
                    )
                if highlight_text_label in options:
                    option_details[highlight_text_label] = self.SUBTITLE_ACCENT_COLOR_DETAILS.get(
                        self._normalized_subtitle_accent_color(),
                        "",
                    )

                choice = self._select_from_list(
                    label="Subtitles",
                    options=options,
                    current_value=current_option if current_option in options else options[0],
                    option_details=option_details,
                )
                if choice is None:
                    if preflight_mode:
                        return "back"
                    self._set_status("Subtitle settings closed.")
                    return "closed"

                choice_value = cast(str, choice)
                current_option = choice_value
                if preflight_mode and choice_value == continue_label:
                    return "closed"

                before = self._config_snapshot()

                if choice_value.startswith("Burned subtitles:"):
                    self.config.burn_subtitles = not self.config.burn_subtitles
                    mode = "On" if self.config.burn_subtitles else "Off"
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: burned subtitles {mode}.")
                    self._append_log(
                        "Subtitle settings: burned subtitles set to "
                        f"{mode} ({'YouTube captions will default to off' if self.config.burn_subtitles else 'YouTube captions will default to on'})."
                    )
                    continue

                if choice_value.startswith("Subtitle preset:"):
                    option_labels = [item.replace("-", " ") for item in self.SUBTITLE_PRESET_CHOICES]
                    label_to_value = dict(zip(option_labels, self.SUBTITLE_PRESET_CHOICES))
                    current_label = self._subtitle_preset_label()
                    selected = self._select_from_list(
                        label="Subtitle Preset",
                        options=option_labels,
                        current_value=current_label,
                        marked_value=current_label,
                        option_details={
                            item.replace("-", " "): self.SUBTITLE_PRESET_DETAILS.get(item, "")
                            for item in self.SUBTITLE_PRESET_CHOICES
                        },
                    )
                    if selected is None:
                        continue
                    self.config.subtitle_preset = label_to_value.get(selected, "regular")
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: preset {selected}.")
                    self._append_log(f"Subtitle settings: preset set to {self.config.subtitle_preset}.")
                    continue

                if choice_value.startswith("Subtitle position:"):
                    option_labels = [item.replace("-", " ") for item in self.SUBTITLE_POSITION_CHOICES]
                    label_to_value = dict(zip(option_labels, self.SUBTITLE_POSITION_CHOICES))
                    current_label = self._subtitle_position_label()
                    selected = self._select_from_list(
                        label="Subtitle Position",
                        options=option_labels,
                        current_value=current_label,
                        marked_value=current_label,
                        option_details={
                            item.replace("-", " "): self.SUBTITLE_POSITION_DETAILS.get(item, "")
                            for item in self.SUBTITLE_POSITION_CHOICES
                        },
                    )
                    if selected is None:
                        continue
                    self.config.subtitle_position = label_to_value.get(selected, "bottom")
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: position {selected}.")
                    self._append_log(f"Subtitle settings: position set to {self.config.subtitle_position}.")
                    continue

                if choice_value.startswith("Font size:"):
                    option_labels = [label for label, _ in self.SUBTITLE_FONT_SIZE_PRESETS]
                    label_to_value = dict(self.SUBTITLE_FONT_SIZE_PRESETS)
                    current_label = self._subtitle_font_size_label()
                    selected = self._select_from_list(
                        label="Font Size",
                        options=option_labels,
                        current_value=current_label if current_label in option_labels else option_labels[0],
                        marked_value=current_label if current_label in option_labels else None,
                        option_details={
                            label: self.SUBTITLE_FONT_SIZE_DETAILS.get(label, "")
                            for label, _ in self.SUBTITLE_FONT_SIZE_PRESETS
                        },
                    )
                    if selected is None:
                        continue
                    self.config.caption_font_scale = float(label_to_value.get(selected, 0.9))
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: font size {selected}.")
                    self._append_log(
                        f"Subtitle settings: font scale set to {self._normalized_caption_font_scale():.2f}."
                    )
                    continue

                if choice_value.startswith("Bold:"):
                    self.config.subtitle_bold = not self.config.subtitle_bold
                    mode = "On" if self.config.subtitle_bold else "Off"
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: bold {mode}.")
                    self._append_log(f"Subtitle settings: bold set to {mode}.")
                    continue

                if choice_value.startswith("Outline:"):
                    self.config.subtitle_outline = not self.config.subtitle_outline
                    mode = "On" if self.config.subtitle_outline else "Off"
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: outline {mode}.")
                    self._append_log(f"Subtitle settings: outline set to {mode}.")
                    continue

                if choice_value.startswith("Box background color:"):
                    option_labels = [item.replace("-", " ") for item in self.SUBTITLE_BOX_COLOR_CHOICES]
                    label_to_value = dict(zip(option_labels, self.SUBTITLE_BOX_COLOR_CHOICES))
                    current_label = self._subtitle_box_color_label()
                    selected = self._select_from_list(
                        label="Box Background Color",
                        options=option_labels,
                        current_value=current_label,
                        marked_value=current_label,
                        option_details={
                            item.replace("-", " "): self.SUBTITLE_ACCENT_COLOR_DETAILS.get(item, "")
                            for item in self.SUBTITLE_BOX_COLOR_CHOICES
                        },
                    )
                    if selected is None:
                        continue
                    self.config.subtitle_box_color = label_to_value.get(
                        selected,
                        self._normalized_subtitle_accent_color(),
                    )
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: box background color {selected}.")
                    self._append_log(
                        "Subtitle settings: box background color set to "
                        f"{self.config.subtitle_box_color}."
                    )
                    continue

                if choice_value.startswith("Highlight text color:"):
                    option_labels = [item.replace("-", " ") for item in self.SUBTITLE_TEXT_COLOR_CHOICES]
                    label_to_value = dict(zip(option_labels, self.SUBTITLE_TEXT_COLOR_CHOICES))
                    current_label = self._subtitle_accent_color_label()
                    selected = self._select_from_list(
                        label="Highlight Text Color",
                        options=option_labels,
                        current_value=current_label,
                        marked_value=current_label,
                        option_details={
                            item.replace("-", " "): self.SUBTITLE_ACCENT_COLOR_DETAILS.get(item, "")
                            for item in self.SUBTITLE_TEXT_COLOR_CHOICES
                        },
                    )
                    if selected is None:
                        continue
                    self.config.subtitle_accent_color = label_to_value.get(selected, "sunflower")
                    if not preflight_mode:
                        self._commit_config_changes(before)
                        self._set_status(f"Subtitle settings updated: highlight text color {selected}.")
                    self._append_log(
                        "Subtitle settings: highlight text color set to "
                        f"{self.config.subtitle_accent_color}."
                    )
                    continue

    def _open_asset_policy_menu(self) -> None:
        with self._modal_focus():
            while True:
                options = [
                    f"Asset mode: {self._asset_mode_label()}",
                    f"Image motion style: {self._image_motion_style_label()}",
                    f"Pexels provider: {'On' if self.config.enable_pexels_provider else 'Off'}",
                    f"Pixabay provider: {'On' if self.config.enable_pixabay_provider else 'Off'}",
                    f"Coverr provider: {'On' if self.config.enable_coverr_provider else 'Off'}",
                    f"Vecteezy provider: {'On' if self.config.enable_vecteezy_provider else 'Off'}",
                    f"Allow still images: {'On' if self.config.allow_image_assets else 'Off'}",
                    f"Attribution-required sources: {'On' if self.config.allow_attribution_required_assets else 'Off'}",
                    "View provider notes",
                ]
                option_details = {
                    options[0]: self.ASSET_MODE_DETAILS.get(self._normalized_asset_mode(), ""),
                    options[1]: self.IMAGE_MOTION_STYLE_DETAILS.get(self._normalized_image_motion_style(), ""),
                    options[2]: self._asset_policy_option_detail("pexels"),
                    options[3]: self._asset_policy_option_detail("pixabay"),
                    options[4]: self._asset_policy_option_detail("coverr"),
                    options[5]: self._asset_policy_option_detail("vecteezy"),
                    options[6]: "When on, scenes may use image fallbacks when good video clips are unavailable.",
                    options[7]: (
                        "When on, providers/assets that need credits stay eligible and should be exported into the "
                        "YouTube description."
                    ),
                    options[8]: "Show current provider-specific notes and reminders for the live stock sources.",
                }
                choice = self._select_from_list(
                    label="Visual Assets",
                    options=options,
                    current_value=options[0],
                    option_details=option_details,
                )
                if choice is None:
                    self._set_status("Visual assets closed.")
                    return

                if choice.startswith("Asset mode:"):
                    option_labels = [item.replace("-", " ") for item in self.ASSET_MODE_CHOICES]
                    label_to_mode = dict(zip(option_labels, self.ASSET_MODE_CHOICES))
                    current_label = self._asset_mode_label()
                    selected = self._select_from_list(
                        label="Asset Mode",
                        options=option_labels,
                        current_value=current_label,
                        marked_value=current_label,
                        option_details={
                            item.replace("-", " "): self.ASSET_MODE_DETAILS.get(item, "")
                            for item in self.ASSET_MODE_CHOICES
                        },
                    )
                    if selected is None:
                        continue
                    mode_value = label_to_mode.get(selected, "prefer-video")
                    self.config.asset_mode = mode_value
                    if mode_value in {"prefer-images", "images-only"}:
                        self.config.allow_image_assets = True
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: asset mode {selected}.")
                    self._append_log(f"Asset policy: asset mode set to {mode_value}.")
                    continue

                if choice.startswith("Image motion style:"):
                    option_labels = [item.replace("-", " ") for item in self.IMAGE_MOTION_STYLE_CHOICES]
                    label_to_style = dict(zip(option_labels, self.IMAGE_MOTION_STYLE_CHOICES))
                    current_label = self._image_motion_style_label()
                    selected = self._select_from_list(
                        label="Image Motion Style",
                        options=option_labels,
                        current_value=current_label,
                        marked_value=current_label,
                        option_details={
                            item.replace("-", " "): self.IMAGE_MOTION_STYLE_DETAILS.get(item, "")
                            for item in self.IMAGE_MOTION_STYLE_CHOICES
                        },
                    )
                    if selected is None:
                        continue
                    style_value = label_to_style.get(selected, "slow")
                    self.config.image_motion_style = style_value
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: image motion {selected}.")
                    self._append_log(f"Asset policy: image motion style set to {style_value}.")
                    continue

                if choice.startswith("Pexels provider:"):
                    self.config.enable_pexels_provider = not self.config.enable_pexels_provider
                    mode = "On" if self.config.enable_pexels_provider else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: Pexels {mode}.")
                    self._append_log(f"Asset policy: Pexels provider set to {mode}.")
                    continue

                if choice.startswith("Pixabay provider:"):
                    self.config.enable_pixabay_provider = not self.config.enable_pixabay_provider
                    mode = "On" if self.config.enable_pixabay_provider else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: Pixabay {mode}.")
                    self._append_log(f"Asset policy: Pixabay provider set to {mode}.")
                    continue

                if choice.startswith("Coverr provider:"):
                    self.config.enable_coverr_provider = not self.config.enable_coverr_provider
                    mode = "On" if self.config.enable_coverr_provider else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: Coverr {mode}.")
                    self._append_log(f"Asset policy: Coverr provider set to {mode}.")
                    continue

                if choice.startswith("Vecteezy provider:"):
                    self.config.enable_vecteezy_provider = not self.config.enable_vecteezy_provider
                    mode = "On" if self.config.enable_vecteezy_provider else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: Vecteezy {mode}.")
                    self._append_log(f"Asset policy: Vecteezy provider set to {mode}.")
                    continue

                if choice.startswith("Allow still images:"):
                    self.config.allow_image_assets = not self.config.allow_image_assets
                    if not self.config.allow_image_assets and self._normalized_asset_mode() in {"prefer-images", "images-only"}:
                        self.config.asset_mode = "prefer-video"
                    mode = "On" if self.config.allow_image_assets else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: still images {mode}.")
                    self._append_log(f"Asset policy: image fallback set to {mode}.")
                    continue

                if choice.startswith("Attribution-required sources:"):
                    self.config.allow_attribution_required_assets = not self.config.allow_attribution_required_assets
                    mode = "On" if self.config.allow_attribution_required_assets else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Asset policy updated: attribution-required sources {mode}.")
                    self._append_log(f"Asset policy: attribution-required sources set to {mode}.")
                    continue

                if choice == "View provider notes":
                    self._show_paginated_text_modal("Provider Notes", self._asset_provider_notes_text())

    def _asset_policy_option_detail(self, provider_key: str) -> str:
        spec = self._asset_provider_spec(provider_key)
        if not spec:
            return ""
        label = str(spec.get("label") or provider_key).strip()
        env_keys = self._provider_env_keys(spec)
        credential_status = "no credential metadata"
        with self._lock:
            if env_keys:
                present_env_keys = [env_key for env_key in env_keys if self._stock_api_keys.get(env_key)]
                if len(present_env_keys) == len(env_keys):
                    credential_status = "credentials detected"
                else:
                    missing_env_keys = [env_key for env_key in env_keys if env_key not in present_env_keys]
                    credential_status = "missing " + ", ".join(missing_env_keys)
        media = str(spec.get("media") or "").strip()
        notes = str(spec.get("notes") or "").strip()
        return f"{label} | {media} | {credential_status}. {notes}".strip()

    def _asset_provider_notes_text(self) -> str:
        lines = [
            "Current live provider notes:",
            "",
        ]
        for spec in self._asset_provider_specs():
            label = str(spec.get("label") or "Unknown").strip()
            env_keys = self._provider_env_keys(spec)
            media = str(spec.get("media") or "").strip()
            notes = str(spec.get("notes") or "").strip()
            enabled = "yes" if self._is_provider_enabled(str(spec.get("key") or "")) else "no"
            with self._lock:
                present_keys = [env_key for env_key in env_keys if self._stock_api_keys.get(env_key)]
            lines.append(f"{label}")
            lines.append(f"Enabled: {enabled}")
            if env_keys:
                lines.append(
                    "Credential envs: "
                    + ", ".join(env_keys)
                    + " | Present: "
                    + ("yes" if len(present_keys) == len(env_keys) else "partial/no")
                )
            if media:
                lines.append(f"Media: {media}")
            if notes:
                lines.append(notes)
        lines.append("")
        lines.append("General policy")
        lines.append(f"Asset mode: {self._asset_mode_label()}")
        lines.append(f"Allow still images: {'yes' if self.config.allow_image_assets else 'no'}")
        lines.append(f"Image motion style: {self._image_motion_style_label()}")
        lines.append(
            "Allow attribution-required sources: "
            + ("yes" if self.config.allow_attribution_required_assets else "no")
        )
        lines.append(
            "YouTube credits export: generated automatically when a manifest is written."
        )
        return "\n".join(lines).strip()

    def _run_debug_voice_speaker_test(self) -> None:
        project_dir = self._resolve_debug_voice_project_dir()
        try:
            entries, label_to_entry, current_label, _had_warning = self._run_with_spinner_modal(
                title="Debug Voices",
                message="Loading voices",
                task=self._combined_voice_entries_for_selection,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Debug voices failed while loading voices: {exc}")
            self._set_status("Debug voices: failed loading selectable voices.")
            return

        if not entries:
            self._set_status("Debug voices: no selectable voices available.")
            return

        labels = [str(item.get("label") or "").strip() for item in entries if str(item.get("label") or "").strip()]
        if not labels:
            self._set_status("Debug voices: no selectable voices available.")
            return

        if not current_label:
            current_label = labels[0]

        option_details = {
            str(item.get("label") or ""): self._voice_selection_option_detail(item)
            for item in entries
            if str(item.get("label") or "").strip()
        }
        while True:
            selected_label = self._select_from_list(
                label="Debug voices",
                options=labels,
                current_value=current_label,
                option_details=option_details,
            )
            if selected_label is None:
                self._set_status("Debug voices: closed.")
                return

            selected_value = cast(str, selected_label)
            current_label = selected_value
            selected_entry = label_to_entry.get(selected_value)
            if selected_entry is None:
                self._set_status("Debug voices: selected voice is invalid.")
                continue

            _lang = str(selected_entry.get("lang_code") or "")
            phrase = self.DEBUG_VOICE_TEST_PHRASES.get(_lang, self.DEBUG_VOICE_TEST_PHRASE)
            self._preview_debug_voice_entry(project_dir, selected_value, selected_entry, phrase)
            continue

    def _preview_debug_voice_entry(
        self,
        project_dir: Path,
        selected_label: str,
        selected_entry: dict[str, Any],
        phrase: str,
    ) -> None:
        policy = cast(dict[str, Any], selected_entry.get("policy") or self._tts_policy_for_entry(selected_entry))
        if str(policy.get("policy_result") or "") == "deny":
            selection = str(policy.get("voice_display") or selected_label).strip() or selected_label
            reason = str(policy.get("reason") or "Voice is blocked by policy.").strip()
            self._append_log(f"WARN: Voice preview blocked by TTS policy: {selection}. {reason}")
            self._set_status(f"Voice preview blocked: {selection}.")
            return

        engine = str(selected_entry.get("engine") or "").strip().lower()
        if engine == "melo":
            speaker_id = str(selected_entry.get("speaker") or "").strip()
            if not speaker_id:
                self._set_status("Voice preview: selected Melo speaker is invalid.")
                return

            try:
                sample_path = self._run_with_spinner_modal(
                    title="Voice Preview",
                    message=f"Generating sample for {selected_label}",
                    task=lambda: self._generate_debug_voice_sample(
                        project_dir=project_dir,
                        speaker=speaker_id,
                        phrase=phrase,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERROR: Voice preview Melo generation failed: {exc}")
                self._set_status("Voice preview: Melo generation failed. See logs.")
                return

            if sample_path is None or not sample_path.exists():
                self._set_status("Voice preview: generated Melo audio file is missing.")
                return

            sample_file = cast(Path, sample_path)
            playback_cancel = threading.Event()
            self._set_status(f"Voice preview: playing {selected_label}.")
            try:
                played = self._run_with_spinner_modal(
                    title="Voice Preview",
                    message=f"Playing {selected_label}",
                    detail_text=phrase,
                    allow_cancel=True,
                    cancel_event=playback_cancel,
                    task=lambda: self._play_media_path(
                        sample_file,
                        label=f"debug-melo-{speaker_id}",
                        audio_only=True,
                        cancel_event=playback_cancel,
                    ),
                )
            except SpinnerCancelled:
                self._set_status("Voice preview: cancelled. Pick another voice or Esc to exit.")
                return

            if played:
                self._set_status("Voice preview: playback complete. Pick another voice or Esc to exit.")
            else:
                self._set_status("Voice preview: playback failed. Pick another voice or Esc to exit.")
            return

        if engine == "kokoro":
            voice = str(selected_entry.get("voice") or "").strip()
            lang_code = normalize_kokoro_lang_code(str(selected_entry.get("lang_code") or self.config.kokoro_lang_code))
            if not voice:
                self._set_status("Voice preview: selected Kokoro voice is invalid.")
                return

            try:
                sample_path = self._run_with_spinner_modal(
                    title="Kokoro Voices",
                    message=f"Preparing {selected_label}",
                    task=lambda: self._generate_debug_kokoro_voice_sample(
                        project_dir=project_dir,
                        lang_code=lang_code,
                        voice=voice,
                        phrase=phrase,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                short = str(exc).split("\n")[0][:120]
                self._append_log(f"ERROR: Voice preview Kokoro generation failed: {exc}")
                self._set_status(f"Voice preview failed: {short}")
                return

            if sample_path is None or not sample_path.exists():
                self._set_status("Voice preview: generated Kokoro audio file is missing.")
                return

            sample_file = cast(Path, sample_path)
            playback_cancel = threading.Event()
            self._set_status(f"Voice preview: playing {selected_label}.")
            try:
                played = self._run_with_spinner_modal(
                    title="Kokoro Voice Preview",
                    message=f"Playing {selected_label}",
                    detail_text=phrase,
                    allow_cancel=True,
                    cancel_event=playback_cancel,
                    task=lambda: self._play_media_path(
                        sample_file,
                        label=f"debug-kokoro-{self._safe_filename_token(voice)}",
                        audio_only=True,
                        cancel_event=playback_cancel,
                    ),
                )
            except SpinnerCancelled:
                self._set_status("Voice preview: cancelled. Pick another voice or Esc to exit.")
                return

            if played:
                self._set_status("Voice preview: playback complete. Pick another voice or Esc to exit.")
            else:
                self._set_status("Voice preview: playback failed. Pick another voice or Esc to exit.")
            return

        if engine == "piper":
            piper_cmd = self._resolve_piper_command()
            if not piper_cmd:
                self._show_piper_not_installed_modal()
                return

            self._append_log(f"Voice preview Piper runtime: {shlex.join(piper_cmd)}")
            voice_meta = cast(dict[str, Any], selected_entry.get("voice_meta") or {})
            voice_id = str(voice_meta.get("id") or "").strip()
            license_note = str(voice_meta.get("license_note") or "").strip()
            self._append_log(f"Voice preview Piper voice selected: {selected_label} ({voice_id})")
            if license_note:
                self._append_log(f"Voice preview Piper license note: {license_note}")

            try:
                sample_path = self._run_with_spinner_modal(
                    title="Piper Voices",
                    message=f"Preparing {selected_label}",
                    task=lambda: self._generate_debug_piper_voice_sample(
                        project_dir=project_dir,
                        piper_command=piper_cmd,
                        voice_meta=voice_meta,
                        phrase=phrase,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERROR: Voice preview Piper generation failed: {exc}")
                self._set_status("Voice preview: Piper generation failed. See logs.")
                return

            if sample_path is None or not sample_path.exists():
                self._set_status("Voice preview: generated Piper audio file is missing.")
                return

            sample_file = cast(Path, sample_path)
            playback_cancel = threading.Event()
            self._set_status(f"Voice preview: playing {selected_label}.")
            try:
                played = self._run_with_spinner_modal(
                    title="Piper Voice Preview",
                    message=f"Playing {selected_label}",
                    detail_text=phrase,
                    allow_cancel=True,
                    cancel_event=playback_cancel,
                    task=lambda: self._play_media_path(
                        sample_file,
                        label=f"debug-piper-{self._safe_filename_token(voice_id or selected_label)}",
                        audio_only=True,
                        cancel_event=playback_cancel,
                    ),
                )
            except SpinnerCancelled:
                self._set_status("Voice preview: cancelled. Pick another voice or Esc to exit.")
                return

            if played:
                self._set_status("Voice preview: playback complete. Pick another voice or Esc to exit.")
            else:
                self._set_status("Voice preview: playback failed. Pick another voice or Esc to exit.")
            return

        self._set_status("Debug voices: selected engine is unsupported.")

    def _sanitize_drawtext_text(self, value: str) -> str:
        cleaned_lines: list[str] = []
        for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            safe_line = "".join(ch for ch in raw_line if 32 <= ord(ch) <= 126).strip()
            if safe_line:
                cleaned_lines.append(safe_line)
        return "\n".join(cleaned_lines).strip()

    def _escape_drawtext_path(self, path: Path) -> str:
        value = str(path.expanduser().resolve()).replace("\\", r"\\")
        value = value.replace(":", r"\:")
        value = value.replace("'", r"\'")
        value = value.replace(",", r"\,")
        value = value.replace("%", r"\%")
        return value

    def _show_piper_not_installed_modal(self) -> None:
        self._append_log("WARN: Piper runtime not found for debug voice preview.")
        self._set_status("Debug voices: Piper is not installed in this environment.")
        self._show_paginated_text_modal(
            title="Piper Not Installed",
            body=(
                "Piper runtime was not found for this TUI session.\n\n"
                f"Current Python: {sys.executable}\n\n"
                "Install Piper in the same environment, then restart TUI:\n"
                f"{sys.executable} -m pip install piper-tts"
            ),
        )

    def _resolve_piper_command(self) -> list[str] | None:
        direct_candidates: list[Path] = []
        from_path = shutil.which("piper")
        if from_path:
            direct_candidates.append(Path(from_path))

        venv_piper = (self._repo_root / ".venv" / "bin" / "piper").resolve()
        if venv_piper.exists() and os.access(str(venv_piper), os.X_OK):
            direct_candidates.append(venv_piper)

        seen_direct: set[str] = set()
        for candidate in direct_candidates:
            key = str(candidate)
            if key in seen_direct:
                continue
            seen_direct.add(key)
            return [key]

        python_candidates: list[Path] = []
        python_candidates.append(Path(sys.executable).resolve())

        venv_python = (self._repo_root / ".venv" / "bin" / "python").resolve()
        if venv_python.exists() and os.access(str(venv_python), os.X_OK):
            python_candidates.append(venv_python)

        from_path_python = shutil.which("python3")
        if from_path_python:
            python_candidates.append(Path(from_path_python).resolve())

        seen_python: set[str] = set()
        for py in python_candidates:
            py_str = str(py)
            if py_str in seen_python:
                continue
            seen_python.add(py_str)

            try:
                probe = subprocess.run(
                    [py_str, "-m", "piper", "--help"],
                    cwd=str(self._repo_root),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                )
                if int(probe.returncode or 0) == 0:
                    return [py_str, "-m", "piper"]
            except Exception:
                continue

        return None

    def _generate_debug_piper_voice_sample(
        self,
        *,
        project_dir: Path,
        piper_command: list[str],
        voice_meta: dict[str, Any],
        phrase: str,
    ) -> Path | None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin is None:
            self._append_log("WARN: ffmpeg not found for Piper debug conversion.")
            return None

        model_path, config_path = self._ensure_piper_voice_assets(voice_meta)
        if model_path is None or config_path is None:
            return None

        voice_id = str(voice_meta.get("id") or "voice")
        safe_name = self._safe_filename_token(voice_id)
        output_dir = project_dir / "review" / "debug" / "piper_voice"
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_wav = output_dir / f"{safe_name}.raw.wav"
        wav_path = output_dir / f"{safe_name}.wav"

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
                "1.0",
            ]
        )
        speaker_id = voice_meta.get("speaker_id")
        if speaker_id is not None:
            command.extend(["--speaker", str(int(speaker_id))])
        piper_run = subprocess.run(
            command,
            cwd=str(self._repo_root),
            check=False,
            input=phrase,
            text=True,
            capture_output=True,
            timeout=180,
        )
        if int(piper_run.returncode or 0) != 0:
            stderr_text = str(piper_run.stderr or "").strip()
            self._append_log(f"WARN: Piper synthesis failed: {stderr_text}")
            if "No module named 'pathvalidate'" in stderr_text:
                self._append_log(
                    "WARN: Piper dependency missing. Install with: "
                    f"{sys.executable} -m pip install pathvalidate"
                )
            return None

        ffmpeg_run = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(raw_wav),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            cwd=str(self._repo_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if int(ffmpeg_run.returncode or 0) != 0:
            self._append_log(f"WARN: ffmpeg conversion failed for Piper debug voice: {str(ffmpeg_run.stderr or '').strip()}")
            return None

        if wav_path.exists():
            return wav_path
        return None

    def _ensure_piper_voice_assets(self, voice_meta: dict[str, Any]) -> tuple[Path | None, Path | None]:
        voice_id = str(voice_meta.get("id") or "voice").strip() or "voice"
        model_url = str(voice_meta.get("model_url") or "").strip()
        config_url = str(voice_meta.get("config_url") or "").strip()
        if not model_url or not config_url:
            self._append_log(f"WARN: Piper voice `{voice_id}` is missing model/config URLs.")
            return None, None

        voice_dir = (Path.home() / ".imagine" / "models" / "piper" / voice_id).resolve()
        voice_dir.mkdir(parents=True, exist_ok=True)
        model_path = voice_dir / f"{voice_id}.onnx"
        config_path = voice_dir / f"{voice_id}.onnx.json"

        if not model_path.exists():
            if not self._download_file(model_url, model_path):
                return None, None
        if not config_path.exists():
            if not self._download_file(config_url, config_path):
                return None, None

        return model_path, config_path

    def _download_file(self, url: str, destination: Path) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
            return True
        except urllib.error.URLError as exc:
            self._append_log(f"WARN: Download failed for {url}: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Download failed for {url}: {exc}")
            return False

    def _safe_filename_token(self, value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("_")
        return token or "sample"

    def _debug_speaker_option_entries(self, speakers: list[str]) -> list[tuple[str, str]]:
        ordered = [str(value).strip() for value in speakers if str(value).strip()]
        if not ordered:
            return []

        entries: list[tuple[str, str]] = []
        used_labels: set[str] = set()
        for speaker in ordered:
            display_id = self._debug_display_speaker_id(speaker)
            label = f"{display_id} (female)"
            if label in used_labels:
                continue
            entries.append((label, speaker))
            used_labels.add(label)

        return entries

    def _debug_display_speaker_id(self, speaker: str) -> str:
        value = str(speaker).strip()
        upper = value.upper().replace("-", "_")
        if upper == "EN_INDIA":
            return "EN-IN"
        return value

    def _combined_voice_entries_for_selection(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str, bool]:
        melo_speakers: list[str] = []
        had_warning = False
        try:
            loaded = self._load_melo_speakers("EN")
            melo_speakers = [str(item).strip() for item in loaded if str(item).strip()]
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Voice picker failed loading Melo speakers: {exc}")
            had_warning = True

        if self.config.melo_speaker and self.config.melo_speaker not in melo_speakers:
            melo_speakers.insert(0, self.config.melo_speaker)

        entries: list[dict[str, Any]] = []
        for speaker_label, speaker_id in self._debug_speaker_option_entries(melo_speakers):
            entries.append(
                {
                    "label": f"[Melo] {speaker_label}",
                    "engine": "melo",
                    "speaker": speaker_id,
                }
            )

        entries.extend(self._kokoro_voice_selection_entries())

        for voice_meta in self.DEBUG_PIPER_VOICES:
            piper_label = str(voice_meta.get("label") or voice_meta.get("id") or "Piper voice").strip()
            if not piper_label:
                continue
            entries.append(
                {
                    "label": f"[Piper] {piper_label}",
                    "engine": "piper",
                    "voice_meta": dict(voice_meta),
                }
            )

        entries = self._decorate_voice_selection_entries(entries)
        by_label = {str(item.get("label") or ""): item for item in entries if str(item.get("label") or "")}
        current_label = self._current_voice_selection_label(entries)

        if not current_label and entries:
            current_label = str(entries[0].get("label") or "")

        return entries, by_label, current_label, had_warning

    def _current_voice_selection_label(self, entries: list[dict[str, Any]]) -> str:
        engine = str(self.config.tts_engine or "melo").strip().lower()
        if engine == "piper":
            selected_meta = self._selected_piper_voice_meta()
            selected_id = str(selected_meta.get("id") or "").strip()
            selected_speaker = selected_meta.get("speaker_id")
            for item in entries:
                if str(item.get("engine") or "") != "piper":
                    continue
                voice_meta = item.get("voice_meta")
                if not isinstance(voice_meta, dict):
                    continue
                meta_id = str(voice_meta.get("id") or "").strip()
                meta_speaker = voice_meta.get("speaker_id")
                if meta_id == selected_id and meta_speaker == selected_speaker:
                    return str(item.get("label") or "")
            return ""

        if engine == "kokoro":
            selected_voice = str(self.config.kokoro_voice or "").strip()
            selected_lang = normalize_kokoro_lang_code(self.config.kokoro_lang_code)
            for item in entries:
                if str(item.get("engine") or "") != "kokoro":
                    continue
                voice = str(item.get("voice") or "").strip()
                lang_code = normalize_kokoro_lang_code(str(item.get("lang_code") or selected_lang))
                if voice == selected_voice and lang_code == selected_lang:
                    return str(item.get("label") or "")
            return ""

        for item in entries:
            if str(item.get("engine") or "") == "melo" and str(item.get("speaker") or "") == self.config.melo_speaker:
                return str(item.get("label") or "")
        return ""

    def _apply_voice_selection_entry(self, selected_entry: dict[str, Any]) -> bool:
        policy = cast(dict[str, Any], selected_entry.get("policy") or self._tts_policy_for_entry(selected_entry))
        if str(policy.get("policy_result") or "") == "deny":
            selection = str(
                policy.get("voice_display")
                or selected_entry.get("base_label")
                or selected_entry.get("label")
                or "voice"
            ).strip()
            reason = str(policy.get("reason") or "Voice is blocked by policy.").strip()
            self._append_log(f"WARN: TTS policy blocked voice picker selection: {selection}. {reason}")
            self._set_status(f"Voice picker blocked: {selection}.")
            return False

        engine = str(selected_entry.get("engine") or "").strip().lower()
        if engine == "melo":
            self.config.tts_engine = "melo"
            self.config.melo_language = "EN"
            self.config.melo_speaker = str(selected_entry.get("speaker") or self.config.melo_speaker)
            return True

        if engine == "kokoro":
            self.config.tts_engine = "kokoro"
            self.config.kokoro_lang_code = normalize_kokoro_lang_code(str(selected_entry.get("lang_code") or "en-us"))
            selected_voice = str(selected_entry.get("voice") or "").strip()
            self.config.kokoro_voice = selected_voice or default_kokoro_voice(self.config.kokoro_lang_code)
            return True

        if engine == "piper":
            voice_meta = selected_entry.get("voice_meta")
            if isinstance(voice_meta, dict):
                self.config.tts_engine = "piper"
                self.config.piper_voice_id = str(voice_meta.get("id") or "").strip()
                speaker_id = voice_meta.get("speaker_id")
                self.config.piper_speaker_id = int(speaker_id) if speaker_id is not None else None
                return True
        return False

    def _kokoro_voice_selection_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for lang_code in self.KOKORO_TUI_LANG_CHOICES:
            lang_label = self.KOKORO_TUI_LANG_LABELS.get(lang_code, lang_code.upper())
            for voice in kokoro_voice_choices_for_lang(lang_code):
                key = (lang_code, voice)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    {
                        "label": f"[Kokoro] {voice} ({lang_label})",
                        "engine": "kokoro",
                        "lang_code": lang_code,
                        "voice": voice,
                    }
                )

        configured_lang = normalize_kokoro_lang_code(self.config.kokoro_lang_code)
        configured_voice = str(self.config.kokoro_voice or "").strip()
        configured_key = (configured_lang, configured_voice)
        if configured_voice and configured_key not in seen and configured_lang in self.KOKORO_TUI_LANG_CHOICES:
            lang_label = self.KOKORO_TUI_LANG_LABELS.get(configured_lang, configured_lang.upper())
            entries.insert(
                0,
                {
                    "label": f"[Kokoro] {configured_voice} ({lang_label}, custom)",
                    "engine": "kokoro",
                    "lang_code": configured_lang,
                    "voice": configured_voice,
                },
            )

        return entries

    def _generate_debug_voice_sample(self, *, project_dir: Path, speaker: str, phrase: str) -> Path | None:
        pipeline = self._pipeline_for_project(project_dir)
        original_engine = pipeline.config.tts_engine
        original_speaker = pipeline.config.melo_speaker
        safe_speaker = re.sub(r"[^A-Za-z0-9._-]+", "_", str(speaker)).strip("_") or "speaker"
        try:
            pipeline.config.tts_engine = "melo"
            pipeline.config.melo_speaker = str(speaker).strip() or original_speaker
            report = pipeline.synthesize_scene_narration_preview(
                scene_id=f"debug_voice_{safe_speaker}",
                text=phrase,
            )
            wav_path = Path(str(report.get("wav_path") or "")).expanduser().resolve()
            if wav_path.exists():
                return wav_path
            return None
        finally:
            pipeline.config.tts_engine = original_engine
            pipeline.config.melo_speaker = original_speaker

    def _generate_debug_kokoro_voice_sample(
        self,
        *,
        project_dir: Path,
        lang_code: str,
        voice: str,
        phrase: str,
    ) -> Path | None:
        pipeline = self._pipeline_for_project(project_dir)
        original_engine = pipeline.config.tts_engine
        original_lang = pipeline.config.kokoro_lang_code
        original_voice = pipeline.config.kokoro_voice
        safe_voice = re.sub(r"[^A-Za-z0-9._,-]+", "_", str(voice)).strip("_") or "voice"
        try:
            pipeline.config.tts_engine = "kokoro"
            pipeline.config.kokoro_lang_code = normalize_kokoro_lang_code(lang_code)
            pipeline.config.kokoro_voice = str(voice).strip() or default_kokoro_voice(lang_code)
            report = pipeline.synthesize_scene_narration_preview(
                scene_id=f"debug_kokoro_{safe_voice}",
                text=phrase,
            )
            wav_path = Path(str(report.get("wav_path") or "")).expanduser().resolve()
            if wav_path.exists():
                return wav_path
            return None
        finally:
            pipeline.config.tts_engine = original_engine
            pipeline.config.kokoro_lang_code = original_lang
            pipeline.config.kokoro_voice = original_voice

    def _resolve_debug_voice_project_dir(self) -> Path:
        if self._active_project_dir is not None:
            return self._active_project_dir

        latest = self._latest_project_workspace()
        if latest is not None:
            return latest

        root = self.config.project_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _load_clip_catalog_entries(self, project_dir: Path) -> list[dict[str, str]]:
        clip_catalog_path = project_dir / "review" / "clip_catalog.json"
        if not clip_catalog_path.exists():
            return []

        try:
            payload = json.loads(clip_catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse clip catalog {clip_catalog_path}: {exc}")
            return []

        clips = payload.get("clips") if isinstance(payload, dict) else None
        if not isinstance(clips, list):
            return []

        entries: list[dict[str, str]] = []
        for item in clips:
            if not isinstance(item, dict):
                continue

            clip_name = str(item.get("clip_name") or "").strip()
            if not clip_name:
                continue
            if clip_name in {"intro-card", "outro-card"}:
                continue

            heading = str(item.get("heading") or "").strip() or "(untitled scene)"
            provider = str(item.get("asset_provider") or "").strip()
            scene_id = str(item.get("scene_id") or "").strip()
            entries.append(
                {
                    "clip_name": clip_name,
                    "heading": heading,
                    "provider": provider,
                    "scene_id": scene_id,
                }
            )

        return entries

    def _run_with_spinner_modal(
        self,
        *,
        title: str,
        message: str,
        task: Callable[[], T],
        detail_text: str | None = None,
        allow_cancel: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> T:
        if self._stdscr is None:
            return task()

        with self._modal_focus():
            result: dict[str, Any] = {}
            error: dict[str, BaseException] = {}
            done = threading.Event()

            def worker() -> None:
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        result["value"] = task()
                except BaseException as exc:  # noqa: BLE001
                    error["exc"] = exc
                finally:
                    done.set()

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()

            stdscr = self._stdscr
            frame_index = 0
            while not done.is_set():
                self._draw()
                height, width = stdscr.getmaxyx()
                modal_width = min(max(52, len(message) + 14), max(22, width - 2))
                has_status_line = bool(str(self._get_status() or "").strip())
                modal_height = 8 if not detail_text else 11
                if has_status_line:
                    modal_height += 1
                if modal_width >= 22 and height >= modal_height + 1:
                    top = max(0, (height - modal_height) // 2)
                    left = max(0, (width - modal_width) // 2)
                    win = curses.newwin(modal_height, modal_width, top, left)
                    win.keypad(True)
                    win.nodelay(True)
                    win.erase()
                    try:
                        win.box()
                    except curses.error:
                        pass

                    spinner = self.SPINNER_FRAMES[frame_index % len(self.SPINNER_FRAMES)]
                    title_text = self._trim_tail(f" {title} ", max(1, modal_width - 4))
                    body_text = self._trim_tail(f"{spinner} {message}...", modal_width - 4)
                    status_text = self._trim_tail(str(self._get_status() or "").strip(), modal_width - 4)
                    footer = "Esc cancel" if allow_cancel else "Please wait"
                    try:
                        win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                        win.addstr(2, 2, body_text)
                        detail_row = 4
                        if has_status_line:
                            win.addstr(4, 2, status_text, self._attr("ok", bold=True))
                            detail_row = 5
                        if detail_text:
                            wrapped = textwrap.wrap(str(detail_text), width=max(20, modal_width - 6))
                            for index, line in enumerate(wrapped[:3]):
                                win.addstr(detail_row + index, 2, self._trim_tail(line, modal_width - 4), self._attr("muted"))
                        win.addstr(modal_height - 1, 2, self._trim_tail(footer, modal_width - 4), self._attr("muted"))
                    except curses.error:
                        pass
                    win.refresh()
                    if allow_cancel:
                        key = win.getch()
                        if key == 27:
                            if cancel_event is not None:
                                cancel_event.set()
                            done.wait(timeout=2.0)
                            raise SpinnerCancelled("Spinner task cancelled by user")

                frame_index += 1
                done.wait(timeout=0.08)

            thread.join(timeout=0.05)
            if "exc" in error:
                raise error["exc"]
            if "value" not in result:
                raise RuntimeError("Spinner task finished without returning a value.")

            return cast(T, result["value"])

    def _prompt_yes_no(self, title: str, body: str, default_yes: bool = False) -> bool:
        if self._stdscr is None:
            return default_yes

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            modal_width = min(max(52, len(body) + 8), max(18, width - 2))
            modal_height = 8
            if modal_width < 18 or height < modal_height + 1:
                return default_yes

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {title} ", max(1, modal_width - 4))
                body_text = self._trim_tail(body, modal_width - 4)
                if default_yes:
                    hint = "Enter yes | N no | Esc cancel"
                else:
                    hint = "Y yes | Enter no | Esc cancel"

                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(3, 2, body_text)
                    win.addstr(modal_height - 1, 2, self._trim_tail(hint, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                win.refresh()
                key = win.getch()
                if key in (27, ord("n"), ord("N")):
                    return False
                if key in (ord("y"), ord("Y")):
                    return True
                if key in (10, 13, curses.KEY_ENTER):
                    return default_yes

    def _prompt_confirm_enter_escape(self, title: str, body: str) -> bool:
        if self._stdscr is None:
            return True

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()
            modal_width = min(max(52, len(body) + 8), max(18, width - 2))
            modal_height = 8
            if modal_width < 18 or height < modal_height + 1:
                return True

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)
            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {title} ", max(1, modal_width - 4))
                body_text = self._trim_tail(body, modal_width - 4)
                hint = "Enter yes | Esc back"

                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(3, 2, body_text)
                    win.addstr(modal_height - 1, 2, self._trim_tail(hint, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                win.refresh()
                key = win.getch()
                if key == 27:
                    return False
                if key in (10, 13, curses.KEY_ENTER):
                    return True

    def _select_multiple_clips(self, entries: list[dict[str, str]]) -> list[int] | None:
        if self._stdscr is None:
            return None
        if not entries:
            return []

        with self._modal_focus():
            stdscr = self._stdscr
            selected: set[int] = set()
            cursor = 0
            start_index = 0

            while True:
                self._draw()
                height, width = stdscr.getmaxyx()

                max_name_len = 0
                for item in entries:
                    clip_name = str(item.get("clip_name") or "")
                    heading = str(item.get("heading") or "")
                    candidate = f"[ ] {clip_name} | {heading}"
                    max_name_len = max(max_name_len, len(candidate))

                modal_width = min(max(64, max_name_len + 4), max(20, width - 2))
                max_modal_height = max(10, height - 2)
                visible_rows = max(1, max_modal_height - 4)
                list_rows = min(len(entries), visible_rows)
                modal_height = list_rows + 4

                top = max(0, (height - modal_height) // 2)
                left = max(0, (width - modal_width) // 2)

                win = curses.newwin(modal_height, modal_width, top, left)
                win.keypad(True)
                win.nodelay(False)
                win.timeout(-1)

                if cursor < start_index:
                    start_index = cursor
                elif cursor >= start_index + list_rows:
                    start_index = cursor - list_rows + 1

                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title = self._trim_tail(" Replace Clips ", max(1, modal_width - 4))
                footer = "Space mark | Enter replace | Esc cancel"
                try:
                    win.addstr(0, 2, title, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(footer, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                for row in range(list_rows):
                    index = start_index + row
                    if index >= len(entries):
                        break
                    item = entries[index]
                    clip_name = str(item.get("clip_name") or "")
                    heading = str(item.get("heading") or "")
                    mark = "[x]" if index in selected else "[ ]"
                    line = self._trim_tail(f"{mark} {clip_name} | {heading}", modal_width - 2)
                    attr = curses.A_REVERSE if index == cursor else 0
                    try:
                        win.addstr(1 + row, 1, line, attr)
                    except curses.error:
                        pass

                win.refresh()
                key = win.getch()

                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    cursor = (cursor - 1) % len(entries)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    cursor = (cursor + 1) % len(entries)
                    continue
                if key == ord(" "):
                    if cursor in selected:
                        selected.remove(cursor)
                    else:
                        selected.add(cursor)
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    return sorted(selected)
                if key == 27:
                    return None

    def _start_replace_clips_workflow(self, project_dir: Path, clip_names: list[str]) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        chosen = [str(name).strip() for name in clip_names if str(name).strip()]
        if not chosen:
            self._set_status("No clip names selected for replacement.")
            return

        self._active_project_dir = project_dir
        self._pending_export_path = self._downloads_export_mp4_path(project_dir)

        self._mark_command_start(workflow_kind="replace")
        self._set_running(True)
        self._set_status(f"Replacing {len(chosen)} clip(s) in {project_dir.name}...")
        self._worker = threading.Thread(target=self._replace_clips_workflow, args=(chosen,), daemon=True)
        self._worker.start()

    def _replace_clips_workflow(self, clip_names: list[str]) -> None:
        try:
            self._refresh_stock_key_cache()
            if not self._passes_asset_hard_guard_precheck():
                self._set_status("Hard guard: missing stock API keys. Replacement blocked.")
                return

            replace_code = self._run_and_stream(self._build_replace_clips_command(clip_names), label="replace")
            if replace_code == 0:
                self._set_status("Replacement succeeded. Inspecting outputs...")
                inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
                exported_mp4, exported_thumbnail = self._export_project_outputs_to_downloads()

                if inspect_code == 0 and exported_mp4 is not None:
                    if exported_thumbnail is not None:
                        self._set_status("Replacement complete. MP4 and thumbnail exported to Downloads.")
                    else:
                        self._set_status(f"Replacement complete. MP4 exported to {exported_mp4.name}.")
                elif inspect_code == 0:
                    self._set_status("Replacement complete. MP4 export skipped.")
                else:
                    self._set_status(f"Replacement succeeded, inspect failed with exit code {inspect_code}.")

                if self._active_project_dir is not None:
                    clip_catalog = self._active_project_dir / "review" / "clip_catalog.json"
                    if clip_catalog.exists():
                        self._append_log(f"Clip catalog: {clip_catalog}")
            else:
                queued_unique_prompt = self._queue_unique_asset_prompt_from_run_report()
                if queued_unique_prompt:
                    self._set_status("Replacement paused: broaden asset keywords to unlock more unique clips.")
                else:
                    self._set_status(f"Replacement failed with exit code {replace_code}.")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: {exc}")
            self._set_status("Replacement failed before completion.")
        finally:
            self._mark_command_stop()
            self._set_running(False)

    def _build_replace_clips_command(
        self,
        clip_names: list[str],
        *,
        project_dir: Path | None = None,
        asset_keywords: list[str] | None = None,
    ) -> list[str]:
        resolved_project_dir = project_dir or self._active_project_dir or self._latest_project_workspace()
        if resolved_project_dir is None:
            resolved_project_dir = self.config.project_dir

        normalized = [str(name).strip().lower() for name in clip_names if str(name).strip()]
        command = [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "replace-clips",
            "--project-dir",
            str(resolved_project_dir),
            "--clip-names",
            *normalized,
            "--require-external-assets",
            "--enable-pexels-provider" if self.config.enable_pexels_provider else "--no-enable-pexels-provider",
            "--enable-pixabay-provider" if self.config.enable_pixabay_provider else "--no-enable-pixabay-provider",
            "--enable-coverr-provider" if self.config.enable_coverr_provider else "--no-enable-coverr-provider",
            "--enable-vecteezy-provider" if self.config.enable_vecteezy_provider else "--no-enable-vecteezy-provider",
            "--allow-image-assets" if self.config.allow_image_assets else "--no-allow-image-assets",
            "--asset-mode",
            self._normalized_asset_mode(),
            "--image-motion-style",
            self._normalized_image_motion_style(),
            (
                "--allow-attribution-required-assets"
                if self.config.allow_attribution_required_assets
                else "--no-allow-attribution-required-assets"
            ),
            "--verbose",
        ]

        keyword_values = asset_keywords if asset_keywords is not None else self.config.asset_keywords
        if keyword_values:
            command.extend(["--asset-keywords", ", ".join(keyword_values)])

        return command

    def _elapsed_seconds(self) -> float:
        with self._lock:
            started = self._run_started_at
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - started)

    def _mark_command_start(self, workflow_kind: str) -> None:
        self._refresh_prior_total_seconds()
        with self._lock:
            self._run_started_at = time.monotonic()
            self._last_elapsed_seconds = None
            self._workflow_kind = workflow_kind
            self._stage_label = None
            self._stage_index = None
            self._stage_total = None

    def _mark_command_stop(self) -> None:
        with self._lock:
            if self._run_started_at is not None:
                self._last_elapsed_seconds = max(0.0, time.monotonic() - self._run_started_at)
            self._run_started_at = None
            self._workflow_kind = None
            self._stage_label = None
            self._stage_index = None
            self._stage_total = None

    def _clean_projects(self) -> None:
        if self._is_running():
            self._set_status("Cannot clean workspaces while run is in progress.")
            return

        workspaces = self._iter_project_workspaces()
        if not workspaces:
            self._set_status("No workspaces to clean.")
            return

        selected = self._select_multiple_workspaces(workspaces)
        if selected is None:
            self._set_status("Cleanup cancelled.")
            return
        if not selected:
            self._set_status("No workspaces selected.")
            return

        deleted = 0
        failed = 0
        selected_paths = [workspaces[index] for index in selected]

        for path in selected_paths:
            try:
                shutil.rmtree(path)
                deleted += 1
                self._append_log(f"Deleted workspace: {path}")
                if self._active_project_dir is not None and path == self._active_project_dir:
                    self._active_project_dir = None
                    self._pending_export_path = None
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self._append_log(f"ERROR: Failed to delete workspace {path}: {exc}")

        if failed > 0:
            self._set_status(f"Cleanup finished: deleted {deleted}, failed {failed}.")
        else:
            self._set_status(f"Cleanup finished: deleted {deleted} workspace(s).")

    def _select_multiple_labels(
        self,
        *,
        title: str,
        labels: list[str],
        footer: str,
        preselected: set[int] | None = None,
    ) -> list[int] | None:
        if self._stdscr is None:
            return None
        if not labels:
            return []

        with self._modal_focus():
            stdscr = self._stdscr
            selected: set[int] = set(preselected or set())
            selected = {index for index in selected if 0 <= index < len(labels)}
            cursor = 0
            start_index = 0

            while True:
                self._draw()
                height, width = stdscr.getmaxyx()

                max_name_len = max(len(label) for label in labels)
                modal_width = min(max(56, max_name_len + 8), max(20, width - 2))
                max_modal_height = max(8, height - 2)
                visible_rows = max(1, max_modal_height - 4)
                list_rows = min(len(labels), visible_rows)
                modal_height = list_rows + 4

                top = max(0, (height - modal_height) // 2)
                left = max(0, (width - modal_width) // 2)

                win = curses.newwin(modal_height, modal_width, top, left)
                win.keypad(True)
                win.nodelay(False)
                win.timeout(-1)

                if cursor < start_index:
                    start_index = cursor
                elif cursor >= start_index + list_rows:
                    start_index = cursor - list_rows + 1

                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {title} ", max(1, modal_width - 4))
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(footer, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                for row in range(list_rows):
                    index = start_index + row
                    if index >= len(labels):
                        break
                    mark = "[x]" if index in selected else "[ ]"
                    line = self._trim_tail(f"{mark} {labels[index]}", modal_width - 2)
                    attr = curses.A_REVERSE if index == cursor else 0
                    try:
                        win.addstr(1 + row, 1, line, attr)
                    except curses.error:
                        pass

                win.refresh()
                key = win.getch()

                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    cursor = (cursor - 1) % len(labels)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    cursor = (cursor + 1) % len(labels)
                    continue
                if key == ord(" "):
                    if cursor in selected:
                        selected.remove(cursor)
                    else:
                        selected.add(cursor)
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    return sorted(selected)
                if key == 27:
                    return None

    def _select_multiple_workspaces(self, workspaces: list[Path]) -> list[int] | None:
        if self._stdscr is None:
            return None

        with self._modal_focus():
            stdscr = self._stdscr
            selected: set[int] = set()
            cursor = 0
            start_index = 0

            while True:
                self._draw()
                height, width = stdscr.getmaxyx()

                max_name_len = max(len(path.name) for path in workspaces)
                modal_width = min(max(56, max_name_len + 10), max(20, width - 2))
                max_modal_height = max(8, height - 2)
                visible_rows = max(1, max_modal_height - 4)
                list_rows = min(len(workspaces), visible_rows)
                modal_height = list_rows + 4

                top = max(0, (height - modal_height) // 2)
                left = max(0, (width - modal_width) // 2)

                win = curses.newwin(modal_height, modal_width, top, left)
                win.keypad(True)
                win.nodelay(False)
                win.timeout(-1)

                if cursor < start_index:
                    start_index = cursor
                elif cursor >= start_index + list_rows:
                    start_index = cursor - list_rows + 1

                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title = self._trim_tail(" Clean Workspaces ", max(1, modal_width - 4))
                footer = "Space mark | A all | Enter delete | Esc cancel"
                try:
                    win.addstr(0, 2, title, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(footer, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                for row in range(list_rows):
                    index = start_index + row
                    if index >= len(workspaces):
                        break
                    path = workspaces[index]
                    mark = "[x]" if index in selected else "[ ]"
                    line = self._trim_tail(f"{mark} {path.name}", modal_width - 2)
                    attr = curses.A_REVERSE if index == cursor else 0
                    try:
                        win.addstr(1 + row, 1, line, attr)
                    except curses.error:
                        pass

                win.refresh()
                key = win.getch()

                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    cursor = (cursor - 1) % len(workspaces)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    cursor = (cursor + 1) % len(workspaces)
                    continue
                if key == ord(" "):
                    if cursor in selected:
                        selected.remove(cursor)
                    else:
                        selected.add(cursor)
                    continue
                if key in (ord("a"), ord("A")):
                    if len(selected) == len(workspaces):
                        selected.clear()
                    else:
                        selected = set(range(len(workspaces)))
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    return sorted(selected)
                if key == 27:
                    return None

    def _edit_text_inline(
        self,
        label: str,
        initial: str,
        *,
        return_escaped: bool = False,
    ) -> str | None | tuple[str | None, bool]:
        """Single-line text editor with full cursor movement for in-place editing."""
        if self._stdscr is None:
            return (None, True) if return_escaped else None

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()

            modal_width = min(max(72, len(label) + 18), max(24, width - 4))
            modal_height = 7
            if modal_width < 24 or height < modal_height + 1:
                return (None, True) if return_escaped else None

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)

            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            text = initial
            cursor = len(text)
            scroll = 0
            field_x = 2
            field_y = 3
            field_width = max(1, modal_width - field_x - 2)

            try:
                curses.curs_set(1)
            except curses.error:
                pass

            try:
                while True:
                    self._draw()
                    win.erase()
                    try:
                        win.box()
                    except curses.error:
                        pass

                    title_text = self._trim_tail(f" {label} ", max(1, modal_width - 4))
                    help_text = "Enter save  Esc cancel  ←→ move  Home/End  Del delete"

                    # Keep cursor in view
                    if cursor < scroll:
                        scroll = cursor
                    if cursor > scroll + field_width - 1:
                        scroll = cursor - field_width + 1
                    scroll = max(0, scroll)

                    visible = text[scroll : scroll + field_width]
                    cursor_in_view = cursor - scroll

                    try:
                        win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                        win.addstr(2, 2, self._trim_tail(label + ":", modal_width - 4))
                        # field background
                        win.addstr(field_y, field_x, " " * min(field_width, modal_width - field_x - 1))
                        win.addstr(field_y, field_x, visible)
                        win.addstr(
                            modal_height - 2,
                            2,
                            self._trim_tail(help_text, modal_width - 4),
                            self._attr("muted"),
                        )
                        win.move(field_y, field_x + min(cursor_in_view, field_width - 1))
                    except curses.error:
                        pass

                    win.refresh()
                    try:
                        key = win.get_wch()
                    except curses.error:
                        continue

                    if isinstance(key, str):
                        if key == "\x1b":
                            return (None, True) if return_escaped else None
                        if key in ("\n", "\r"):
                            return (text.strip(), False) if return_escaped else text.strip()
                        if key in ("\x08", "\x7f"):  # Backspace
                            if cursor > 0:
                                text = text[: cursor - 1] + text[cursor:]
                                cursor -= 1
                            continue
                        if key == "\x01":  # Ctrl+A → Home
                            cursor = 0
                            continue
                        if key == "\x05":  # Ctrl+E → End
                            cursor = len(text)
                            continue
                        if key == "\x04":  # Ctrl+D → delete forward
                            if cursor < len(text):
                                text = text[:cursor] + text[cursor + 1 :]
                            continue
                        if len(key) == 1 and ord(key) >= 32 and len(text) < 512:
                            text = text[:cursor] + key + text[cursor:]
                            cursor += 1
                        continue

                    # int branch: special/function keys
                    if key == 27:
                        return (None, True) if return_escaped else None
                    if key in (10, 13, curses.KEY_ENTER):
                        return (text.strip(), False) if return_escaped else text.strip()
                    if key in (curses.KEY_BACKSPACE, 127, 8):
                        if cursor > 0:
                            text = text[: cursor - 1] + text[cursor:]
                            cursor -= 1
                        continue
                    if key == curses.KEY_LEFT:
                        cursor = max(0, cursor - 1)
                        continue
                    if key == curses.KEY_RIGHT:
                        cursor = min(len(text), cursor + 1)
                        continue
                    if key == curses.KEY_HOME:
                        cursor = 0
                        continue
                    if key == curses.KEY_END:
                        cursor = len(text)
                        continue
                    if key == curses.KEY_DC:  # Delete forward
                        if cursor < len(text):
                            text = text[:cursor] + text[cursor + 1 :]
                        continue
            finally:
                try:
                    curses.curs_set(0)
                except curses.error:
                    pass

    @overload
    def _prompt_input(
        self,
        label: str,
        current_value: str,
        *,
        return_escaped: Literal[False] = False,
    ) -> str | None:
        ...

    @overload
    def _prompt_input(
        self,
        label: str,
        current_value: str,
        *,
        return_escaped: Literal[True],
    ) -> tuple[str | None, bool]:
        ...

    def _prompt_input(
        self,
        label: str,
        current_value: str,
        *,
        return_escaped: bool = False,
    ) -> str | None | tuple[str | None, bool]:
        if self._stdscr is None:
            return (None, True) if return_escaped else None

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()

            modal_width = min(max(72, len(label) + 18), max(24, width - 4))
            modal_height = min(11, max(8, height - 2))
            if modal_width < 24 or height < modal_height + 1:
                return None

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)

            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            user_input = ""
            max_input_len = 512

            try:
                curses.curs_set(1)
            except curses.error:
                pass

            try:
                while True:
                    self._draw()
                    win.erase()
                    try:
                        win.box()
                    except curses.error:
                        pass

                    title_text = self._trim_tail(f" {label} ", max(1, modal_width - 4))
                    input_prefix = "New: "
                    content_width = max(1, modal_width - 4)
                    current_lines = textwrap.wrap(
                        f"Current: {current_value}",
                        width=content_width,
                        replace_whitespace=False,
                        drop_whitespace=False,
                        break_long_words=True,
                        break_on_hyphens=False,
                    ) or ["Current: "]
                    max_current_rows = 2
                    shown_current_lines = current_lines[:max_current_rows]
                    input_row = 2 + len(shown_current_lines) + 1
                    input_indent = 2 + len(input_prefix)
                    input_width = max(1, modal_width - input_indent - 2)
                    available_input_rows = max(1, modal_height - input_row - 2)

                    if user_input:
                        wrapped_input = textwrap.wrap(
                            user_input,
                            width=input_width,
                            replace_whitespace=False,
                            drop_whitespace=False,
                            break_long_words=True,
                            break_on_hyphens=False,
                        ) or [""]
                        shown_input_lines = wrapped_input[-available_input_rows:]
                    else:
                        shown_input_lines = ["(keep current)"]

                    help_text = "Enter apply | Esc back | Backspace delete"

                    try:
                        win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                        for index, line in enumerate(shown_current_lines):
                            win.addstr(2 + index, 2, line)
                        win.addstr(input_row, 2, input_prefix, self._attr("accent", bold=True))
                        for index, line in enumerate(shown_input_lines):
                            attr = 0 if user_input else self._attr("muted")
                            win.addstr(input_row + index, input_indent, line, attr)
                        win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                        cursor_line = input_row + max(0, len(shown_input_lines) - 1)
                        cursor_col = input_indent + (len(shown_input_lines[-1]) if user_input else 0)
                        win.move(cursor_line, min(modal_width - 2, max(input_indent, cursor_col)))
                    except curses.error:
                        pass

                    win.refresh()
                    try:
                        key = win.get_wch()
                    except curses.error:
                        continue

                    # get_wch() returns str for printable chars, int for special keys
                    if isinstance(key, str):
                        if key in ("\x1b",):  # Esc
                            return (None, True) if return_escaped else None
                        if key in ("\n", "\r"):
                            value = user_input.strip()
                            if not value:
                                return (None, False) if return_escaped else None
                            return (value, False) if return_escaped else value
                        if key in ("\x08", "\x7f"):  # Backspace
                            if user_input:
                                user_input = user_input[:-1]
                            continue
                        if len(key) == 1 and ord(key) >= 32 and len(user_input) < max_input_len:
                            user_input += key
                        continue

                    # int branch: special/function keys
                    if key == 27:
                        return (None, True) if return_escaped else None
                    if key in (10, 13, curses.KEY_ENTER):
                        value = user_input.strip()
                        if not value:
                            return (None, False) if return_escaped else None
                        return (value, False) if return_escaped else value
                    if key in (curses.KEY_BACKSPACE, 127, 8):
                        if user_input:
                            user_input = user_input[:-1]
                        continue
            finally:
                try:
                    curses.curs_set(0)
                except curses.error:
                    pass

    @overload
    def _select_from_list(
        self,
        label: str,
        options: list[str],
        current_value: str,
        *,
        option_details: dict[str, str] | None = None,
        marked_value: str | None = None,
        return_escaped: Literal[False] = False,
    ) -> str | None:
        ...

    @overload
    def _select_from_list(
        self,
        label: str,
        options: list[str],
        current_value: str,
        *,
        option_details: dict[str, str] | None = None,
        marked_value: str | None = None,
        return_escaped: Literal[True],
    ) -> tuple[str | None, bool]:
        ...

    def _select_from_list(
        self,
        label: str,
        options: list[str],
        current_value: str,
        *,
        option_details: dict[str, str] | None = None,
        marked_value: str | None = None,
        return_escaped: bool = False,
    ) -> str | None | tuple[str | None, bool]:
        if self._stdscr is None:
            return (None, True) if return_escaped else None

        normalized = [str(option).strip() for option in options if str(option).strip()]
        if not normalized:
            return (None, True) if return_escaped else None

        normalized_details = {
            str(key).strip(): str(value).strip()
            for key, value in (option_details or {}).items()
            if str(key).strip() and str(value).strip()
        }

        with self._modal_focus():
            stdscr = self._stdscr
            height, width = stdscr.getmaxyx()

            max_option_len = max(len(item) for item in normalized)
            modal_width = min(max(42, max_option_len + 8), max(12, width - 2))
            detail_rows = 3 if normalized_details else 0
            max_modal_height = max(6 + detail_rows, height - 2)
            max_list_rows = max(1, max_modal_height - 4 - detail_rows)
            list_rows = min(len(normalized), max_list_rows)
            modal_height = max(6 + detail_rows, list_rows + 4 + detail_rows)

            if modal_width < 12 or modal_height < (6 + detail_rows):
                return None

            top = max(0, (height - modal_height) // 2)
            left = max(0, (width - modal_width) // 2)

            selected = 0
            if current_value in normalized:
                selected = normalized.index(current_value)
            start_index = max(0, selected - list_rows + 1)

            win = curses.newwin(modal_height, modal_width, top, left)
            win.keypad(True)
            win.nodelay(False)
            win.timeout(-1)

            while True:
                self._draw()
                win.erase()
                try:
                    win.box()
                except curses.error:
                    pass

                title_text = self._trim_tail(f" {label} ", max(1, modal_width - 4))
                help_text = "Enter select | Esc back" if not marked_value else "Enter select | * current | Esc back"
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                except curses.error:
                    pass

                if selected < start_index:
                    start_index = selected
                elif selected >= start_index + list_rows:
                    start_index = selected - list_rows + 1

                for row in range(list_rows):
                    option_index = start_index + row
                    if option_index >= len(normalized):
                        break
                    item = normalized[option_index]
                    prefix = ">" if option_index == selected else " "
                    marker = "*" if marked_value and item == marked_value else " "
                    line = self._trim_tail(f"{prefix} {marker} {item}", modal_width - 2)
                    attr = curses.A_REVERSE if option_index == selected else 0
                    try:
                        win.addstr(1 + row, 1, line, attr)
                    except curses.error:
                        pass

                if normalized_details:
                    detail_text = normalized_details.get(normalized[selected], "")
                    detail_width = max(20, modal_width - 4)
                    detail_lines = textwrap.wrap(
                        detail_text,
                        width=detail_width,
                        replace_whitespace=False,
                        drop_whitespace=False,
                    )[:detail_rows]
                    detail_top = 1 + list_rows
                    for row in range(detail_rows):
                        line = detail_lines[row] if row < len(detail_lines) else ""
                        try:
                            win.addstr(detail_top + row, 2, self._trim_tail(line, modal_width - 4), self._attr("muted"))
                        except curses.error:
                            pass

                win.refresh()
                key = win.getch()

                if key in (curses.KEY_UP, ord("k"), ord("K")):
                    selected = (selected - 1) % len(normalized)
                    continue
                if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                    selected = (selected + 1) % len(normalized)
                    continue
                if key in (10, 13, curses.KEY_ENTER):
                    value = normalized[selected]
                    return (value, False) if return_escaped else value
                if key in (27,):
                    return (None, True) if return_escaped else None

    def _load_melo_speakers(self, language: str) -> list[str]:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=FutureWarning)
                with redirect_stderr(io.StringIO()):
                    from melo.api import TTS  # type: ignore

                    tts = TTS(language=language, device="auto")
                    hps = getattr(tts, "hps", None)
                    hps_data = getattr(hps, "data", None)
                    spk2id = dict(getattr(hps_data, "spk2id", {}) or {})
            return sorted(str(name) for name in spk2id.keys())
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Melo speaker inventory unavailable for {language}: {exc}")
            return []

    def _piper_voice_option_entries(self) -> list[tuple[str, dict[str, Any]]]:
        options: list[tuple[str, dict[str, Any]]] = []
        for voice_meta in self.DEBUG_PIPER_VOICES:
            label = str(voice_meta.get("label") or voice_meta.get("id") or "Piper voice").strip()
            if not label:
                continue
            options.append((label, dict(voice_meta)))
        return options

    def _selected_piper_voice_meta(self) -> dict[str, Any]:
        options = self._piper_voice_option_entries()
        if not options:
            raise RuntimeError("No Piper voices are configured.")

        configured_id = str(self.config.piper_voice_id or "").strip()
        configured_speaker_id = self.config.piper_speaker_id
        for _label, meta in options:
            meta_id = str(meta.get("id") or "").strip()
            if meta_id != configured_id:
                continue
            meta_speaker = meta.get("speaker_id")
            if meta_speaker is None and configured_speaker_id is None:
                return dict(meta)
            if meta_speaker is not None and configured_speaker_id is not None and int(meta_speaker) == int(configured_speaker_id):
                return dict(meta)

        fallback = dict(options[0][1])
        self.config.piper_voice_id = str(fallback.get("id") or "").strip()
        speaker_id = fallback.get("speaker_id")
        self.config.piper_speaker_id = int(speaker_id) if speaker_id is not None else None
        return fallback

    def _trim_tail(self, text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3] + "..."

    def _trim_middle(self, text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= 5:
            return text[:limit]
        half = (limit - 3) // 2
        tail = limit - 3 - half
        return text[:half] + "..." + text[-tail:]

    def _safe_addstr(self, row: int, col: int, text: str, width: int, attr: int = 0) -> None:
        if self._stdscr is None:
            return
        if row < 0 or col < 0:
            return

        clipped = text.replace("\n", " ")
        if width > 0:
            clipped = clipped[: max(0, width - col - 1)]

        try:
            self._stdscr.addstr(row, col, clipped, attr)
        except curses.error:
            pass

    def _draw_hotkey_hint(self, *, row: int, width: int) -> None:
        actions = ("Run", "YouTube", "Prompt", "Thumbnail", "Settings", "Clean", "Quit")
        normal_attr = self._attr("muted")
        hotkey_attr = self._attr("accent", bold=True) | curses.A_UNDERLINE
        col = 0

        for index, action in enumerate(actions):
            if not action:
                continue

            first = action[0]
            rest = action[1:]
            self._safe_addstr(row, col, first, width, attr=hotkey_attr)
            col += len(first)

            if rest:
                self._safe_addstr(row, col, rest, width, attr=normal_attr)
                col += len(rest)

            if index < len(actions) - 1:
                separator = "  "
                self._safe_addstr(row, col, separator, width, attr=normal_attr)
                col += len(separator)

    def _cancel_active_process(self) -> None:
        process = self._get_active_process()
        if process is None or process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _shutdown(self) -> None:
        self._cancel_active_process()

        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=2)

        if self._started_ollama and self._ollama_process and self._ollama_process.poll() is None:
            self._ollama_process.terminate()
            try:
                self._ollama_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._ollama_process.kill()
                self._ollama_process.wait(timeout=5)

        if self._ollama_log_handle is not None:
            self._ollama_log_handle.close()
            self._ollama_log_handle = None

        if self._session_log_handle is not None:
            self._session_log_handle.close()
            self._session_log_handle = None

    def _write_session_log_line(self, line: str) -> None:
        if self._session_log_handle is None:
            return
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            self._session_log_handle.write(f"{timestamp} {line}\n")
            self._session_log_handle.flush()
        except Exception:
            pass

    def _append_stream_log(self, raw_line: str) -> None:
        self._write_session_log_line(raw_line)

        compact_line = self._compact_stream_log_line(raw_line)
        if compact_line is None:
            return

        with self._lock:
            if compact_line == self._last_ui_stream_line:
                return
            self._last_ui_stream_line = compact_line
            self._logs.append(compact_line)

    def _compact_stream_log_line(self, line: str) -> str | None:
        clean = self.ANSI_ESCAPE_RE.sub("", str(line or ""))
        clean = clean.replace("\r", " ").replace("\t", " ").replace("\ufffd", "")
        clean = "".join(ch if ch.isprintable() else " " for ch in clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return None

        if self._is_noisy_stream_line(clean):
            return None

        return clean

    def _is_noisy_stream_line(self, line: str) -> bool:
        stripped = line.strip()
        lower = stripped.lower()

        if not stripped:
            return True

        if "it/s" in lower and "%|" in lower:
            return True

        if stripped.startswith(("0%|", "100%|", " 0%|", " 100%|")):
            return True

        if stripped.startswith(">") and "====" in stripped:
            return True

        if stripped.replace("=", "").strip() == "":
            return True

        return False

    def _append_log(self, line: str) -> None:
        raw_line = str(line or "")
        cleaned = self.ANSI_ESCAPE_RE.sub("", raw_line)
        cleaned = cleaned.replace("\r", " ").replace("\t", " ").replace("\ufffd", "")
        cleaned = "".join(ch if ch.isprintable() else " " for ch in cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return

        with self._lock:
            self._logs.append(cleaned)
            self._last_ui_stream_line = None

        self._write_session_log_line(raw_line)

    def _get_logs(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def _get_status(self) -> str:
        with self._lock:
            return self._status

    def _set_running(self, running: bool) -> None:
        with self._lock:
            self._running = running

    def _is_running(self) -> bool:
        with self._lock:
            return self._running

    def _set_active_process(self, process: subprocess.Popen[str] | None) -> None:
        with self._lock:
            self._active_process = process

    def _get_active_process(self) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._active_process

    def _init_session_log_paths(self) -> tuple[Path, Path]:
        root_raw = os.environ.get("IMAGINE_TUI_LOG_DIR")
        if root_raw:
            root = Path(root_raw).expanduser().resolve()
        else:
            root = (Path.home() / ".imagine" / "logs").resolve()

        root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        session_path = root / f"tui-{stamp}.log"
        latest_path = root / "latest.log"
        return session_path, latest_path

    def _settings_path(self) -> Path:
        configured = os.environ.get("IMAGINE_TUI_SETTINGS_PATH")
        if configured:
            return Path(configured).expanduser().resolve()
        return (Path.home() / ".imagine" / "tui_settings.json").resolve()

    def _load_persisted_settings(self) -> None:
        settings_path = self._settings_path()
        if not settings_path.exists():
            return

        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"WARN: Could not parse TUI settings file {settings_path}: {exc}")
            return

        if not isinstance(payload, dict):
            return

        hitl_value = payload.get("hitl_enabled")
        if isinstance(hitl_value, bool):
            self._hitl_enabled = hitl_value

        fast_mode_value = payload.get("fast_mode")
        if isinstance(fast_mode_value, bool):
            self.config.fast_mode = fast_mode_value

        burn_subtitles_value = payload.get("burn_subtitles")
        if isinstance(burn_subtitles_value, bool):
            self.config.burn_subtitles = burn_subtitles_value

        subtitle_preset_value = payload.get("subtitle_preset")
        if subtitle_preset_value is not None:
            self.config.subtitle_preset = normalize_subtitle_preset(subtitle_preset_value, "regular")

        subtitle_position_value = payload.get("subtitle_position")
        if subtitle_position_value is not None:
            self.config.subtitle_position = normalize_subtitle_position(subtitle_position_value, "bottom")

        subtitle_accent_color_value = payload.get("subtitle_accent_color")
        if subtitle_accent_color_value is not None:
            self.config.subtitle_accent_color = normalize_subtitle_accent_color(
                subtitle_accent_color_value,
                "sunflower",
            )

        subtitle_box_color_value = payload.get("subtitle_box_color")
        if subtitle_box_color_value is not None:
            self.config.subtitle_box_color = normalize_subtitle_box_color(
                subtitle_box_color_value,
                self._normalized_subtitle_accent_color(),
            )
        elif subtitle_accent_color_value is not None:
            self.config.subtitle_box_color = self._normalized_subtitle_accent_color()

        caption_font_scale_value = payload.get("caption_font_scale")
        if caption_font_scale_value is not None:
            try:
                self.config.caption_font_scale = normalize_caption_font_scale(caption_font_scale_value, 0.9)
            except Exception:
                pass

        subtitle_bold_value = payload.get("subtitle_bold")
        if isinstance(subtitle_bold_value, bool):
            self.config.subtitle_bold = subtitle_bold_value

        subtitle_outline_value = payload.get("subtitle_outline")
        if isinstance(subtitle_outline_value, bool):
            self.config.subtitle_outline = subtitle_outline_value

        enable_pexels_value = payload.get("enable_pexels_provider")
        if isinstance(enable_pexels_value, bool):
            self.config.enable_pexels_provider = enable_pexels_value

        enable_pixabay_value = payload.get("enable_pixabay_provider")
        if isinstance(enable_pixabay_value, bool):
            self.config.enable_pixabay_provider = enable_pixabay_value

        enable_coverr_value = payload.get("enable_coverr_provider")
        if isinstance(enable_coverr_value, bool):
            self.config.enable_coverr_provider = enable_coverr_value

        enable_vecteezy_value = payload.get("enable_vecteezy_provider")
        if isinstance(enable_vecteezy_value, bool):
            self.config.enable_vecteezy_provider = enable_vecteezy_value

        allow_image_value = payload.get("allow_image_assets")
        if isinstance(allow_image_value, bool):
            self.config.allow_image_assets = allow_image_value

        asset_mode_value = payload.get("asset_mode")
        if isinstance(asset_mode_value, str):
            candidate_mode = asset_mode_value.strip().lower()
            if candidate_mode in self.ASSET_MODE_CHOICES:
                self.config.asset_mode = candidate_mode

        image_motion_style_value = payload.get("image_motion_style")
        if isinstance(image_motion_style_value, str):
            candidate_style = self.IMAGE_MOTION_STYLE_ALIASES.get(
                image_motion_style_value.strip().lower(),
                image_motion_style_value.strip().lower(),
            )
            if candidate_style in self.IMAGE_MOTION_STYLE_CHOICES:
                self.config.image_motion_style = candidate_style

        allow_attribution_value = payload.get("allow_attribution_required_assets")
        if isinstance(allow_attribution_value, bool):
            self.config.allow_attribution_required_assets = allow_attribution_value

        include_intro_value = payload.get("include_intro")
        if isinstance(include_intro_value, bool):
            self.config.include_intro = include_intro_value

        external_editor_value = payload.get("external_editor_command")
        if isinstance(external_editor_value, str):
            self.config.external_editor_command = external_editor_value.strip()

        thumbnail_comfyui_url_value = payload.get("thumbnail_comfyui_url")
        if isinstance(thumbnail_comfyui_url_value, str):
            self.config.thumbnail_comfyui_url = thumbnail_comfyui_url_value.strip().rstrip("/") or THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL

        thumbnail_comfyui_install_dir_value = payload.get("thumbnail_comfyui_install_dir")
        if isinstance(thumbnail_comfyui_install_dir_value, str):
            self.config.thumbnail_comfyui_install_dir = str(Path(thumbnail_comfyui_install_dir_value.strip()).expanduser().resolve()) if thumbnail_comfyui_install_dir_value.strip() else ""

        thumbnail_export_dir_value = payload.get("thumbnail_default_export_dir")
        if isinstance(thumbnail_export_dir_value, str):
            self.config.thumbnail_default_export_dir = str(
                Path(thumbnail_export_dir_value.strip() or str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR)).expanduser().resolve()
            )

        thumbnail_ollama_hooks_value = payload.get("thumbnail_use_ollama_hooks")
        if isinstance(thumbnail_ollama_hooks_value, bool):
            self.config.thumbnail_use_ollama_hooks = thumbnail_ollama_hooks_value

        prompt_value = payload.get("prompt")
        if isinstance(prompt_value, str):
            candidate = prompt_value.strip()
            if candidate:
                self.config.prompt = candidate

        content_mode_value = payload.get("content_mode")
        if isinstance(content_mode_value, str):
            self.config.content_mode = normalize_content_mode(content_mode_value, "explainer")

        news_feed_urls_value = payload.get("news_feed_urls")
        if isinstance(news_feed_urls_value, list):
            deduped: list[str] = []
            seen: set[str] = set()
            for item in news_feed_urls_value:
                value = str(item).strip()
                if not value:
                    continue
                lowered = value.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                deduped.append(value)
            self.config.news_feed_urls = deduped
        self._apply_default_news_feeds_if_needed()

        resolution_value = payload.get("resolution")
        if isinstance(resolution_value, str):
            self.config.resolution = self._normalize_resolution_value(resolution_value)

        asset_keywords_value = payload.get("asset_keywords")
        if isinstance(asset_keywords_value, list):
            parsed_keywords = self._normalize_asset_keywords(
                [str(item).strip() for item in asset_keywords_value if str(item).strip()]
            )
            self.config.asset_keywords = parsed_keywords

        minutes_value = payload.get("minutes")
        if minutes_value is not None:
            try:
                candidate_minutes = int(minutes_value)
            except Exception:
                candidate_minutes = 0
            if candidate_minutes > 0:
                self.config.minutes = candidate_minutes

        script_tone_value = payload.get("script_tone")
        if isinstance(script_tone_value, str):
            candidate_tone = script_tone_value.strip()
            if candidate_tone in self.SCRIPT_TONE_CHOICES:
                self.config.script_tone = candidate_tone

        target_audience_value = payload.get("target_audience")
        if isinstance(target_audience_value, str):
            candidate_audience = target_audience_value.strip()
            if candidate_audience:
                self.config.target_audience = candidate_audience

        hook_style_value = payload.get("hook_style")
        if isinstance(hook_style_value, str):
            candidate_hook = hook_style_value.strip()
            if candidate_hook in self.HOOK_STYLE_CHOICES:
                self.config.hook_style = candidate_hook

        narrative_mode_value = payload.get("narrative_mode")
        if isinstance(narrative_mode_value, str):
            candidate_mode = narrative_mode_value.strip()
            if candidate_mode in self.NARRATIVE_MODE_CHOICES:
                self.config.narrative_mode = candidate_mode

        example_density_value = payload.get("example_density")
        if isinstance(example_density_value, str):
            candidate_density = example_density_value.strip()
            if candidate_density in self.EXAMPLE_DENSITY_CHOICES:
                self.config.example_density = candidate_density

        tts_engine_value = payload.get("tts_engine")
        if isinstance(tts_engine_value, str):
            candidate_engine = tts_engine_value.strip().lower()
            if candidate_engine in {"melo", "piper", "kokoro"}:
                self.config.tts_engine = candidate_engine

        piper_voice_value = payload.get("piper_voice_id")
        if isinstance(piper_voice_value, str):
            self.config.piper_voice_id = piper_voice_value.strip()

        piper_speaker_value = payload.get("piper_speaker_id")
        if piper_speaker_value is None:
            self.config.piper_speaker_id = None
        else:
            try:
                self.config.piper_speaker_id = int(piper_speaker_value)
            except Exception:
                pass

        voice_profile_value = payload.get("voice_profile")
        if isinstance(voice_profile_value, str):
            candidate_profile = voice_profile_value.strip()
            if candidate_profile in self.VOICE_PROFILE_CHOICES:
                self.config.voice_profile = candidate_profile

        voice_speed_value = payload.get("voice_speed")
        if voice_speed_value is not None:
            try:
                candidate_speed = float(voice_speed_value)
            except Exception:
                candidate_speed = 0.0
            if 0.5 <= candidate_speed <= 2.0:
                self.config.voice_speed = candidate_speed

        melo_language_value = payload.get("melo_language")
        if isinstance(melo_language_value, str):
            candidate_language = melo_language_value.strip().upper()
            if candidate_language in self.MELO_LANGUAGE_CHOICES:
                self.config.melo_language = candidate_language

        melo_speaker_value = payload.get("melo_speaker")
        if isinstance(melo_speaker_value, str):
            candidate_speaker = melo_speaker_value.strip()
            if candidate_speaker:
                self.config.melo_speaker = candidate_speaker

        kokoro_lang_code_value = payload.get("kokoro_lang_code")
        if isinstance(kokoro_lang_code_value, str):
            self.config.kokoro_lang_code = normalize_kokoro_lang_code(kokoro_lang_code_value)

        kokoro_voice_value = payload.get("kokoro_voice")
        if isinstance(kokoro_voice_value, str):
            candidate_voice = kokoro_voice_value.strip()
            if candidate_voice:
                self.config.kokoro_voice = candidate_voice

        active_channel_value = payload.get("active_channel")
        if isinstance(active_channel_value, str):
            candidate_channel = active_channel_value.strip()
            if candidate_channel in self.CHANNEL_PROFILES:
                self.config.active_channel = candidate_channel

        script_language_value = payload.get("script_language")
        if isinstance(script_language_value, str):
            candidate_lang = script_language_value.strip().lower()
            if candidate_lang:
                self.config.script_language = candidate_lang

        if not self.config.allow_image_assets and self._normalized_asset_mode() in {"prefer-images", "images-only"}:
            self.config.asset_mode = "prefer-video"

    def _save_persisted_settings(self) -> None:
        settings_path = self._settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 17,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "hitl_enabled": bool(self._hitl_enabled),
            "fast_mode": bool(self.config.fast_mode),
            "burn_subtitles": bool(self.config.burn_subtitles),
            "subtitle_preset": self._normalized_subtitle_preset(),
            "subtitle_position": self._normalized_subtitle_position(),
            "subtitle_accent_color": self._normalized_subtitle_accent_color(),
            "subtitle_box_color": self._normalized_subtitle_box_color(),
            "caption_font_scale": round(self._normalized_caption_font_scale(), 3),
            "subtitle_bold": bool(self._normalized_subtitle_bold()),
            "subtitle_outline": bool(self._normalized_subtitle_outline()),
            "enable_pexels_provider": bool(self.config.enable_pexels_provider),
            "enable_pixabay_provider": bool(self.config.enable_pixabay_provider),
            "enable_coverr_provider": bool(self.config.enable_coverr_provider),
            "enable_vecteezy_provider": bool(self.config.enable_vecteezy_provider),
            "allow_image_assets": bool(self.config.allow_image_assets),
            "asset_mode": self._normalized_asset_mode(),
            "image_motion_style": self._normalized_image_motion_style(),
            "allow_attribution_required_assets": bool(self.config.allow_attribution_required_assets),
            "include_intro": bool(self.config.include_intro),
            "external_editor_command": str(self.config.external_editor_command).strip(),
            "thumbnail_comfyui_url": str(getattr(self.config, "thumbnail_comfyui_url", THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL) or "").strip()
            or THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL,
            "thumbnail_comfyui_install_dir": str(getattr(self.config, "thumbnail_comfyui_install_dir", "") or "").strip(),
            "thumbnail_default_export_dir": str(
                Path(
                    str(getattr(self.config, "thumbnail_default_export_dir", THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR) or "").strip()
                    or str(THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR)
                ).expanduser().resolve()
            ),
            "thumbnail_use_ollama_hooks": bool(getattr(self.config, "thumbnail_use_ollama_hooks", True)),
            "prompt": self.config.prompt,
            "content_mode": self._content_mode(),
            "news_feed_urls": list(self.config.news_feed_urls),
            "resolution": self.config.resolution,
            "asset_keywords": list(self.config.asset_keywords),
            "minutes": int(self.config.minutes),
            "script_tone": self.config.script_tone,
            "target_audience": self.config.target_audience,
            "hook_style": self.config.hook_style,
            "narrative_mode": self.config.narrative_mode,
            "example_density": self.config.example_density,
            "tts_engine": self.config.tts_engine,
            "piper_voice_id": self.config.piper_voice_id,
            "piper_speaker_id": self.config.piper_speaker_id,
            "voice_profile": self.config.voice_profile,
            "voice_speed": round(float(self.config.voice_speed), 3),
            "melo_language": self.config.melo_language,
            "melo_speaker": self.config.melo_speaker,
            "kokoro_lang_code": normalize_kokoro_lang_code(self.config.kokoro_lang_code),
            "kokoro_voice": self.config.kokoro_voice,
            "active_channel": self.config.active_channel,
            "script_language": self.config.script_language,
        }
        settings_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _open_session_log(self) -> None:
        self._session_log_handle = self._session_log_path.open("a", encoding="utf-8")
        try:
            if self._latest_log_path.exists() or self._latest_log_path.is_symlink():
                self._latest_log_path.unlink()
            self._latest_log_path.symlink_to(self._session_log_path.name)
        except Exception:
            try:
                self._latest_log_path.write_text(str(self._session_log_path) + "\n", encoding="utf-8")
            except Exception:
                pass


def run_tui(
    prompt: str,
    content_mode: str,
    asset_keywords: list[str],
    news_feed_urls: list[str],
    project_dir: Path,
    minutes: int,
    resolution: str,
    burn_subtitles: bool,
    subtitle_preset: str,
    subtitle_position: str,
    subtitle_accent_color: str,
    subtitle_box_color: str,
    caption_font_scale: float,
    subtitle_bold: bool,
    subtitle_outline: bool,
    script_tone: str,
    target_audience: str,
    hook_style: str,
    narrative_mode: str,
    example_density: str,
    asset_mode: str,
    image_motion_style: str,
    tts_engine: str,
    piper_voice_id: str,
    piper_speaker_id: int | None,
    voice_profile: str,
    voice_speed: float,
    melo_language: str,
    melo_speaker: str,
    kokoro_lang_code: str,
    kokoro_voice: str,
    include_intro: bool = False,
    external_editor_command: str = "",
    fast_mode: bool = False,
) -> int:
    language_value = str(melo_language).strip().upper() or "EN"
    if language_value not in LocalVideoMvpTui.MELO_LANGUAGE_CHOICES:
        language_value = "EN"
    kokoro_language_value = normalize_kokoro_lang_code(kokoro_lang_code)

    config = TuiConfig(
        prompt=prompt,
        content_mode=normalize_content_mode(content_mode, "explainer"),
        asset_keywords=[str(item).strip() for item in (asset_keywords or []) if str(item).strip()],
        news_feed_urls=[str(item).strip() for item in (news_feed_urls or []) if str(item).strip()],
        project_dir=project_dir,
        minutes=max(1, minutes),
        resolution=LocalVideoMvpTui.RESOLUTION_PRESETS.get(
            str(resolution).strip().lower(),
            str(resolution).strip() or LocalVideoMvpTui.RESOLUTION_PRESETS["720p"],
        ),
        burn_subtitles=bool(burn_subtitles),
        subtitle_preset=normalize_subtitle_preset(subtitle_preset, "regular"),
        subtitle_position=normalize_subtitle_position(subtitle_position, "bottom"),
        subtitle_accent_color=normalize_subtitle_accent_color(subtitle_accent_color, "sunflower"),
        subtitle_box_color=normalize_subtitle_box_color(subtitle_box_color, subtitle_accent_color),
        caption_font_scale=normalize_caption_font_scale(caption_font_scale, 0.9),
        subtitle_bold=bool(subtitle_bold),
        subtitle_outline=bool(subtitle_outline),
        fast_mode=bool(fast_mode),
        script_tone=str(script_tone).strip() or "conversational",
        target_audience=str(target_audience).strip() or "curious general audience",
        hook_style=str(hook_style).strip() or "surprising-fact",
        narrative_mode=str(narrative_mode).strip() or "story-led",
        example_density=str(example_density).strip() or "balanced",
        asset_mode=(
            str(asset_mode).strip().lower()
            if str(asset_mode).strip().lower() in LocalVideoMvpTui.ASSET_MODE_CHOICES
            else "prefer-video"
        ),
        image_motion_style=(
            LocalVideoMvpTui.IMAGE_MOTION_STYLE_ALIASES.get(
                str(image_motion_style).strip().lower(),
                str(image_motion_style).strip().lower(),
            )
            if LocalVideoMvpTui.IMAGE_MOTION_STYLE_ALIASES.get(
                str(image_motion_style).strip().lower(),
                str(image_motion_style).strip().lower(),
            )
            in LocalVideoMvpTui.IMAGE_MOTION_STYLE_CHOICES
            else "slow"
        ),
        tts_engine=(str(tts_engine).strip().lower() or "melo"),
        piper_voice_id=str(piper_voice_id).strip(),
        piper_speaker_id=piper_speaker_id,
        voice_profile=voice_profile,
        voice_speed=max(0.5, min(2.0, float(voice_speed))),
        melo_language=language_value,
        melo_speaker=str(melo_speaker).strip() or "EN-US",
        kokoro_lang_code=kokoro_language_value,
        kokoro_voice=str(kokoro_voice).strip() or default_kokoro_voice(kokoro_language_value),
        include_intro=bool(include_intro),
        external_editor_command=str(external_editor_command).strip(),
    )
    if config.tts_engine not in {"melo", "piper", "kokoro"}:
        config.tts_engine = "melo"
    if config.asset_mode in {"prefer-images", "images-only"}:
        config.allow_image_assets = True
    app = LocalVideoMvpTui(config)
    return app.run()
