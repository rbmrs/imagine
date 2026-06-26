from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
import wave
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import requests

from .models import (
    AssetCandidate,
    AssetRight,
    ApprovedEditorialSource,
    NewsBrief,
    NewsSourceCandidate,
    PipelineConfig,
    PlannedShot,
    Scene,
    ShotPlan,
    ShotReviewState,
    ScriptPlan,
    TimelineClip,
    normalize_caption_font_scale,
    normalize_content_mode,
    normalize_news_visual_strategy,
    normalize_shot_confidence,
    normalize_subtitle_accent_color,
    normalize_subtitle_box_color,
    normalize_subtitle_position,
    normalize_subtitle_preset,
)
from .visual_vocab import normalize_match_text, resolve_channel_visual_vocabulary


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

BIBLE_BOOK_SPOKEN_ALIASES = {
    "gn": "Genesis",
    "genesis": "Genesis",
    "ex": "Exodo",
    "exodo": "Exodo",
    "lv": "Levitico",
    "levitico": "Levitico",
    "nm": "Numeros",
    "numeros": "Numeros",
    "dt": "Deuteronomio",
    "deuteronomio": "Deuteronomio",
    "js": "Josue",
    "josue": "Josue",
    "jz": "Juizes",
    "juizes": "Juizes",
    "rt": "Rute",
    "rute": "Rute",
    "1sm": "Primeiro Samuel",
    "1 samuel": "Primeiro Samuel",
    "2sm": "Segundo Samuel",
    "2 samuel": "Segundo Samuel",
    "1rs": "Primeiro Reis",
    "1 reis": "Primeiro Reis",
    "2rs": "Segundo Reis",
    "2 reis": "Segundo Reis",
    "1cr": "Primeiro Cronicas",
    "1 cronicas": "Primeiro Cronicas",
    "2cr": "Segundo Cronicas",
    "2 cronicas": "Segundo Cronicas",
    "ne": "Neemias",
    "neemias": "Neemias",
    "est": "Ester",
    "et": "Ester",
    "ester": "Ester",
    "sl": "Salmo",
    "salmo": "Salmo",
    "salmos": "Salmo",
    "pv": "Proverbios",
    "proverbios": "Proverbios",
    "ec": "Eclesiastes",
    "eclesiastes": "Eclesiastes",
    "is": "Isaias",
    "isaias": "Isaias",
    "jr": "Jeremias",
    "jeremias": "Jeremias",
    "ez": "Ezequiel",
    "ezequiel": "Ezequiel",
    "dn": "Daniel",
    "daniel": "Daniel",
    "os": "Oseias",
    "oseias": "Oseias",
    "jl": "Joel",
    "joel": "Joel",
    "am": "Amos",
    "amos": "Amos",
    "jn": "Jonas",
    "jonas": "Jonas",
    "mq": "Miqueias",
    "miqueias": "Miqueias",
    "na": "Naum",
    "naum": "Naum",
    "hc": "Habacuque",
    "habacuque": "Habacuque",
    "sf": "Sofonias",
    "sofonias": "Sofonias",
    "ag": "Ageu",
    "ageu": "Ageu",
    "zc": "Zacarias",
    "zacarias": "Zacarias",
    "ml": "Malaquias",
    "malaquias": "Malaquias",
    "mt": "Mateus",
    "mateus": "Mateus",
    "mc": "Marcos",
    "mar": "Marcos",
    "marcos": "Marcos",
    "lc": "Lucas",
    "lucas": "Lucas",
    "jo": "Joao",
    "joao": "Joao",
    "at": "Atos",
    "atos": "Atos",
    "rm": "Romanos",
    "rom": "Romanos",
    "romanos": "Romanos",
    "1cor": "Primeira Carta aos Corintios",
    "1 cor": "Primeira Carta aos Corintios",
    "2cor": "Segunda Carta aos Corintios",
    "2 cor": "Segunda Carta aos Corintios",
    "gl": "Galatas",
    "galatas": "Galatas",
    "ef": "Efesios",
    "efesios": "Efesios",
    "fp": "Filipenses",
    "filipenses": "Filipenses",
    "cl": "Colossenses",
    "colossenses": "Colossenses",
    "1ts": "Primeira Carta aos Tessalonicenses",
    "1 ts": "Primeira Carta aos Tessalonicenses",
    "2ts": "Segunda Carta aos Tessalonicenses",
    "2 ts": "Segunda Carta aos Tessalonicenses",
    "1tm": "Primeira Carta a Timoteo",
    "1 tm": "Primeira Carta a Timoteo",
    "2tm": "Segunda Carta a Timoteo",
    "2 tm": "Segunda Carta a Timoteo",
    "tt": "Tito",
    "tito": "Tito",
    "fm": "Filemom",
    "filemom": "Filemom",
    "hb": "Hebreus",
    "hebreus": "Hebreus",
    "tg": "Tiago",
    "tiago": "Tiago",
    "1pe": "Primeira Carta de Pedro",
    "1 pe": "Primeira Carta de Pedro",
    "2pe": "Segunda Carta de Pedro",
    "2 pe": "Segunda Carta de Pedro",
    "1jo": "Primeira Carta de Joao",
    "1 jo": "Primeira Carta de Joao",
    "2jo": "Segunda Carta de Joao",
    "2 jo": "Segunda Carta de Joao",
    "3jo": "Terceira Carta de Joao",
    "3 jo": "Terceira Carta de Joao",
    "jd": "Judas",
    "judas": "Judas",
    "ap": "Apocalipse",
    "apocalipse": "Apocalipse",
}

BIBLE_REFERENCE_RE = re.compile(
    r"(?<![\w/])"
    r"(?P<book>(?:[1-3]\s*)?[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.]{0,15})"
    r"\s+"
    r"(?P<chapter>\d{1,3})"
    r"\s*[:,]\s*"
    r"(?P<verses>\d{1,3}(?:\s*[-.]\s*\d{1,3})*)"
)

SUBTITLE_ACCENT_ASS_COLORS = {
    "sunflower": "&H4AD8FF&",
    "mint": "&HB0E379&",
    "coral": "&H6A7FFF&",
    "sky": "&HFFC86E&",
    "lime": "&H64F4C7&",
    "rose": "&HAE6FFF&",
    "lavender": "&HFF9CB7&",
    "tangerine": "&H4AB0FF&",
    "white": "&H00FFFFFF&",
    "black": "&H00000000&",
}
SUBTITLE_ACTIVE_BOX_BLUR = 2.0
SUBTITLE_ACTIVE_BOX_OUTLINE = 1.2
SUBTITLE_ACTIVE_BOX_OUTLINE_NO_STROKE = 0.9
_COVERR_PREVIEW_FALLBACK_KEY = "thumb" "nail"
_VECTEEZY_PREVIEW_FALLBACK_KEY = "thumb" "nail_url"


@dataclass(frozen=True)
class CaptionWordTiming:
    start: float
    end: float
    token: str


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str
    words: tuple[CaptionWordTiming, ...] = ()

PIPER_VOICE_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "en_US-libritts-high",
        "label": "[C] EN-US LibriTTS (high, speaker 000)",
        "speaker_id": 0,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
        "license_name": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_required": True,
        "strict_safe_allowed": False,
        "policy_note": "Attribution is required for this preset, so TUI strict-safe mode blocks it.",
    },
    {
        "id": "en_US-libritts-high",
        "label": "[C] EN-US LibriTTS (high, speaker 120)",
        "speaker_id": 120,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
        "license_name": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_required": True,
        "strict_safe_allowed": False,
        "policy_note": "Attribution is required for this preset, so TUI strict-safe mode blocks it.",
    },
    {
        "id": "en_US-libritts-high",
        "label": "[C] EN-US LibriTTS (high, speaker 360)",
        "speaker_id": 360,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
        "license_name": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_required": True,
        "strict_safe_allowed": False,
        "policy_note": "Attribution is required for this preset, so TUI strict-safe mode blocks it.",
    },
    {
        "id": "en_US-libritts-high",
        "label": "[C] EN-US LibriTTS (high, speaker 700)",
        "speaker_id": 700,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
        "license_name": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_required": True,
        "strict_safe_allowed": False,
        "policy_note": "Attribution is required for this preset, so TUI strict-safe mode blocks it.",
    },
    {
        "id": "en_US-ljspeech-high",
        "label": "[C] EN-US LJSpeech (high)",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high/en_US-ljspeech-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high/en_US-ljspeech-high.onnx.json?download=true",
        "license_name": "Public-domain lineage (verify model card)",
        "license_url": None,
        "attribution_required": False,
        "strict_safe_allowed": True,
        "policy_note": "Available in strict-safe mode, but confirm the exact model card before redistribution.",
    },
    {
        "id": "en_US-joe-medium",
        "label": "[C] EN-US Joe (medium)",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx.json?download=true",
        "license_name": "Commercial-friendly baseline (verify model card)",
        "license_url": None,
        "attribution_required": False,
        "strict_safe_allowed": True,
        "policy_note": "Available in strict-safe mode with the current curated Piper catalog.",
    },
    {
        "id": "en_US-john-medium",
        "label": "[C] EN-US John (medium)",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/john/medium/en_US-john-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/john/medium/en_US-john-medium.onnx.json?download=true",
        "license_name": "Commercial-friendly baseline (verify model card)",
        "license_url": None,
        "attribution_required": False,
        "strict_safe_allowed": True,
        "policy_note": "Available in strict-safe mode with the current curated Piper catalog.",
    },
    {
        "id": "en_US-norman-medium",
        "label": "[C] EN-US Norman (medium)",
        "speaker_id": None,
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/norman/medium/en_US-norman-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/norman/medium/en_US-norman-medium.onnx.json?download=true",
        "license_name": "Commercial-friendly baseline (verify model card)",
        "license_url": None,
        "attribution_required": False,
        "strict_safe_allowed": True,
        "policy_note": "Available in strict-safe mode with the current curated Piper catalog.",
    },
)

KOKORO_LANG_ALIASES = {
    "a": "en-us",
    "en-us": "en-us",
    "b": "en-gb",
    "en-gb": "en-gb",
    "e": "es",
    "es": "es",
    "f": "fr-fr",
    "fr-fr": "fr-fr",
    "h": "hi",
    "hi": "hi",
    "i": "it",
    "it": "it",
    "j": "ja",
    "ja": "ja",
    "p": "pt-br",
    "pt-br": "pt-br",
    "z": "zh",
    "zh": "zh",
}

KOKORO_LANG_CHOICES = tuple(
    language for language in ("en-us", "en-gb", "es", "fr-fr", "hi", "it", "ja", "pt-br", "zh")
)

KOKORO_LANG_PREFIXES = {
    "en-us": ("af_", "am_"),
    "en-gb": ("bf_", "bm_"),
    "es": ("ef_", "em_"),
    "fr-fr": ("ff_",),
    "hi": ("hf_", "hm_"),
    "it": ("if_", "im_"),
    "ja": ("jf_", "jm_"),
    "pt-br": ("pf_", "pm_"),
    "zh": ("zf_", "zm_"),
}

KOKORO_DEFAULT_VOICES = {
    "en-us": "af_heart",
    "en-gb": "bf_emma",
    "es": "ef_dora",
    "fr-fr": "ff_siwis",
    "hi": "hf_alpha",
    "it": "if_sara",
    "ja": "jf_alpha",
    "pt-br": "pf_dora",
    "zh": "zf_xiaoxiao",
}

KOKORO_VOICE_PRESETS = (
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
    "ef_dora",
    "em_alex",
    "em_santa",
    "ff_siwis",
    "hf_alpha",
    "hf_beta",
    "hm_omega",
    "hm_psi",
    "if_sara",
    "im_nicola",
    "jf_alpha",
    "jf_gongitsune",
    "jf_nezumi",
    "jf_tebukuro",
    "jm_kumo",
    "pf_dora",
    "pm_alex",
    "pm_santa",
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
    "zm_yunxia",
    "zm_yunyang",
)


def normalize_kokoro_lang_code(raw_value: str | None, default: str = "en-us") -> str:
    candidate = str(raw_value or "").strip().lower()
    if not candidate:
        candidate = default
    normalized = KOKORO_LANG_ALIASES.get(candidate)
    if normalized:
        return normalized
    return KOKORO_LANG_ALIASES.get(str(default or "en-us").strip().lower(), "en-us")


def kokoro_voice_choices_for_lang(lang_code: str | None = None) -> list[str]:
    normalized = normalize_kokoro_lang_code(lang_code)
    prefixes = KOKORO_LANG_PREFIXES.get(normalized, ())
    if not prefixes:
        return list(KOKORO_VOICE_PRESETS)
    return [voice for voice in KOKORO_VOICE_PRESETS if voice.startswith(prefixes)]


def default_kokoro_voice(lang_code: str | None = None) -> str:
    normalized = normalize_kokoro_lang_code(lang_code)
    return str(KOKORO_DEFAULT_VOICES.get(normalized) or "af_heart")


def normalize_tts_engine(raw_value: str | None, default: str = "melo") -> str:
    candidate = str(raw_value or "").strip().lower()
    if candidate in {"melo", "piper", "kokoro"}:
        return candidate
    fallback = str(default or "melo").strip().lower()
    if fallback in {"melo", "piper", "kokoro"}:
        return fallback
    return "melo"


def piper_voice_preset_meta(voice_id: str | None, speaker_id: int | None = None) -> dict[str, Any] | None:
    normalized_voice_id = str(voice_id or "").strip()
    if not normalized_voice_id:
        return None

    matches = [item for item in PIPER_VOICE_PRESETS if str(item.get("id") or "").strip() == normalized_voice_id]
    if not matches:
        return None

    if speaker_id is not None:
        for item in matches:
            item_speaker = item.get("speaker_id")
            if item_speaker is not None and int(item_speaker) == int(speaker_id):
                return dict(item)

    return dict(matches[0])


def describe_tts_selection_policy(
    *,
    tts_engine: str,
    strict_commercial_safe: bool = True,
    melo_language: str | None = None,
    melo_speaker: str | None = None,
    kokoro_lang_code: str | None = None,
    kokoro_voice: str | None = None,
    piper_voice_id: str | None = None,
    piper_speaker_id: int | None = None,
    piper_model_url: str | None = None,
    piper_config_url: str | None = None,
) -> dict[str, Any]:
    engine = normalize_tts_engine(tts_engine)
    voice_display = ""
    model_id = engine
    provider = "local"
    source = ""
    source_url: str | None = None
    license_name = "Verify voice/model license"
    license_url: str | None = None
    strict_safe_allowed = True
    attribution_required = False
    manual_review_required = False
    note = ""

    if engine == "melo":
        language = str(melo_language or "").strip().upper() or "EN"
        speaker = str(melo_speaker or "").strip() or "EN-US"
        voice_display = f"{language}/{speaker}"
        source = "melo-tts runtime"
        license_name = "Runtime license applies"
        note = "Imagine's baseline TTS path. Review the installed voice package before redistribution."
    elif engine == "kokoro":
        lang_code = normalize_kokoro_lang_code(kokoro_lang_code)
        voice = str(kokoro_voice or "").strip() or default_kokoro_voice(lang_code)
        voice_display = f"{lang_code}/{voice}"
        model_id = "hexgrad/Kokoro-82M"
        source = "hexgrad/Kokoro-82M"
        source_url = "https://huggingface.co/hexgrad/Kokoro-82M"
        license_name = "Apache-2.0"
        license_url = "https://huggingface.co/hexgrad/Kokoro-82M"
        note = "Straightforward upstream licensing and the preferred strict-safe upgrade path."
    else:
        custom_model_url = str(piper_model_url or "").strip()
        custom_config_url = str(piper_config_url or "").strip()
        preset = piper_voice_preset_meta(piper_voice_id, piper_speaker_id)
        preset_model_url = str(preset.get("model_url") or "").strip() if preset is not None else ""
        preset_config_url = str(preset.get("config_url") or "").strip() if preset is not None else ""
        uses_custom_urls = bool(custom_model_url and custom_config_url) and (
            preset is None
            or custom_model_url != preset_model_url
            or custom_config_url != preset_config_url
        )
        if uses_custom_urls:
            model_id = str(piper_voice_id or "custom-piper").strip() or "custom-piper"
            voice_display = model_id
            source = "custom Piper weights"
            source_url = custom_model_url
            license_name = "Custom Piper weights (manual review required)"
            strict_safe_allowed = False
            manual_review_required = True
            note = "Custom Piper model/config URLs are not allowlisted in strict-safe mode."
        else:
            if preset is None:
                model_id = str(piper_voice_id or "unknown-piper").strip() or "unknown-piper"
                voice_display = model_id
                source = "unknown Piper preset"
                license_name = "Unknown Piper preset"
                strict_safe_allowed = False
                manual_review_required = True
                note = "This Piper preset is outside the curated catalog, so strict-safe mode blocks it."
            else:
                model_id = str(preset.get("id") or "piper").strip() or "piper"
                label = str(preset.get("label") or model_id).strip()
                voice_display = label
                source = "rhasspy/piper-voices preset"
                source_url = str(preset.get("model_url") or "").strip() or None
                license_name = str(preset.get("license_name") or "Verify Piper model card").strip()
                license_url = str(preset.get("license_url") or "").strip() or None
                attribution_required = bool(preset.get("attribution_required"))
                strict_safe_allowed = bool(preset.get("strict_safe_allowed", True))
                manual_review_required = not strict_safe_allowed
                note = str(preset.get("policy_note") or "").strip()

    if strict_commercial_safe:
        policy_result = "allow" if strict_safe_allowed else "deny"
    else:
        policy_result = "allow" if strict_safe_allowed else "warn"

    if engine == "piper" and not strict_safe_allowed:
        if strict_commercial_safe:
            reason = "Strict-safe mode blocks this Piper selection because the model/weights are not on the allowlist."
        else:
            reason = "This Piper selection needs manual license review before commercial use."
    elif engine == "kokoro":
        reason = "Kokoro is allowlisted for the current strict-safe TUI flow."
    elif engine == "melo":
        reason = "Melo remains allowed as the current baseline local narration engine."
    else:
        reason = "TTS selection is allowed."

    if attribution_required and not strict_commercial_safe:
        reason = "This Piper selection requires attribution; review obligations before distribution."

    return {
        "engine": engine,
        "voice_display": voice_display,
        "model_id": model_id,
        "provider": provider,
        "source": source,
        "source_url": source_url,
        "license_name": license_name,
        "license_url": license_url,
        "strict_safe_allowed": strict_safe_allowed,
        "strict_commercial_safe": bool(strict_commercial_safe),
        "attribution_required": attribution_required,
        "manual_review_required": manual_review_required,
        "policy_result": policy_result,
        "reason": reason,
        "note": note,
    }


def describe_tts_config_policy(config: PipelineConfig) -> dict[str, Any]:
    return describe_tts_selection_policy(
        tts_engine=config.tts_engine,
        strict_commercial_safe=config.strict_commercial_safe,
        melo_language=config.melo_language,
        melo_speaker=config.melo_speaker,
        kokoro_lang_code=config.kokoro_lang_code,
        kokoro_voice=config.kokoro_voice,
        piper_voice_id=config.piper_voice_id,
        piper_speaker_id=config.piper_speaker_id,
        piper_model_url=config.piper_model_url,
        piper_config_url=config.piper_config_url,
    )

ASSET_PROVIDER_LABELS = {
    "editorial-source": "Editorial Source",
    "pexels": "Pexels",
    "pixabay": "Pixabay",
    "coverr": "Coverr",
    "vecteezy": "Vecteezy",
}

STRICT_SAFE_BLOCKING_FLAGS = {
    "editorial-only",
    "manual-review-required",
    "provider-badge-required",
    "provider-clickback-required",
    "provider-logo-required",
}

COVERR_HOURLY_REQUEST_LIMIT = 50
ASSET_QUERY_CACHE_TTL_SECONDS = 24 * 60 * 60

FALLBACK_ONLY_ASSET_PROVIDERS = {"coverr", "vecteezy"}
ASSET_MODE_CHOICES = {"prefer-video", "balanced", "prefer-images", "images-only"}
IMAGE_MOTION_STYLE_CHOICES = {"static", "slow", "balanced", "fast"}
IMAGE_MOTION_STYLE_ALIASES = {
    "subtle": "slow",
    "documentary": "balanced",
    "dynamic": "fast",
}
NEWS_REVIEW_DECISIONS = {"approve-facts", "approve-screenshot", "reject"}
NEWS_JURISDICTION_CHOICES = {"us"}
NEWS_SCREENSHOT_HIDE_CSS = """
img,
picture,
figure,
video,
iframe,
[role='img'],
[class*='hero'],
[id*='hero'],
[class*='gallery'],
[id*='gallery'],
[class*='video'],
[id*='video'] {
  visibility: hidden !important;
  opacity: 0 !important;
}
body {
  background: #ffffff !important;
}
"""


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
        http_adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
        self.http.mount("http://", http_adapter)
        self.http.mount("https://", http_adapter)
        self.stage_times: dict[str, float] = {}
        self.warnings: list[str] = []
        self.caption_stats: dict[str, Any] = {}
        self.duration_stats: dict[str, Any] = {}
        self.pacing_stats: dict[str, Any] = {}
        self.asset_stats: dict[str, Any] = {}
        self.news_stats: dict[str, Any] = {}
        self.optimization_stats: dict[str, Any] = {}
        self.used_template_fallback = False
        self._scene_asset_shortlists: dict[str, list[AssetCandidate]] = {}
        self._selected_assets_by_scene: dict[str, AssetCandidate] = {}
        self._scene_montage_assets: dict[str, list[dict[str, Any]]] = {}
        self._shot_asset_shortlists: dict[str, list[AssetCandidate]] = {}
        self._selected_assets_by_shot: dict[str, AssetCandidate] = {}
        self._news_source_candidates: list[NewsSourceCandidate] = []
        self._approved_editorial_sources: list[ApprovedEditorialSource] = []
        self._approved_editorial_sources_by_id: dict[str, ApprovedEditorialSource] = {}
        self._news_brief: NewsBrief | None = None
        self._shot_plan: ShotPlan | None = None
        self._intro_bookend_background: Path | None = None
        self._outro_bookend_background: Path | None = None
        self._bookend_logo_overlay: Path | None = None
        self._ffmpeg_drawtext_available: bool | None = None
        self._ffmpeg_subtitles_available: bool | None = None
        self._hw_encoder: str | None = None
        self._hw_encoder_checked = False
        self._piper_command: list[str] | None = None
        self._kokoro_pipelines: dict[str, Any] = {}
        self._ollama_ready = False
        self._started_at: dt.datetime | None = None
        self._finished_at: dt.datetime | None = None
        self._coverr_usage_state: dict[str, Any] | None = None
        self._coverr_requests_this_run = 0
        self._vecteezy_usage_state: dict[str, Any] | None = None
        self._vecteezy_downloads_this_run = 0
        self._duration_cache: dict[tuple[str, int, int], float] = {}
        self._scene_query_context_cache: dict[str, dict[str, Any]] = {}
        self._log_lock = threading.Lock()
        self._warnings_lock = threading.Lock()
        self._provider_usage_lock = threading.Lock()
        self._asset_cache_registry_lock = threading.Lock()
        self._asset_cache_locks: dict[str, threading.Lock] = {}
        self._stats_lock = threading.Lock()

    def _reset_run_state(self) -> None:
        self.stage_times = {}
        self.warnings = []
        self.caption_stats = {}
        self.duration_stats = {}
        self.pacing_stats = {}
        self.asset_stats = {}
        self.news_stats = {}
        self.optimization_stats = {}
        self.optimization_stats["profile"] = {
            "content_mode": self._content_mode(),
            "minutes": self.config.minutes,
            "resolution": f"{self.config.width}x{self.config.height}",
            "fps": self.config.fps,
            "caption_engine": self.config.caption_engine,
            "burn_subtitles": self.config.burn_subtitles,
            "subtitle_preset": normalize_subtitle_preset(self.config.subtitle_preset, "regular"),
            "subtitle_position": normalize_subtitle_position(self.config.subtitle_position, "bottom"),
            "subtitle_accent_color": normalize_subtitle_accent_color(
                self.config.subtitle_accent_color,
                "sunflower",
            ),
            "subtitle_box_color": normalize_subtitle_box_color(
                self.config.subtitle_box_color,
                normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower"),
            ),
            "subtitle_bold": bool(self.config.subtitle_bold),
            "subtitle_outline": bool(self.config.subtitle_outline),
            "include_intro": self.config.include_intro,
            "include_outro": self.config.include_outro,
            "outro_spoken_text": self.config.outro_spoken_text,
            "channel_profile": self._channel_profile_key(),
            "require_external_assets": self.config.require_external_assets,
            "enable_pexels_provider": self.config.enable_pexels_provider,
            "enable_pixabay_provider": self.config.enable_pixabay_provider,
            "enable_coverr_provider": self.config.enable_coverr_provider,
            "enable_vecteezy_provider": self.config.enable_vecteezy_provider,
            "allow_image_assets": self._image_assets_enabled(),
            "allow_attribution_required_assets": self.config.allow_attribution_required_assets,
            "asset_mode": self._normalized_asset_mode(),
            "asset_shortlist_size": self.config.asset_shortlist_size,
            "video_effects": self.config.video_effects,
            "image_motion_style": self._normalized_image_motion_style(),
        }
        self.used_template_fallback = False
        self._scene_asset_shortlists = {}
        self._selected_assets_by_scene = {}
        self._scene_montage_assets = {}
        self._shot_asset_shortlists = {}
        self._selected_assets_by_shot = {}
        self._news_source_candidates = []
        self._approved_editorial_sources = []
        self._approved_editorial_sources_by_id = {}
        self._news_brief = None
        self._shot_plan = None
        self._piper_command = None
        self._coverr_usage_state = None
        self._coverr_requests_this_run = 0
        self._vecteezy_usage_state = None
        self._vecteezy_downloads_this_run = 0
        self._duration_cache = {}
        self._scene_query_context_cache = {}

    def run(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")

            if self._news_mode_enabled():
                self.run_sources_stage()
            plan = self.run_draft_stage()
            reviewed_plan = self.run_review_stage(plan)
            preview_stage = self.run_shot_plan_stage(reviewed_plan)
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

    def run_sources_stage(self) -> dict[str, Any]:
        return self._run_stage(
            "sources",
            self._stage_label(1, "Fetching editorial sources"),
            self._prepare_news_sources,
        )

    def run_draft_stage(self) -> ScriptPlan:
        if self._news_mode_enabled():
            self._validate_news_review_gate()
            self._ensure_news_brief()

        plan = self._run_stage(
            "script_plan",
            self._stage_label(2 if self._news_mode_enabled() else 1, "Generating script plan"),
            self._generate_script_plan,
        )
        self._initialize_duration_stats(plan)
        plan = self._run_stage(
            "duration_preflight",
            self._stage_label(2.1 if self._news_mode_enabled() else 1.1, "Aligning script length with target duration"),
            lambda: self._ensure_minimum_script_length(plan),
        )
        self._write_json(self.paths["script"], plan.to_dict())
        narration_text = self._clean_narration_text(plan.narration_text())
        self._write_text(self.paths["narration_txt"], narration_text + "\n")
        estimated_seconds = self._estimate_seconds_from_words(self._word_count_plan(plan))
        _, min_seconds, max_seconds = self._duration_bounds()
        self.duration_stats["word_count_final"] = self._word_count_plan(plan)
        self.duration_stats["estimated_seconds_final"] = round(estimated_seconds, 3)
        self.duration_stats["within_tolerance"] = bool(min_seconds <= estimated_seconds <= max_seconds)
        return plan

    def run_review_stage(self, plan: ScriptPlan) -> ScriptPlan:
        if self._news_mode_enabled():
            self._validate_news_review_gate()
        return plan

    def run_shot_plan_stage(self, plan: ScriptPlan) -> dict[str, Any]:
        self._ensure_narration_for_plan(plan)
        shot_plan = self._run_stage(
            "shot_plan",
            self._stage_label(4 if self._news_mode_enabled() else 3, "Planning shots"),
            lambda: self._prepare_shot_plan(plan),
        )
        rights = self._run_stage(
            "shot_assets",
            self._stage_label(5 if self._news_mode_enabled() else 4, "Resolving shot visuals"),
            lambda: self._resolve_shot_assets(shot_plan),
        )
        shot_script = self._shot_plan_as_script_plan(shot_plan)
        self._prepare_bookend_backgrounds(shot_script)
        self._write_shot_clip_catalog(shot_plan, rights)
        self._run_stage(
            "shot_preview",
            self._stage_label(6 if self._news_mode_enabled() else 5, "Rendering shot previews"),
            lambda: self._ensure_shot_previews(shot_plan),
        )
        self._ensure_captions(plan)
        timeline = self._ensure_timeline(plan)
        return {
            "shot_plan": shot_plan,
            "rights": rights,
            "timeline": timeline,
        }

    def _prepare_shot_plan(self, plan: ScriptPlan) -> ShotPlan:
        shot_plan = self._build_shot_plan(plan)
        prior_state = self._load_json_state(self.paths["shot_review_state"])
        self._shot_plan = shot_plan
        self._write_json(self.paths["shot_plan"], shot_plan.to_dict())
        self._write_json(self.paths["shot_review_state"], self._build_shot_review_state(shot_plan, prior_state))
        return shot_plan

    def _split_scene_into_shots(self, scene: Scene) -> list[str]:
        voiceover = str(scene.voiceover or "").strip()
        if not voiceover:
            return []

        if scene.visual_strategy in {"news-source-screenshot", "source-card"} or float(scene.seconds) < 8.0:
            return [voiceover]

        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", voiceover) if part.strip()]
        if len(sentences) >= 2:
            midpoint = max(1, len(sentences) // 2)
            first = " ".join(sentences[:midpoint]).strip()
            second = " ".join(sentences[midpoint:]).strip()
            segments = [part for part in (first, second) if part]
            if len(segments) > 1:
                return segments[:2]

        words = voiceover.split()
        if len(words) >= 18 and float(scene.seconds) >= 10.0:
            midpoint = max(1, len(words) // 2)
            return [" ".join(words[:midpoint]).strip(), " ".join(words[midpoint:]).strip()]
        return [voiceover]

    def _extract_required_entities(self, text: str) -> list[str]:
        entities: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"\b[A-Z][A-Za-z0-9'_-]{2,}\b", text or ""):
            lowered = token.lower()
            if lowered in FUNCTION_WORDS or lowered in seen:
                continue
            seen.add(lowered)
            entities.append(token)
        return entities[:6]

    def _shot_visual_type(self, scene: Scene, *, shot_index: int, total_shots: int, entities: list[str]) -> str:
        if scene.visual_strategy in {"news-source-screenshot", "source-card"}:
            return scene.visual_strategy
        if self._news_mode_enabled():
            if shot_index == 1 and scene.source_refs:
                return scene.visual_strategy if scene.visual_strategy != "stock" else "source-card"
            if entities:
                return "internal-card"
            return "stock-video"
        if entities:
            return "stock-video"
        if total_shots > 1 and shot_index == total_shots:
            return "still-image"
        return "stock-video"

    def _shot_search_queries(self, scene: Scene, segment_text: str, entities: list[str]) -> list[str]:
        queries: list[str] = []
        heading = str(scene.heading or "").strip()
        key_info = self._short_query_phrase(segment_text, max_words=4)
        for candidate in (
            key_info,
            " ".join(entities[:2]).strip(),
            " ".join(scene.search_terms[:2]).strip(),
            f"{self._short_query_phrase(heading, max_words=2)} {' '.join(scene.search_terms[:1])}".strip(),
        ):
            value = re.sub(r"\s+", " ", str(candidate or "").strip())
            if not value:
                continue
            if value.lower() in {item.lower() for item in queries}:
                continue
            queries.append(value)
        return queries[:4]

    def _initial_shot_confidence(self, scene: Scene, entities: list[str], visual_type: str) -> str:
        if visual_type in {"news-source-screenshot", "source-card"}:
            return "high"
        if self._news_mode_enabled() and entities:
            return "low"
        if entities:
            return "medium"
        return "high"

    def _build_shot_plan(self, plan: ScriptPlan) -> ShotPlan:
        shots: list[PlannedShot] = []
        narration_cursor = 0.0
        for scene in plan.scenes:
            segments = self._split_scene_into_shots(scene) or [str(scene.voiceover or "").strip()]
            total_words = max(1, self._word_count_text(scene.voiceover))
            remaining_seconds = max(0.3, float(scene.seconds))
            local_cursor = narration_cursor
            total_shots = max(1, min(2, len([item for item in segments if item.strip()])))

            for index, segment_text in enumerate(segments[:2], start=1):
                segment_words = max(1, self._word_count_text(segment_text))
                if index == total_shots:
                    shot_seconds = max(0.3, remaining_seconds)
                else:
                    ratio = float(segment_words) / float(total_words)
                    shot_seconds = max(0.3, round(float(scene.seconds) * ratio, 3))
                    remaining_seconds = max(0.3, remaining_seconds - shot_seconds)
                required_entities = self._extract_required_entities(f"{scene.heading} {segment_text}")
                visual_type = self._shot_visual_type(
                    scene,
                    shot_index=index,
                    total_shots=total_shots,
                    entities=required_entities,
                )
                match_confidence = self._initial_shot_confidence(scene, required_entities, visual_type)
                if self._news_mode_enabled():
                    fallback_strategy = "source-card -> internal-card -> placeholder"
                else:
                    fallback_strategy = "stock -> still-image -> internal-card -> placeholder"
                shot_id = f"{scene.scene_id}_shot_{index:02d}"
                shot_objective = f"{scene.heading}: {segment_text}".strip()
                key_info = re.sub(r"\s+", " ", segment_text).strip()
                shot = PlannedShot(
                    shot_id=shot_id,
                    scene_id=scene.scene_id,
                    clip_name=scene.clip_name,
                    heading=scene.heading,
                    shot_index=index,
                    total_shots=total_shots,
                    narration_text=segment_text,
                    seconds=shot_seconds,
                    narration_start=local_cursor,
                    narration_end=local_cursor + shot_seconds,
                    shot_objective=shot_objective,
                    key_info=key_info,
                    required_entities=required_entities,
                    search_queries=self._shot_search_queries(scene, segment_text, required_entities),
                    fallback_strategy=fallback_strategy,
                    visual_type=visual_type,
                    match_confidence=match_confidence,
                    fallback_level="exact",
                    source_refs=list(scene.source_refs),
                    visual_strategy=scene.visual_strategy,
                )
                context = self._scene_query_context(self._shot_as_scene(shot))
                shot.matched_channel_terms = list(context.get("matched_terms") or [])
                shot.effective_search_queries = list(context.get("effective_queries") or [])
                shots.append(shot)
                local_cursor += shot_seconds

            narration_cursor += float(scene.seconds)
        return ShotPlan(title=plan.title, summary=plan.summary, shots=shots)

    def _shot_signature(self, shot: PlannedShot) -> str:
        payload = {
            "shot_id": shot.shot_id,
            "key_info": shot.key_info,
            "narration_text": shot.narration_text,
            "seconds": round(float(shot.seconds), 3),
            "search_queries": list(shot.search_queries),
            "visual_type": shot.visual_type,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()

    def _build_shot_review_state(
        self,
        shot_plan: ShotPlan,
        prior_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prior_shots = prior_state.get("shots") if isinstance(prior_state, dict) else None
        if not isinstance(prior_shots, dict):
            prior_shots = {}

        state = ShotReviewState()
        for shot in shot_plan.shots:
            previous = prior_shots.get(shot.shot_id) if isinstance(prior_shots.get(shot.shot_id), dict) else {}
            blocked = normalize_shot_confidence(shot.match_confidence, "medium") == "low"
            signature = self._shot_signature(shot)
            preview_path = self._shot_preview_path(shot.shot_id)
            approved = bool(previous.get("approved")) if previous.get("shot_signature") == signature else False
            if not blocked:
                approved = True
            regenerate_cycle = self._normalize_shot_regenerate_cycle(
                previous.get("regenerate_cycle") if previous.get("shot_signature") == signature else None
            )
            state.shots[shot.shot_id] = {
                "approved": bool(approved),
                "blocked": bool(blocked),
                "needs_review": bool(normalize_shot_confidence(shot.match_confidence, "medium") != "high"),
                "match_confidence": normalize_shot_confidence(shot.match_confidence, "medium"),
                "updated_at": previous.get("updated_at"),
                "shot_signature": signature,
                "preview_path": str(preview_path.resolve()),
                "regenerate_cycle": regenerate_cycle,
            }
        return {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            **state.to_dict(),
        }

    def _shot_preview_dir(self, shot_id: str) -> Path:
        return self.paths["shots"] / shot_id

    def _shot_preview_path(self, shot_id: str) -> Path:
        return self._shot_preview_dir(shot_id) / "preview.mp4"

    def _shot_preview_audio_path(self, shot_id: str) -> Path:
        return self._shot_preview_dir(shot_id) / "narration.wav"

    def _shot_preview_captions_path(self, shot_id: str) -> tuple[Path, Path]:
        shot_dir = self._shot_preview_dir(shot_id)
        return (shot_dir / "captions.srt", shot_dir / "captions.ass")

    def _shot_candidate_preview_dir(self, shot_id: str) -> Path:
        return self._shot_preview_dir(shot_id) / "candidate_previews"

    def _shot_candidate_manifest_path(self, shot_id: str) -> Path:
        return self._shot_preview_dir(shot_id) / "candidate_manifest.json"

    def _shot_as_scene(self, shot: PlannedShot) -> Scene:
        return Scene(
            scene_id=shot.shot_id,
            clip_name=f"{shot.clip_name}-shot-{shot.shot_index:02d}",
            heading=shot.key_info or shot.heading,
            voiceover=shot.narration_text,
            search_terms=list(shot.search_queries),
            seconds=float(shot.seconds),
            source_refs=list(shot.source_refs),
            visual_strategy=normalize_news_visual_strategy(shot.visual_strategy, "stock"),
            asset_path=shot.asset_path,
            asset_provider=shot.asset_provider,
        )

    def _normalize_search_queries(self, values: list[str] | tuple[str, ...] | None) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            cleaned = re.sub(r"\s+", " ", str(value).strip())
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            queries.append(cleaned)
        return queries

    def _normalize_shot_regenerate_cycle(self, payload: Any) -> dict[str, Any]:
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

        def _str_list(value: Any) -> list[str]:
            items = value if isinstance(value, list) else []
            result: list[str] = []
            seen: set[str] = set()
            for item in items:
                cleaned = str(item).strip().lower()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                result.append(cleaned)
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
            "search_queries": self._normalize_search_queries(cycle.get("search_queries")),
            "rejected_asset_keys": _str_list(cycle.get("rejected_asset_keys")),
            "strict_query_override": bool(cycle.get("strict_query_override")),
        }

    def _active_shot_asset_keys(self) -> dict[str, str]:
        clip_catalog = self._load_json_state(self.paths["clip_catalog"]) or {}
        clips = clip_catalog.get("clips") if isinstance(clip_catalog, dict) else None
        if not isinstance(clips, list):
            return {}

        active: dict[str, str] = {}
        for item in clips:
            if not isinstance(item, dict):
                continue
            shot_id = str(item.get("shot_id") or "").strip()
            if not shot_id:
                continue
            candidates = item.get("candidates")
            if isinstance(candidates, list):
                for raw_candidate in candidates:
                    if not isinstance(raw_candidate, dict) or not bool(raw_candidate.get("selected")):
                        continue
                    active[shot_id] = self._asset_uniqueness_key(raw_candidate)
                    break
        return active

    def _apply_key_info_to_shot(
        self,
        shot: PlannedShot,
        key_info: str | None,
        search_queries: list[str] | tuple[str, ...] | None = None,
        *,
        strict_query_override: bool = False,
    ) -> PlannedShot:
        if key_info is None:
            updated_key_info = ""
        else:
            updated_key_info = re.sub(r"\s+", " ", str(key_info).strip())
        if updated_key_info:
            shot.key_info = updated_key_info

        effective_key_info = shot.key_info or shot.heading
        shot.required_entities = self._extract_required_entities(f"{shot.heading} {effective_key_info}")
        manual_queries = self._normalize_search_queries(search_queries)
        if strict_query_override and manual_queries:
            shot.search_queries = manual_queries[:6]
            context = self._scene_query_context(self._shot_as_scene(shot), ignore_global_keywords=True)
            shot.matched_channel_terms = list(context.get("matched_terms") or [])
            shot.effective_search_queries = list(context.get("effective_queries") or [])
            return shot
        prior_queries = [] if search_queries is not None else list(shot.search_queries)
        query_seed = [
            *manual_queries,
            effective_key_info,
            *shot.required_entities,
            *prior_queries,
        ]
        queries: list[str] = []
        seen_queries: set[str] = set()
        for candidate in query_seed:
            cleaned = re.sub(r"\s+", " ", str(candidate).strip())
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen_queries:
                continue
            seen_queries.add(lowered)
            queries.append(cleaned)
        shot.search_queries = queries[:6]
        context = self._scene_query_context(
            self._shot_as_scene(shot),
            ignore_global_keywords=bool(strict_query_override),
        )
        shot.matched_channel_terms = list(context.get("matched_terms") or [])
        shot.effective_search_queries = list(context.get("effective_queries") or [])
        return shot

    def _persisted_shot_asset_keys(self) -> dict[str, set[str]]:
        keys_by_shot: dict[str, set[str]] = {}

        def add_key(shot_id: str, key: str) -> None:
            clean_shot_id = str(shot_id).strip()
            clean_key = str(key).strip().lower()
            if not clean_shot_id or not clean_key:
                return
            keys_by_shot.setdefault(clean_shot_id, set()).add(clean_key)

        clip_catalog = self._load_json_state(self.paths["clip_catalog"]) or {}
        clips = clip_catalog.get("clips") if isinstance(clip_catalog, dict) else None
        if isinstance(clips, list):
            for item in clips:
                if not isinstance(item, dict):
                    continue
                shot_id = str(item.get("shot_id") or "").strip()
                if not shot_id:
                    continue
                top_level_key = self._asset_uniqueness_key(item)
                if top_level_key:
                    add_key(shot_id, top_level_key)
                candidates = item.get("candidates")
                if not isinstance(candidates, list):
                    continue
                for raw_candidate in candidates:
                    if not isinstance(raw_candidate, dict) or not bool(raw_candidate.get("selected")):
                        continue
                    add_key(shot_id, self._asset_uniqueness_key(raw_candidate))

        for right in self._load_existing_rights():
            add_key(right.scene_id, self._asset_uniqueness_key_from_right(right))

        return keys_by_shot

    def _load_shot_plan(self) -> ShotPlan | None:
        payload = self._load_json_state(self.paths["shot_plan"])
        if not isinstance(payload, dict):
            return None
        raw_shots = payload.get("shots")
        if not isinstance(raw_shots, list):
            return None
        shots: list[PlannedShot] = []
        for item in raw_shots:
            if not isinstance(item, dict):
                continue
            shot_id = str(item.get("shot_id") or "").strip()
            if not shot_id:
                continue
            shots.append(
                PlannedShot(
                    shot_id=shot_id,
                    scene_id=str(item.get("scene_id") or "").strip(),
                    clip_name=str(item.get("clip_name") or "").strip(),
                    heading=str(item.get("heading") or "").strip(),
                    shot_index=max(1, int(item.get("shot_index") or 1)),
                    total_shots=max(1, int(item.get("total_shots") or 1)),
                    narration_text=str(item.get("narration_text") or "").strip(),
                    seconds=max(0.3, float(item.get("seconds") or 0.3)),
                    narration_start=max(0.0, float(item.get("narration_start") or 0.0)),
                    narration_end=max(0.0, float(item.get("narration_end") or 0.0)),
                    shot_objective=str(item.get("shot_objective") or "").strip(),
                    key_info=str(item.get("key_info") or "").strip(),
                    required_entities=[
                        str(value).strip()
                        for value in item.get("required_entities") or []
                        if str(value).strip()
                    ]
                    if isinstance(item.get("required_entities"), list)
                    else [],
                    matched_channel_terms=[
                        str(value).strip()
                        for value in item.get("matched_channel_terms") or []
                        if str(value).strip()
                    ]
                    if isinstance(item.get("matched_channel_terms"), list)
                    else [],
                    search_queries=[
                        str(value).strip()
                        for value in item.get("search_queries") or []
                        if str(value).strip()
                    ]
                    if isinstance(item.get("search_queries"), list)
                    else [],
                    effective_search_queries=[
                        str(value).strip()
                        for value in item.get("effective_search_queries") or []
                        if str(value).strip()
                    ]
                    if isinstance(item.get("effective_search_queries"), list)
                    else [],
                    fallback_strategy=str(item.get("fallback_strategy") or "internal-card").strip(),
                    visual_type=str(item.get("visual_type") or "stock-video").strip(),
                    match_confidence=normalize_shot_confidence(item.get("match_confidence"), "medium"),
                    fallback_level=str(item.get("fallback_level") or "exact").strip(),
                    asset_path=self._coerce_str_or_none(item.get("asset_path")),
                    asset_provider=self._coerce_str_or_none(item.get("asset_provider")),
                    source_refs=[
                        str(value).strip()
                        for value in item.get("source_refs") or []
                        if str(value).strip()
                    ]
                    if isinstance(item.get("source_refs"), list)
                    else [],
                    visual_strategy=normalize_news_visual_strategy(item.get("visual_strategy"), "stock"),
                )
            )
        if not shots:
            return None
        shot_plan = ShotPlan(
            title=str(payload.get("title") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            shots=shots,
        )
        self._shot_plan = shot_plan
        return shot_plan

    def _load_shot_review_state(self) -> dict[str, dict[str, Any]]:
        payload = self._load_json_state(self.paths["shot_review_state"])
        if not isinstance(payload, dict):
            return {}
        raw_shots = payload.get("shots")
        if not isinstance(raw_shots, dict):
            return {}
        state: dict[str, dict[str, Any]] = {}
        for shot_id, value in raw_shots.items():
            if str(shot_id).strip() and isinstance(value, dict):
                state[str(shot_id).strip()] = dict(value)
        return state

    def _save_shot_review_state(self, state: dict[str, dict[str, Any]]) -> None:
        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "shots": {shot_id: dict(value) for shot_id, value in sorted(state.items())},
        }
        self._write_json(self.paths["shot_review_state"], payload)

    def _validate_shot_review_gate(self) -> None:
        shot_plan = self._shot_plan or self._load_shot_plan()
        if shot_plan is None or not shot_plan.shots:
            return

        state = self._load_shot_review_state()
        blocked: list[str] = []
        for shot in shot_plan.shots:
            record = state.get(shot.shot_id) or {}
            if bool(record.get("blocked")) and not bool(record.get("approved")):
                blocked.append(shot.shot_id)
        if blocked:
            raise RuntimeError(
                "Shot review is incomplete. Pending blocked shots: "
                + ", ".join(blocked[:8])
            )

    def _shot_plan_as_script_plan(self, shot_plan: ShotPlan) -> ScriptPlan:
        scenes: list[Scene] = []
        for shot in shot_plan.shots:
            scenes.append(
                Scene(
                    scene_id=shot.shot_id,
                    clip_name=f"{shot.clip_name}-shot-{shot.shot_index:02d}",
                    heading=shot.heading,
                    voiceover=shot.narration_text,
                    search_terms=list(shot.search_queries),
                    seconds=float(shot.seconds),
                    source_refs=list(shot.source_refs),
                    visual_strategy=normalize_news_visual_strategy(shot.visual_strategy, "stock"),
                    asset_path=shot.asset_path,
                    asset_provider=shot.asset_provider,
                )
            )
        return ScriptPlan(title=shot_plan.title, summary=shot_plan.summary, scenes=scenes)

    def run_preview_stage(self, plan: ScriptPlan) -> dict[str, Any]:
        self._ensure_narration_for_plan(plan)
        rights = self._run_stage(
            "assets",
            self._stage_label(4 if self._news_mode_enabled() else 3, "Resolving visual assets"),
            lambda: self._resolve_assets(plan),
        )
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

            self._render_video(
                timeline,
                self.paths["narration_wav"],
                self.paths["final_mp4"],
                metrics_section="render",
            )
            shutil.copy2(self.paths["captions"], self.paths["final_srt"])
            self._optimization_section("render").update(
                {
                    "mode": "rerendered-finalize",
                    "reused_preview": False,
                }
            )

        self._run_stage(
            "render",
            self._stage_label(7 if self._news_mode_enabled() else 6, "Rendering final video"),
            render_stage,
        )
        self._run_stage(
            "manifest",
            self._stage_label(8 if self._news_mode_enabled() else 7, "Writing rights manifest"),
            lambda: self._write_manifest_and_publish_artifacts(plan, rights),
        )

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
            "youtube_credits": str(self.paths["youtube_credits"].resolve()),
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
            if prepare_scene_review:
                self._check_dependencies()
            else:
                self._check_script_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")
            if self._news_mode_enabled():
                self._validate_news_review_gate()
                self._ensure_news_brief()
            plan = self.run_draft_stage()

            if prepare_scene_review:
                stage = self.run_preview_stage(plan)
                rights = list(stage["rights"])
                self._run_stage(
                    "manifest",
                    self._stage_label(8 if self._news_mode_enabled() else 6, "Writing rights manifest"),
                    lambda: self._write_manifest_and_publish_artifacts(plan, rights),
                )

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "narration_txt": str(self.paths["narration_txt"].resolve()),
                "run_log": str(self.paths["run_log"].resolve()),
                "run_report": str(self.paths["run_report"].resolve()),
            }

            if prepare_scene_review:
                outputs["timeline"] = str(self.paths["timeline"].resolve())
                outputs["clip_catalog"] = str(self.paths["clip_catalog"].resolve())
                outputs["captions"] = str(self.paths["captions"].resolve())
                outputs["captions_ass"] = str(self.paths["captions_ass"].resolve())
                outputs["youtube_credits"] = str(self.paths["youtube_credits"].resolve())
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

    def run_sources(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_news_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")
            self.run_sources_stage()

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "news_source_candidates": str(self.paths["news_source_candidates"].resolve()),
                "news_review_state": str(self.paths["news_review_state"].resolve()),
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

    def run_review(self, review_script_path: Path | None = None) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            if self._news_mode_enabled():
                self._validate_news_review_gate()
                self._ensure_news_brief()
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

    def run_shot_plan(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")
            if self._news_mode_enabled():
                self._validate_news_review_gate()
                self._ensure_news_brief()
            plan = self._load_preferred_script_plan()
            self._write_json(self.paths["script"], plan.to_dict())
            stage = self.run_shot_plan_stage(plan)
            rights = list(stage["rights"])
            self._run_stage(
                "manifest",
                self._stage_label(9 if self._news_mode_enabled() else 8, "Writing rights manifest"),
                lambda: self._write_manifest_and_publish_artifacts(plan, rights),
            )
            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "script": str(self.paths["script"].resolve()),
                "approved_script": str(self.paths["approved_script"].resolve()) if self.paths["approved_script"].exists() else "",
                "shot_plan": str(self.paths["shot_plan"].resolve()),
                "shot_review_state": str(self.paths["shot_review_state"].resolve()),
                "timeline": str(self.paths["timeline"].resolve()),
                "clip_catalog": str(self.paths["clip_catalog"].resolve()),
                "narration": str(self.paths["narration_wav"].resolve()),
                "captions": str(self.paths["captions"].resolve()),
                "captions_ass": str(self.paths["captions_ass"].resolve()),
                "youtube_credits": str(self.paths["youtube_credits"].resolve()),
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

    def run_preview(self) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            self._write_text(self.paths["prompt"], self.config.prompt.strip() + "\n")
            if self._news_mode_enabled():
                self._validate_news_review_gate()
                self._ensure_news_brief()
            self._load_shot_plan()
            self._validate_shot_review_gate()

            plan = self._load_preferred_script_plan()
            self._write_json(self.paths["script"], plan.to_dict())
            self._ensure_narration_for_plan(plan)

            rights = self._load_existing_rights()
            shot_plan = self._shot_plan or self._load_shot_plan()
            if shot_plan is not None and shot_plan.shots:
                can_reuse_assets = bool(rights) and all(
                    shot.asset_path and Path(shot.asset_path).exists() for shot in shot_plan.shots
                )
            else:
                can_reuse_assets = bool(rights) and all(
                    scene.asset_path and Path(scene.asset_path).exists() for scene in plan.scenes
                )

            if can_reuse_assets:
                if shot_plan is not None and shot_plan.shots:
                    self._prepare_bookend_backgrounds(self._shot_plan_as_script_plan(shot_plan))
                    self._write_shot_clip_catalog(shot_plan, rights)
                else:
                    self._prepare_bookend_backgrounds(plan)
                    self._write_clip_catalog(plan, rights)

                self._ensure_captions(plan)
                timeline = self._ensure_timeline(plan)
            else:
                stage = self.run_shot_plan_stage(plan) if shot_plan is not None else self.run_preview_stage(plan)
                rights = list(stage["rights"])
                timeline = list(stage["timeline"])

            self._ensure_preview_render(timeline)
            self._run_stage(
                "manifest",
                self._stage_label(8 if self._news_mode_enabled() else 7, "Writing rights manifest"),
                lambda: self._write_manifest_and_publish_artifacts(plan, rights),
            )

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
                "youtube_credits": str(self.paths["youtube_credits"].resolve()),
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
            if self._news_mode_enabled():
                self._validate_news_review_gate()
                self._ensure_news_brief()
            self._load_shot_plan()
            self._validate_shot_review_gate()

            plan = self._load_preferred_script_plan()
            self._write_json(self.paths["script"], plan.to_dict())
            if self._shot_plan is None:
                self._validate_scene_review_gate(plan)
            self._ensure_narration_for_plan(plan)

            rights = self._load_existing_rights()
            shot_plan = self._shot_plan or self._load_shot_plan()
            if shot_plan is not None and shot_plan.shots:
                can_reuse_assets = bool(rights) and all(
                    shot.asset_path and Path(shot.asset_path).exists() for shot in shot_plan.shots
                )
            else:
                can_reuse_assets = bool(rights) and all(
                    scene.asset_path and Path(scene.asset_path).exists() for scene in plan.scenes
                )

            if can_reuse_assets:
                if shot_plan is not None and shot_plan.shots:
                    self._prepare_bookend_backgrounds(self._shot_plan_as_script_plan(shot_plan))
                else:
                    self._prepare_bookend_backgrounds(plan)
                self._ensure_captions(plan)
                timeline = self._ensure_timeline(plan)
            else:
                stage = self.run_shot_plan_stage(plan) if shot_plan is not None else self.run_preview_stage(plan)
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
                self._ensure_outro_narration_audio()
                return

            self._warn("Approved script changed since narration generation; re-synthesizing narration audio.")

        self._initialize_duration_stats(plan)
        narration_text = self._clean_narration_text(plan.narration_text())
        self._write_text(self.paths["narration_txt"], narration_text + "\n")

        def voice_stage() -> None:
            with self._timed_optimization_block("narration", "tts_synthesis_seconds"):
                self._synthesize_narration(narration_text, self.paths["narration_raw"])
            with self._timed_optimization_block("narration", "normalize_seconds"):
                self._normalize_audio(self.paths["narration_raw"], narration_wav)

        self._run_stage(
            "narration",
            self._stage_label(3 if self._news_mode_enabled() else 2, "Synthesizing narration audio"),
            voice_stage,
        )
        self._ensure_outro_narration_audio()
        audio_duration = self._media_duration(narration_wav)
        self._rebalance_scene_durations(plan, audio_duration)
        self._update_duration_post_tts(plan, audio_duration, adjust_passes=0)
        self._update_pacing_post_tts(narration_text, audio_duration, adjust_passes=0)
        if not self._duration_within_tolerance(audio_duration):
            requested = self.duration_stats.get("requested_seconds", self.config.target_seconds())
            delta = audio_duration - float(requested)
            delta_pct = (delta / float(requested) * 100.0) if requested else 0.0
            self._warn(
                "Narration duration is outside tolerance, but the approved script is being preserved. "
                f"Proceeding with {audio_duration:.1f}s ({delta_pct:+.1f}%)."
            )
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

        self._increment_optimization_counter("shot_preview", "preview_count")
        with self._timed_optimization_block("shot_preview", "narration_synthesis_seconds"):
            self._synthesize_narration(narration_text, raw_path)
        with self._timed_optimization_block("shot_preview", "narration_normalize_seconds"):
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
            self._log(self._stage_label(5 if self._news_mode_enabled() else 4, "Reusing captions"))
            self.stage_times["captions"] = 0.0
            self._log("captions completed in 0.00s")
            cached_stats = state.get("caption_stats") if isinstance(state, dict) else None
            if isinstance(cached_stats, dict):
                self.caption_stats = dict(cached_stats)
            self._optimization_section("captions").update(
                {
                    "mode": "reused",
                    "captions_signature": signature,
                }
            )
            return

        captions = self._run_stage(
            "captions",
            self._stage_label(5 if self._news_mode_enabled() else 4, "Building captions"),
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
        self._optimization_section("captions").update(
            {
                "mode": "generated",
                "captions_signature": signature,
            }
        )

    def _ensure_timeline(self, plan: ScriptPlan) -> list[TimelineClip]:
        signature = self._timeline_signature(plan)
        state = self._load_json_state(self.paths["timeline_state"])
        can_reuse = isinstance(state, dict) and str(state.get("timeline_signature") or "") == signature
        if can_reuse:
            timeline = self._load_timeline_from_json(self.paths["timeline"])
            if timeline:
                self._log(self._stage_label(7 if self._news_mode_enabled() else 6, "Reusing timeline"))
                self.stage_times["timeline"] = 0.0
                self._log("timeline completed in 0.00s")
                self._optimization_section("timeline").update(
                    {
                        "mode": "reused",
                        "timeline_signature": signature,
                    }
                )
                return timeline

        timeline = self._run_stage(
            "timeline",
            self._stage_label(7 if self._news_mode_enabled() else 6, "Building timeline"),
            lambda: self._build_timeline(plan),
        )
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
        self._optimization_section("timeline").update(
            {
                "mode": "generated",
                "timeline_signature": signature,
            }
        )
        return timeline

    def _ensure_preview_render(self, timeline: list[TimelineClip]) -> None:
        render_signature = self._render_signature(timeline)
        if self._preview_render_cache_valid(render_signature):
            preview_srt = self.paths["preview_srt"]
            if not preview_srt.exists() and self.paths["captions"].exists():
                shutil.copy2(self.paths["captions"], preview_srt)
            self._log(self._stage_label(7 if self._news_mode_enabled() else 6, "Reusing preview video"))
            self.stage_times["preview_render"] = 0.0
            self._log("preview_render completed in 0.00s")
            self._optimization_section("preview_render").update(
                {
                    "mode": "reused",
                    "render_signature": render_signature,
                }
            )
            return

        def render_preview_stage() -> None:
            self._render_video(
                timeline,
                self.paths["narration_wav"],
                self.paths["preview_mp4"],
                metrics_section="preview_render",
            )
            shutil.copy2(self.paths["captions"], self.paths["preview_srt"])

        self._run_stage(
            "preview_render",
            self._stage_label(7 if self._news_mode_enabled() else 6, "Rendering preview video"),
            render_preview_stage,
        )
        self._write_json(
            self.paths["preview_render_state"],
            {
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "render_signature": render_signature,
                "preview_mp4": str(self.paths["preview_mp4"].resolve()),
                "preview_srt": str(self.paths["preview_srt"].resolve()),
            },
        )
        self._optimization_section("preview_render").update(
            {
                "mode": "generated",
                "render_signature": render_signature,
            }
        )

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

        self._log(self._stage_label(7 if self._news_mode_enabled() else 6, "Reusing approved preview as final output"))
        self._optimization_section("render").update(
            {
                "mode": "reused-preview",
                "reused_preview": True,
                "render_signature": render_signature,
            }
        )
        return True

    def _captions_signature(self) -> str:
        payload = {
            "narration_sha256": self._safe_file_sha256(self.paths["narration_wav"]),
            "caption_engine": self.config.caption_engine,
            "caption_style": self.config.caption_style,
            "width": self.config.width,
            "height": self.config.height,
            "caption_words_min": self.config.caption_words_min,
            "caption_words_max": self.config.caption_words_max,
            "caption_max_chars": self.config.caption_max_chars,
            "caption_min_seconds": self.config.caption_min_seconds,
            "caption_max_seconds": self.config.caption_max_seconds,
            "caption_font_scale": self.config.caption_font_scale,
            "caption_bottom_ratio": self.config.caption_bottom_ratio,
            "subtitle_preset": normalize_subtitle_preset(self.config.subtitle_preset, "regular"),
            "subtitle_position": normalize_subtitle_position(self.config.subtitle_position, "bottom"),
            "subtitle_accent_color": normalize_subtitle_accent_color(
                self.config.subtitle_accent_color,
                "sunflower",
            ),
            "subtitle_box_color": normalize_subtitle_box_color(
                self.config.subtitle_box_color,
                normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower"),
            ),
            "subtitle_bold": bool(self.config.subtitle_bold),
            "subtitle_outline": bool(self.config.subtitle_outline),
            "intro_seconds": self._intro_bookend_seconds(),
        }
        return self._stable_payload_hash(payload)

    def _timeline_signature(self, plan: ScriptPlan) -> str:
        shot_plan = self._shot_plan or self._load_shot_plan()
        scene_assets: list[dict[str, Any]] = []
        for scene in plan.scenes:
            asset_path = str(scene.asset_path or "").strip()
            source = Path(asset_path).expanduser().resolve() if asset_path else None
            if source is None or not source.exists():
                scene_assets.append({"scene_id": scene.scene_id, "asset_path": asset_path, "exists": False})
                continue
            stat = source.stat()
            scene_assets.append(
                {
                    "scene_id": scene.scene_id,
                    "asset_path": str(source),
                    "exists": True,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
        payload = {
            "plan": plan.to_dict(),
            "shot_plan": shot_plan.to_dict() if shot_plan is not None else None,
            "scene_assets": scene_assets,
            "include_intro": self.config.include_intro,
            "include_outro": self.config.include_outro,
            "intro_seconds": self.config.intro_seconds,
            "outro_seconds": self.config.outro_seconds,
            "outro_text": self.config.outro_text,
            "outro_spoken_text": self.config.outro_spoken_text,
        }
        return self._stable_payload_hash(payload)

    def _render_signature(self, timeline: list[TimelineClip]) -> str:
        payload = {
            "clips": [clip.to_dict() for clip in timeline],
            "narration_sha256": self._safe_file_sha256(self.paths["narration_wav"]),
            "captions_ass_sha256": self._safe_file_sha256(self.paths["captions_ass"]) if self.config.burn_subtitles else "",
            "clip_catalog_sha256": self._safe_file_sha256(self.paths["clip_catalog"]),
            "render": {
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "video_effects": self.config.video_effects,
                "burn_subtitles": self.config.burn_subtitles,
                "subtitle_preset": normalize_subtitle_preset(self.config.subtitle_preset, "regular"),
                "subtitle_position": normalize_subtitle_position(self.config.subtitle_position, "bottom"),
                "subtitle_accent_color": normalize_subtitle_accent_color(
                    self.config.subtitle_accent_color,
                    "sunflower",
                ),
                "subtitle_box_color": normalize_subtitle_box_color(
                    self.config.subtitle_box_color,
                    normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower"),
                ),
                "subtitle_bold": bool(self.config.subtitle_bold),
                "subtitle_outline": bool(self.config.subtitle_outline),
                "include_intro": self.config.include_intro,
                "include_outro": self.config.include_outro,
                "intro_seconds": self.config.intro_seconds,
                "outro_seconds": self.config.outro_seconds,
                "outro_text": self.config.outro_text,
                "outro_spoken_text": self.config.outro_spoken_text,
                "channel_profile": self._channel_profile_key(),
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
            narration_start_raw = item.get("narration_start")
            narration_end_raw = item.get("narration_end")
            try:
                narration_start = float(narration_start_raw) if narration_start_raw is not None else None
            except (TypeError, ValueError):
                narration_start = None
            try:
                narration_end = float(narration_end_raw) if narration_end_raw is not None else None
            except (TypeError, ValueError):
                narration_end = None
            source_raw = item.get("source_path")
            source_path = str(source_raw).strip() if source_raw is not None else None
            visual_strategy = normalize_news_visual_strategy(item.get("visual_strategy"), "stock")
            editorial_source_id = self._coerce_str_or_none(item.get("editorial_source_id"))
            shot_id = self._coerce_str_or_none(item.get("shot_id"))
            parent_scene_id = self._coerce_str_or_none(item.get("parent_scene_id"))
            match_confidence = self._coerce_str_or_none(item.get("match_confidence"))
            fallback_level = self._coerce_str_or_none(item.get("fallback_level"))
            clips.append(
                TimelineClip(
                    scene_id=scene_id,
                    clip_name=clip_name,
                    start=start,
                    end=end,
                    seconds=seconds,
                    source_path=source_path or None,
                    heading=heading,
                    narration_start=narration_start,
                    narration_end=narration_end,
                    visual_strategy=visual_strategy,
                    editorial_source_id=editorial_source_id,
                    shot_id=shot_id,
                    parent_scene_id=parent_scene_id,
                    match_confidence=match_confidence,
                    fallback_level=fallback_level,
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

    def _asset_query_cache_root(self) -> Path:
        return (Path.home() / ".imagine" / "asset_query_cache").resolve()

    def _asset_query_cache_path(self, provider_name: str, query: str) -> Path:
        provider_key = str(provider_name or "").strip().lower() or "unknown"
        normalized_query = str(query or "").strip().lower()
        digest = hashlib.sha1(normalized_query.encode("utf-8")).hexdigest()
        return self._asset_query_cache_root() / provider_key / f"{digest}.json"

    def _query_cache_entry_fresh(self, fetched_at: str, ttl_seconds: int) -> bool:
        if not fetched_at:
            return False
        try:
            timestamp = dt.datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
        age_seconds = (dt.datetime.now(dt.timezone.utc) - timestamp.astimezone(dt.timezone.utc)).total_seconds()
        return age_seconds <= max(0, int(ttl_seconds))

    def _load_persistent_query_cache(self, provider_name: str, query: str) -> list[AssetCandidate] | None:
        payload = self._load_json_state(self._asset_query_cache_path(provider_name, query))
        if not isinstance(payload, dict):
            return None

        fetched_at = str(payload.get("fetched_at") or "").strip()
        ttl_seconds = self._coerce_optional_int(payload.get("ttl_seconds")) or ASSET_QUERY_CACHE_TTL_SECONDS
        if not self._query_cache_entry_fresh(fetched_at, ttl_seconds):
            return None

        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            return None

        candidates: list[AssetCandidate] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            candidate = self._asset_candidate_from_payload(item)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _write_persistent_query_cache(
        self,
        provider_name: str,
        query: str,
        candidates: list[AssetCandidate],
    ) -> None:
        if not candidates:
            return

        path = self._asset_query_cache_path(provider_name, query)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": str(provider_name or "").strip().lower(),
            "query": str(query or "").strip(),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ttl_seconds": ASSET_QUERY_CACHE_TTL_SECONDS,
            "candidate_count": len(candidates),
            "candidates": [candidate.to_dict() for candidate in candidates],
        }
        self._write_json(path, payload)

    def _new_http_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(dict(self.http.headers))
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _asset_cache_lock(self, output: Path) -> threading.Lock:
        cache_key = str(output.resolve())
        with self._asset_cache_registry_lock:
            existing = self._asset_cache_locks.get(cache_key)
            if existing is not None:
                return existing
            lock = threading.Lock()
            self._asset_cache_locks[cache_key] = lock
            return lock

    def _http_get_with_retries(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: tuple[int, int] = (5, 15),
        session: requests.Session | None = None,
    ) -> requests.Response:
        delays = (0.0, 1.0, 2.0, 4.0)
        last_error: Exception | None = None
        active_session = session or self.http

        for attempt_index, delay in enumerate(delays):
            if attempt_index > 0:
                self._increment_optimization_counter("asset_resolution", "provider_retry_attempts")
                time.sleep(delay)
            try:
                response = active_session.get(url, headers=headers, params=params, timeout=timeout)
            except requests.exceptions.Timeout as exc:
                last_error = exc
                if attempt_index == len(delays) - 1:
                    break
                continue
            except requests.exceptions.RequestException as exc:
                raise RuntimeError(f"HTTP GET failed for {url}: {exc}") from exc

            if response.status_code >= 500:
                response.close()
                if attempt_index == len(delays) - 1:
                    raise RuntimeError(f"HTTP GET failed for {url}, status={response.status_code}")
                continue
            return response

        if last_error is not None:
            raise RuntimeError(f"HTTP GET timed out for {url}") from last_error
        raise RuntimeError(f"HTTP GET failed for {url}")

    def _selected_candidate_for_shot(self, shot_id: str) -> AssetCandidate | None:
        clip_catalog = self._load_json_state(self.paths["clip_catalog"]) or {}
        clips = clip_catalog.get("clips") if isinstance(clip_catalog, dict) else None
        if not isinstance(clips, list):
            return None
        for item in clips:
            if not isinstance(item, dict):
                continue
            if str(item.get("shot_id") or "").strip() != shot_id:
                continue
            candidates = item.get("candidates")
            if not isinstance(candidates, list):
                return None
            for raw_candidate in candidates:
                if not isinstance(raw_candidate, dict) or not bool(raw_candidate.get("selected")):
                    continue
                return self._asset_candidate_from_payload(raw_candidate)
            return None
        return None

    def _shot_candidate_preview_path(self, shot_id: str, shortlist_index: int, candidate: AssetCandidate) -> Path:
        source_url = candidate.preview_url or candidate.download_url or candidate.source_url
        parsed = urlparse(str(source_url or ""))
        ext = str(Path(parsed.path).suffix or "").strip().lower()
        if ext not in {".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png"}:
            ext = ".jpg" if candidate.media_type == "image" else ".mp4"
        return self._shot_candidate_preview_dir(shot_id) / f"{int(shortlist_index):02d}-{candidate.media_type}{ext}"

    def _materialize_preview_file(self, source_path: Path, destination_path: Path) -> Path:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            existing_size = destination_path.stat().st_size
            source_size = source_path.stat().st_size
            if existing_size == source_size and existing_size > 0:
                return destination_path
        shutil.copy2(source_path, destination_path)
        return destination_path

    def _prefetch_shot_candidate_preview(
        self,
        shot_id: str,
        shortlist_index: int,
        candidate: AssetCandidate,
    ) -> tuple[str | None, bool, str | None]:
        preview_source: Path | None = None
        note: str | None = None
        try:
            if candidate.source_platform == "vecteezy":
                if candidate.preview_url:
                    preview_source = self._download_asset(
                        candidate.preview_url,
                        cache_key=f"preview::{self._candidate_cache_key(candidate) or shortlist_index}",
                    )
                else:
                    note = "Preview unavailable for this Vecteezy candidate."
            elif candidate.preview_url:
                preview_source = self._download_asset(
                    candidate.preview_url,
                    cache_key=f"preview::{self._candidate_cache_key(candidate) or shortlist_index}",
                )
            elif candidate.media_type == "image":
                preview_source, _ = self._download_candidate_asset(candidate)
            elif candidate.source_platform != "vecteezy":
                preview_source, _ = self._download_candidate_asset(candidate)
            else:
                note = "Preview unavailable for this candidate."
        except Exception as exc:
            preview_source = None
            note = f"Preview unavailable: {exc}"

        if preview_source is None:
            return None, False, note

        preview_path = self._shot_candidate_preview_path(shot_id, shortlist_index, candidate)
        materialized_path = self._materialize_preview_file(preview_source, preview_path)
        return str(materialized_path.resolve()), True, note

    def _asset_candidate_from_payload(self, payload: dict[str, Any]) -> AssetCandidate | None:
        source_platform = str(payload.get("source_platform") or "").strip()
        media_type = str(payload.get("media_type") or "").strip().lower()
        download_url = str(payload.get("download_url") or "").strip()
        source_url = str(payload.get("source_url") or "").strip()
        if not (source_platform and media_type and download_url and source_url):
            return None

        return AssetCandidate(
            source_platform=source_platform,
            source_asset_id=(str(payload.get("source_asset_id") or "").strip() or None),
            media_type=media_type,
            download_url=download_url,
            source_url=source_url,
            preview_url=(str(payload.get("preview_url") or "").strip() or None),
            creator_name=(str(payload.get("creator_name") or "").strip() or None),
            creator_profile_url=(str(payload.get("creator_profile_url") or "").strip() or None),
            license_name=(str(payload.get("license_name") or "").strip() or None),
            license_url=(str(payload.get("license_url") or "").strip() or None),
            description=(str(payload.get("description") or "").strip() or None),
            width=self._coerce_optional_int(payload.get("width")),
            height=self._coerce_optional_int(payload.get("height")),
            duration_seconds=self._coerce_optional_float(payload.get("duration_seconds")),
            download_extension=(str(payload.get("download_extension") or "").strip() or None),
            query=(str(payload.get("query") or "").strip() or None),
            quality_score=self._coerce_optional_float(payload.get("quality_score")) or 0.0,
            ranking_score=self._coerce_optional_float(payload.get("ranking_score")) or 0.0,
            restriction_flags=[
                str(flag).strip()
                for flag in payload.get("restriction_flags") or []
                if str(flag).strip()
            ]
            if isinstance(payload.get("restriction_flags"), list)
            else [],
            attribution_required=bool(payload.get("attribution_required")),
            attribution_text=(str(payload.get("attribution_text") or "").strip() or None),
        )

    def _load_asset_selection_overrides(self) -> dict[str, AssetCandidate]:
        payload = self._load_json_state(self.paths["asset_selection_overrides"])
        if not isinstance(payload, dict):
            return {}

        scenes = payload.get("scenes")
        if not isinstance(scenes, dict):
            return {}

        overrides: dict[str, AssetCandidate] = {}
        for raw_scene_id, raw_candidate in scenes.items():
            scene_id = str(raw_scene_id).strip()
            if not scene_id or not isinstance(raw_candidate, dict):
                continue
            candidate = self._asset_candidate_from_payload(raw_candidate)
            if candidate is not None:
                overrides[scene_id] = candidate
        return overrides

    def _save_asset_selection_overrides(self, overrides: dict[str, AssetCandidate]) -> None:
        path = self.paths["asset_selection_overrides"]
        if not overrides:
            path.unlink(missing_ok=True)
            return

        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scenes": {
                scene_id: candidate.to_dict()
                for scene_id, candidate in sorted(overrides.items())
            },
        }
        self._write_json(path, payload)

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
            selection_overrides = self._load_asset_selection_overrides()
            preferred_candidates = {
                scene_id: candidate
                for scene_id, candidate in selection_overrides.items()
                if scene_id in target_scene_ids
            }
            remaining_overrides = {
                scene_id: candidate
                for scene_id, candidate in selection_overrides.items()
                if scene_id not in target_scene_ids
            }
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
            if preferred_candidates:
                self._log(
                    "Applying stored candidate overrides for scenes: "
                    + ", ".join(sorted(preferred_candidates.keys()))
                )

            replacement_rights = self._run_stage(
                "assets",
                "Stage 2/5: Resolving replacement assets",
                lambda: self._resolve_assets(
                    plan,
                    scenes=target_scenes,
                    preused_asset_keys=used_asset_keys,
                    preferred_candidates=preferred_candidates,
                ),
            )

            rights_by_scene: dict[str, AssetRight] = {right.scene_id: right for right in keep_rights}
            for right in replacement_rights:
                rights_by_scene[right.scene_id] = right
            merged_rights = [rights_by_scene[scene.scene_id] for scene in plan.scenes if scene.scene_id in rights_by_scene]

            self._write_json(self.paths["script"], plan.to_dict())
            self._prepare_bookend_backgrounds(plan)
            self._write_clip_catalog(plan, merged_rights)
            self._save_asset_selection_overrides(remaining_overrides)

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
                self._render_video(
                    timeline,
                    narration_wav,
                    self.paths["final_mp4"],
                    metrics_section="render",
                )
                shutil.copy2(captions_srt, self.paths["final_srt"])

            self._run_stage("render", "Stage 4/5: Rendering updated video", render_stage)
            self._run_stage(
                "manifest",
                "Stage 5/5: Writing rights manifest",
                lambda: self._write_manifest_and_publish_artifacts(plan, merged_rights),
            )

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
                "youtube_credits": str(self.paths["youtube_credits"].resolve()),
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

    def prepare_shot_candidates(
        self,
        shot_id: str,
        *,
        key_info: str | None = None,
        search_queries: list[str] | tuple[str, ...] | None = None,
        strict_query_override: bool = False,
    ) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            shot_plan = self._load_shot_plan()
            if shot_plan is None or not shot_plan.shots:
                raise RuntimeError("Shot plan is missing. Run shot-plan before preparing shot candidates.")

            target_shot: PlannedShot | None = None
            for item in shot_plan.shots:
                if item.shot_id == shot_id:
                    target_shot = replace(item)
                    break
            if target_shot is None:
                raise RuntimeError(f"Shot not found in shot plan: {shot_id}")

            target_shot = self._apply_key_info_to_shot(
                target_shot,
                key_info,
                search_queries=search_queries,
                strict_query_override=strict_query_override,
            )
            provider_order = self._enabled_provider_order()
            query_cache: dict[tuple[str, str], list[AssetCandidate]] = {}
            scene = self._shot_as_scene(target_shot)
            editorial_locked = normalize_news_visual_strategy(target_shot.visual_strategy, "stock") in {
                "news-source-screenshot",
                "source-card",
            }
            review_state = self._load_shot_review_state()
            cycle = self._normalize_shot_regenerate_cycle((review_state.get(shot_id) or {}).get("regenerate_cycle"))
            if strict_query_override:
                cycle["strict_query_override"] = True

            shortlist_size = max(1, int(self.config.asset_shortlist_size))
            ranked_candidates: list[AssetCandidate] = []
            video_candidates: list[AssetCandidate] = []
            image_candidates: list[AssetCandidate] = []
            candidates: list[AssetCandidate] = []
            existing_rights = self._load_existing_rights()
            existing_rights_by_scene = {right.scene_id: right for right in existing_rights}
            selected_candidate = self._selected_candidate_for_shot(shot_id)
            selected_key = self._asset_uniqueness_key(selected_candidate) if selected_candidate is not None else None
            current_asset_key = ""
            if not editorial_locked:
                persisted_asset_keys = self._persisted_shot_asset_keys()
                active_asset_keys = self._active_shot_asset_keys()
                current_asset_key = active_asset_keys.get(shot_id)
                if not current_asset_key:
                    current_right = existing_rights_by_scene.get(shot_id)
                    if current_right is not None:
                        current_asset_key = self._asset_uniqueness_key_from_right(current_right)
                if not current_asset_key:
                    shot_keys = persisted_asset_keys.get(shot_id) or set()
                    current_asset_key = next(iter(sorted(shot_keys)), "")
                if not current_asset_key and selected_key:
                    current_asset_key = selected_key
                other_active_asset_keys = {
                    key
                    for active_shot_id, key in active_asset_keys.items()
                    if active_shot_id != shot_id and key
                }
                for other_shot_id, keys in persisted_asset_keys.items():
                    if other_shot_id == shot_id:
                        continue
                    other_active_asset_keys.update(str(key).strip().lower() for key in keys if str(key).strip())
                other_active_asset_keys.update(
                    self._asset_uniqueness_key_from_right(right)
                    for right in existing_rights
                    if right.scene_id != shot_id
                )
                ranked_candidates = self._rank_scene_candidates(
                    scene,
                    provider_order=provider_order,
                    query_cache=query_cache,
                    ignore_global_keywords=bool(cycle.get("strict_query_override")),
                    deprioritized_asset_keys=set(cycle.get("rejected_asset_keys") or []),
                )
                filtered_candidates: list[AssetCandidate] = []
                for candidate in ranked_candidates:
                    unique_key = self._asset_uniqueness_key(candidate)
                    if current_asset_key and unique_key == current_asset_key:
                        continue
                    if unique_key in other_active_asset_keys:
                        continue
                    filtered_candidates.append(candidate)
                video_candidates = [candidate for candidate in filtered_candidates if candidate.media_type == "video"][:shortlist_size]
                image_candidates = [candidate for candidate in filtered_candidates if candidate.media_type == "image"][:shortlist_size]
                candidates = self._sort_candidates([*video_candidates, *image_candidates])

            preview_budget = {"video": 3, "image": 3}
            manifest_candidates: list[dict[str, Any]] = []
            payload_by_key: dict[str, dict[str, Any]] = {}
            for shortlist_index, candidate in enumerate(candidates):
                preview_path: str | None = None
                preview_available = False
                preview_note: str | None = None
                media_type = str(candidate.media_type or "").strip().lower()
                if preview_budget.get(media_type, 0) > 0:
                    preview_path, preview_available, preview_note = self._prefetch_shot_candidate_preview(
                        shot_id,
                        shortlist_index,
                        candidate,
                    )
                    preview_budget[media_type] = max(0, int(preview_budget.get(media_type, 0)) - 1)
                else:
                    preview_note = "Preview not prefetched for this candidate."

                payload = candidate.to_dict()
                payload["shortlist_index"] = int(shortlist_index)
                payload["preview_local_path"] = preview_path
                payload["preview_available"] = bool(preview_available)
                payload["preview_note"] = preview_note
                payload["selected"] = bool(selected_key and self._asset_uniqueness_key(candidate) == selected_key)
                manifest_candidates.append(payload)
                payload_by_key[self._asset_uniqueness_key(candidate)] = payload

            manifest_video_candidates = [
                dict(payload_by_key[self._asset_uniqueness_key(candidate)])
                for candidate in video_candidates
                if self._asset_uniqueness_key(candidate) in payload_by_key
            ]
            manifest_image_candidates = [
                dict(payload_by_key[self._asset_uniqueness_key(candidate)])
                for candidate in image_candidates
                if self._asset_uniqueness_key(candidate) in payload_by_key
            ]

            manifest_payload = {
                "schema_version": 1,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "shot_id": shot_id,
                "scene_id": target_shot.scene_id,
                "heading": target_shot.heading,
                "key_info": target_shot.key_info,
                "channel_vocabulary_key": self._channel_profile_key(),
                "matched_channel_terms": list(target_shot.matched_channel_terms),
                "search_queries": list(target_shot.search_queries),
                "effective_search_queries": list(target_shot.effective_search_queries),
                "strict_query_override": bool(cycle.get("strict_query_override")),
                "editorial_locked": bool(editorial_locked),
                "visual_strategy": normalize_news_visual_strategy(target_shot.visual_strategy, "stock"),
                "current_asset_key": current_asset_key,
                "current_asset_path": target_shot.asset_path,
                "current_asset_provider": target_shot.asset_provider,
                "candidates": manifest_candidates,
                "video_candidates": manifest_video_candidates,
                "image_candidates": manifest_image_candidates,
            }
            manifest_path = self._shot_candidate_manifest_path(shot_id)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json(manifest_path, manifest_payload)

            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "shot_candidate_manifest": str(manifest_path.resolve()),
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

    def regenerate_shot(
        self,
        shot_id: str,
        *,
        key_info: str | None = None,
        search_queries: list[str] | tuple[str, ...] | None = None,
        candidate_index: int | None = None,
        strict_query_override: bool = False,
    ) -> dict[str, str]:
        self._reset_run_state()
        self._started_at = dt.datetime.now(dt.timezone.utc)
        self._prepare_dirs()
        self._log("Preparing project directories")

        outputs: dict[str, str] = {}
        try:
            self._check_dependencies()
            shot_plan = self._load_shot_plan()
            if shot_plan is None or not shot_plan.shots:
                raise RuntimeError("Shot plan is missing. Run shot-plan before regenerating a shot.")

            target_shot: PlannedShot | None = None
            target_index = -1
            for index, item in enumerate(shot_plan.shots):
                if item.shot_id == shot_id:
                    target_shot = replace(item)
                    target_index = index
                    break
            if target_shot is None:
                raise RuntimeError(f"Shot not found in shot plan: {shot_id}")

            original_shot = replace(target_shot)
            target_shot = self._apply_key_info_to_shot(
                target_shot,
                key_info,
                search_queries=search_queries,
                strict_query_override=strict_query_override,
            )

            preferred_candidates: dict[str, AssetCandidate] | None = None
            if candidate_index is not None:
                selected_payload: dict[str, Any] | None = None
                manifest_payload = self._load_json_state(self._shot_candidate_manifest_path(shot_id)) or {}
                manifest_candidates = manifest_payload.get("candidates") if isinstance(manifest_payload, dict) else None
                if isinstance(manifest_candidates, list):
                    for item in manifest_candidates:
                        if not isinstance(item, dict):
                            continue
                        if int(item.get("shortlist_index") or -1) == int(candidate_index):
                            selected_payload = item
                            break
                if selected_payload is None:
                    clip_catalog = self._load_json_state(self.paths["clip_catalog"]) or {}
                    clips = clip_catalog.get("clips") if isinstance(clip_catalog, dict) else None
                    if isinstance(clips, list):
                        for item in clips:
                            if not isinstance(item, dict):
                                continue
                            if str(item.get("shot_id") or "").strip() != shot_id:
                                continue
                            candidates = item.get("candidates")
                            if isinstance(candidates, list) and 0 <= int(candidate_index) < len(candidates):
                                candidate_payload = candidates[int(candidate_index)]
                                if isinstance(candidate_payload, dict):
                                    selected_payload = candidate_payload
                            break
                selected_candidate = self._asset_candidate_from_payload(selected_payload) if selected_payload is not None else None
                if selected_candidate is not None:
                    preferred_candidates = {shot_id: selected_candidate}

            single_plan = ShotPlan(title=shot_plan.title, summary=shot_plan.summary, shots=[target_shot])
            existing_rights = self._load_existing_rights()
            existing_rights_by_scene = {right.scene_id: right for right in existing_rights}
            preused_asset_keys = {
                self._asset_uniqueness_key_from_right(right)
                for right in existing_rights
                if right.scene_id != shot_id
            }
            rights = self._resolve_shot_assets(
                single_plan,
                preferred_candidates=preferred_candidates,
                persist_state=False,
                preused_asset_keys=preused_asset_keys,
                strict_query_override_scene_ids={shot_id} if strict_query_override else None,
            )
            updated_shot = single_plan.shots[0]
            if (
                not self._news_mode_enabled()
                and normalize_news_visual_strategy(updated_shot.visual_strategy, "stock") == "stock"
                and updated_shot.asset_provider == "internal-shot-card"
                and original_shot.asset_path
            ):
                updated_shot.asset_path = original_shot.asset_path
                updated_shot.asset_provider = original_shot.asset_provider
                updated_shot.fallback_level = original_shot.fallback_level
                previous_right = existing_rights_by_scene.get(shot_id)
                rights = [previous_right] if previous_right is not None else []
                self._warn(
                    f"No better stock/image asset resolved for {shot_id}. Keeping the current shot asset instead of switching to an internal card."
                )
            self._ensure_shot_previews(single_plan)
            shot_plan.shots[target_index] = updated_shot
            self._shot_plan = shot_plan

            merged_rights: list[AssetRight] = [right for right in existing_rights if right.scene_id != shot_id]
            merged_rights.extend(rights)

            self._write_json(self.paths["shot_plan"], shot_plan.to_dict())
            prior_state = self._load_json_state(self.paths["shot_review_state"])
            review_payload = self._build_shot_review_state(shot_plan, prior_state)
            raw_shots = review_payload.get("shots") if isinstance(review_payload, dict) else None
            if isinstance(raw_shots, dict) and shot_id in raw_shots and isinstance(raw_shots[shot_id], dict):
                raw_shots[shot_id]["approved"] = False if bool(raw_shots[shot_id].get("blocked")) else True
                raw_shots[shot_id]["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                regenerate_cycle = self._normalize_shot_regenerate_cycle(raw_shots[shot_id].get("regenerate_cycle"))
                regenerate_cycle["regenerated"] = True
                if search_queries:
                    regenerate_cycle["search_queries"] = self._normalize_search_queries(search_queries)
                if strict_query_override:
                    regenerate_cycle["strict_query_override"] = True
                raw_shots[shot_id]["regenerate_cycle"] = regenerate_cycle
            self._write_json(self.paths["shot_review_state"], review_payload)
            self._write_shot_clip_catalog(shot_plan, merged_rights)

            plan = self._load_preferred_script_plan()
            self._prepare_bookend_backgrounds(self._shot_plan_as_script_plan(shot_plan))
            self._write_manifest_and_publish_artifacts(plan, merged_rights)
            self._ensure_timeline(plan)
            outputs = {
                "project_dir": str(self.config.project_dir.resolve()),
                "shot_plan": str(self.paths["shot_plan"].resolve()),
                "shot_review_state": str(self.paths["shot_review_state"].resolve()),
                "clip_catalog": str(self.paths["clip_catalog"].resolve()),
                "timeline": str(self.paths["timeline"].resolve()),
                "shot_preview": str(self._shot_preview_path(shot_id).resolve()),
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
            "publish": project_dir / "publish",
            "source_screenshots": project_dir / "review" / "source_screenshots",
            "prompt": project_dir / "prompt.txt",
            "script": project_dir / "script.json",
            "narration_txt": project_dir / "narration.txt",
            "narration_raw": project_dir / "narration.raw.wav",
            "narration_wav": project_dir / "narration.wav",
            "outro_narration_raw": project_dir / "tmp" / "outro_narration.raw.wav",
            "outro_narration_wav": project_dir / "tmp" / "outro_narration.wav",
            "captions": project_dir / "captions.srt",
            "captions_ass": project_dir / "captions.ass",
            "timeline": project_dir / "timeline.json",
            "clip_catalog": project_dir / "review" / "clip_catalog.json",
            "news_source_candidates": project_dir / "review" / "news_source_candidates.json",
            "news_review_state": project_dir / "review" / "news_review_state.json",
            "news_brief": project_dir / "review" / "news_brief.json",
            "approved_script": project_dir / "review" / "script_approved.json",
            "scene_review_state": project_dir / "review" / "scene_review_state.json",
            "shot_plan": project_dir / "review" / "shot_plan.json",
            "shot_review_state": project_dir / "review" / "shot_review_state.json",
            "shots": project_dir / "review" / "shots",
            "asset_selection_overrides": project_dir / "review" / "asset_selection_overrides.json",
            "narration_state": project_dir / "review" / "narration_state.json",
            "captions_state": project_dir / "review" / "captions_state.json",
            "timeline_state": project_dir / "review" / "timeline_state.json",
            "preview_render_state": project_dir / "review" / "preview_render_state.json",
            "preview_mp4": project_dir / "review" / "preview.mp4",
            "preview_srt": project_dir / "review" / "preview.srt",
            "scene_narration_dir": project_dir / "review" / "narration" / "scenes",
            "youtube_credits": project_dir / "publish" / "youtube_description_credits.txt",
            "manifest": project_dir / "rights_manifest.json",
            "run_log": project_dir / "run.log",
            "run_report": project_dir / "run_report.json",
            "final_mp4": project_dir / "output" / "final.mp4",
            "final_srt": project_dir / "output" / "final.srt",
        }

    def _prepare_dirs(self) -> None:
        for key in ("root", "assets_cache", "review", "tmp", "output", "publish", "source_screenshots", "shots"):
            self.paths[key].mkdir(parents=True, exist_ok=True)

    def _check_dependencies(self) -> None:
        self._require_binary("ffmpeg")
        self._require_binary("ffprobe")
        self._validate_render_filter_requirements()
        self._check_news_dependencies()
        self._check_script_dependencies()
        if self.config.tts_engine == "piper":
            command = self._resolve_piper_command()
            if command is None:
                raise RuntimeError(
                    "Piper runtime not found. Install with: python -m pip install piper-tts"
                )
        if self.config.tts_engine == "kokoro":
            try:
                from kokoro import KPipeline  # type: ignore  # noqa: F401
            except Exception as exc:
                raise RuntimeError(
                    "Kokoro TTS not available. Install voice deps with: python -m pip install -e '.[voice]'"
                ) from exc

    def _check_script_dependencies(self) -> None:
        if self.config.script_engine == "ollama":
            self._require_binary("ollama")
            self._ollama_ready = self._ollama_server_ready()
            if not self._ollama_ready:
                raise RuntimeError(
                    "Ollama is unavailable. Start it with 'ollama serve' or switch to --script-engine template."
                )

    def _content_mode(self) -> str:
        return normalize_content_mode(self.config.content_mode, "explainer")

    def _news_mode_enabled(self) -> bool:
        return self._content_mode() == "news"

    def _news_jurisdiction(self) -> str:
        value = str(self.config.news_jurisdiction or "us").strip().lower()
        if value not in NEWS_JURISDICTION_CHOICES:
            return "us"
        return value

    def _stage_label(self, stage_number: float, text: str) -> str:
        total = 8 if self._news_mode_enabled() else 7
        return f"Stage {stage_number:g}/{total}: {text}"

    def _news_feed_urls(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw_value in self.config.news_feed_urls:
            value = str(raw_value).strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(value)
        return out

    def _check_news_dependencies(self) -> None:
        if not self._news_mode_enabled():
            return

        if self._news_jurisdiction() != "us":
            raise RuntimeError("News mode currently supports only --news-jurisdiction us.")

        try:
            import feedparser  # type: ignore  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "feedparser is required for news mode. Install deps with: python -m pip install -e ."
            ) from exc

        try:
            import trafilatura  # type: ignore  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "trafilatura is required for news mode. Install deps with: python -m pip install -e ."
            ) from exc

    def _news_candidate_state_payload(self) -> dict[str, Any]:
        return self._load_json_state(self.paths["news_review_state"]) or {}

    def _load_news_review_state(self) -> dict[str, dict[str, Any]]:
        payload = self._news_candidate_state_payload()
        raw_sources = payload.get("sources") if isinstance(payload, dict) else None
        out: dict[str, dict[str, Any]] = {}
        if isinstance(raw_sources, dict):
            for source_id, value in raw_sources.items():
                key = str(source_id).strip()
                if key and isinstance(value, dict):
                    out[key] = dict(value)
        return out

    def _save_news_review_state(self, state: dict[str, dict[str, Any]]) -> None:
        payload = {
            "schema_version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "content_mode": self._content_mode(),
            "sources": state,
        }
        self._write_json(self.paths["news_review_state"], payload)

    def _load_news_source_candidates(self) -> list[NewsSourceCandidate]:
        payload = self._load_json_state(self.paths["news_source_candidates"]) or {}
        raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
        out: list[NewsSourceCandidate] = []
        if not isinstance(raw_candidates, list):
            return out

        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "").strip()
            article_url = str(item.get("article_url") or "").strip()
            canonical_url = str(item.get("canonical_url") or article_url).strip()
            if not source_id or not article_url:
                continue
            out.append(
                NewsSourceCandidate(
                    source_id=source_id,
                    feed_url=str(item.get("feed_url") or "").strip(),
                    article_url=article_url,
                    canonical_url=canonical_url,
                    domain=str(item.get("domain") or "").strip(),
                    title=str(item.get("title") or "").strip() or article_url,
                    publisher=str(item.get("publisher") or "").strip() or str(item.get("domain") or "").strip(),
                    summary=str(item.get("summary") or "").strip(),
                    dek=self._coerce_str_or_none(item.get("dek")),
                    byline=self._coerce_str_or_none(item.get("byline")),
                    published_at=self._coerce_str_or_none(item.get("published_at")),
                    extracted_text=str(item.get("extracted_text") or "").strip(),
                    screenshot_path=self._coerce_str_or_none(item.get("screenshot_path")),
                    source_card_path=self._coerce_str_or_none(item.get("source_card_path")),
                    screenshot_available=bool(item.get("screenshot_available")),
                    screenshot_reason=self._coerce_str_or_none(item.get("screenshot_reason")),
                    content_sha256=self._coerce_str_or_none(item.get("content_sha256")),
                )
            )
        return out

    def _canonicalize_news_url(self, raw_url: str) -> str:
        parsed = urlparse(str(raw_url).strip())
        if not parsed.scheme or not parsed.netloc:
            return str(raw_url).strip()
        clean_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if not str(key).lower().startswith("utm_")
            and str(key).lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        ]
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            query=urlencode(clean_query),
            fragment="",
        )
        return urlunparse(normalized)

    def _news_domain_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def _news_entry_datetime(self, entry: Any) -> dt.datetime | None:
        for key in ("published_parsed", "updated_parsed"):
            value = getattr(entry, key, None)
            if value:
                try:
                    return dt.datetime(*value[:6], tzinfo=dt.timezone.utc)
                except Exception:
                    pass
        for key in ("published", "updated"):
            value = getattr(entry, key, None)
            if not value:
                continue
            try:
                parsed = parsedate_to_datetime(str(value))
            except Exception:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        return None

    def _news_source_id(self, canonical_url: str, title: str) -> str:
        digest = hashlib.sha1(f"{canonical_url}|{title.strip().lower()}".encode("utf-8")).hexdigest()
        return f"src-{digest[:12]}"

    def _article_text_payload(self, article_url: str) -> tuple[str, str]:
        import trafilatura  # type: ignore

        downloaded = trafilatura.fetch_url(article_url)
        if not downloaded:
            return "", ""
        extracted = trafilatura.extract(
            downloaded,
            include_links=False,
            include_images=False,
            favor_precision=True,
        )
        text_value = re.sub(r"\s+", " ", str(extracted or "")).strip()
        return str(downloaded), text_value

    def _news_summary_sentences(self, text: str, *, limit: int = 2) -> str:
        sentences = [sentence.strip() for sentence in self._split_sentences(text) if sentence.strip()]
        return " ".join(sentences[: max(1, int(limit))]).strip()

    def _build_news_source_card(
        self,
        *,
        source_id: str,
        title: str,
        publisher: str,
        domain: str,
        published_at: str | None,
    ) -> str | None:
        output_path = self.paths["source_screenshots"] / f"{source_id}-card.png"
        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path.resolve())

        title_file = output_path.with_suffix(".title.txt")
        publisher_file = output_path.with_suffix(".publisher.txt")
        meta_file = output_path.with_suffix(".meta.txt")
        meta_text = "Facts-only source card"
        if published_at:
            meta_text = f"{publisher} | {published_at[:10]}"
        elif domain:
            meta_text = domain
        self._write_text(title_file, self._wrap_bookend_text(title or domain or "Editorial source") + "\n")
        self._write_text(publisher_file, (publisher or domain or "Editorial Source") + "\n")
        self._write_text(meta_file, meta_text + "\n")

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x0f172a:s={self.config.width}x{self.config.height}:r=1",
            "-frames:v",
            "1",
            "-vf",
            (
                "drawbox=x=0:y=0:w=iw:h=ih:color=0x0f172a:t=fill,"
                "drawbox=x=iw*0.08:y=ih*0.12:w=iw*0.84:h=ih*0.76:color=0x111827:t=fill,"
                "drawbox=x=iw*0.08:y=ih*0.12:w=iw*0.84:h=6:color=0xeab308:t=fill,"
                f"drawtext=textfile='{self._escape_drawtext_path(publisher_file)}':fontcolor=white:fontsize=34:x=w*0.11:y=h*0.20,"
                f"drawtext=textfile='{self._escape_drawtext_path(title_file)}':fontcolor=white:fontsize=54:x=w*0.11:y=h*0.31,"
                f"drawtext=textfile='{self._escape_drawtext_path(meta_file)}':fontcolor=0xcbd5e1:fontsize=28:x=w*0.11:y=h*0.78"
            ),
            str(output_path),
        ]
        result = self._run_command(command, timeout=180, check=False)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            self._warn(f"Could not generate source card for {source_id}; screenshot scenes may fall back to placeholders.")
            return None
        return str(output_path.resolve())

    def _capture_news_screenshot(self, source_id: str, article_url: str) -> tuple[str | None, str | None]:
        output_path = self.paths["source_screenshots"] / f"{source_id}.png"
        if output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path.resolve()), None

        try:
            from playwright.sync_api import Error as PlaywrightError  # type: ignore
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception:
            return None, "Playwright unavailable"

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": max(1280, self.config.width), "height": 1600})
                page.goto(article_url, wait_until="domcontentloaded", timeout=45000)
                page.add_style_tag(content=NEWS_SCREENSHOT_HIDE_CSS)
                page.wait_for_timeout(900)
                clip = page.evaluate(
                    """
                    () => {
                      const heading = document.querySelector("h1");
                      if (!heading) return null;
                      const rect = heading.getBoundingClientRect();
                      const body = document.body;
                      const top = Math.max(0, Math.min(rect.top - 140, 220));
                      const rawBottom = Math.max(rect.bottom + 220, rect.top + 340);
                      const bottom = Math.min(
                        Math.max(top + 260, rawBottom),
                        Math.max(top + 260, Math.min(body.scrollHeight, 720))
                      );
                      return {
                        x: 0,
                        y: top,
                        width: Math.max(960, Math.min(window.innerWidth, 1280)),
                        height: Math.max(260, Math.min(620, bottom - top)),
                      };
                    }
                    """
                )
                if not isinstance(clip, dict):
                    browser.close()
                    return None, "Headline block not found"
                page.screenshot(path=str(output_path), clip=clip)
                browser.close()
        except PlaywrightError as exc:
            return None, f"Playwright error: {exc}"
        except Exception as exc:
            return None, str(exc)

        if not output_path.exists() or output_path.stat().st_size <= 0:
            return None, "Screenshot capture produced no file"
        return str(output_path.resolve()), None

    def _prepare_news_sources(self) -> dict[str, Any]:
        if not self._news_mode_enabled():
            self.news_stats = {"content_mode": self._content_mode(), "source_stage_run": False}
            return {"candidates": 0, "approved_sources": 0}

        self._check_news_dependencies()
        feed_urls = self._news_feed_urls()
        if not feed_urls:
            raise RuntimeError("News mode requires at least one --news-feed-url.")

        import feedparser  # type: ignore

        now = dt.datetime.now(dt.timezone.utc)
        max_age = dt.timedelta(hours=max(1, int(self.config.news_max_age_hours)))
        max_candidates = max(1, int(self.config.news_max_candidates))
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        candidate_specs: list[dict[str, Any]] = []
        raw_entry_count = 0

        for feed_url in feed_urls:
            parsed = feedparser.parse(feed_url)
            raw_entries = getattr(parsed, "entries", []) or []
            raw_entry_count += len(raw_entries)
            if not raw_entries:
                self._warn(
                    f"Configured news feed returned zero entries: {feed_url}. "
                    "Use RSS/Atom feed URLs, not regular web pages."
                )
            for entry in raw_entries:
                article_url = str(getattr(entry, "link", "") or "").strip()
                title = re.sub(r"\s+", " ", str(getattr(entry, "title", "") or "")).strip()
                if not article_url or not title:
                    continue

                canonical_url = self._canonicalize_news_url(article_url)
                dedupe_key = canonical_url.lower()
                title_key = title.lower()
                if dedupe_key in seen_urls or title_key in seen_titles:
                    continue

                published_at = self._news_entry_datetime(entry)
                if published_at is not None and (now - published_at) > max_age:
                    continue

                domain = self._news_domain_from_url(canonical_url)
                summary = re.sub(r"\s+", " ", str(getattr(entry, "summary", "") or "")).strip()
                publisher = (
                    re.sub(r"\s+", " ", str(getattr(getattr(entry, "source", None), "title", "") or "")).strip()
                    or domain
                )
                source_id = self._news_source_id(canonical_url, title)
                candidate_specs.append(
                    {
                        "source_id": source_id,
                        "feed_url": feed_url,
                        "article_url": article_url,
                        "canonical_url": canonical_url,
                        "domain": domain,
                        "title": title,
                        "publisher": publisher,
                        "summary": summary,
                        "published_at": published_at,
                    }
                )
                seen_urls.add(dedupe_key)
                seen_titles.add(title_key)

        candidate_specs.sort(
            key=lambda item: (
                item["published_at"].timestamp() if isinstance(item.get("published_at"), dt.datetime) else float("-inf"),
                str(item.get("domain") or ""),
                str(item.get("title") or "").lower(),
            ),
            reverse=True,
        )
        selected_specs = candidate_specs[:max_candidates]
        candidates: list[NewsSourceCandidate] = []
        for spec in selected_specs:
            article_url = str(spec.get("article_url") or "").strip()
            title = str(spec.get("title") or article_url).strip()
            canonical_url = str(spec.get("canonical_url") or article_url).strip()
            domain = str(spec.get("domain") or "").strip()
            publisher = str(spec.get("publisher") or domain).strip() or domain
            summary = str(spec.get("summary") or "").strip()
            source_id = str(spec.get("source_id") or "").strip()
            published_at_value = spec.get("published_at")
            published_at = (
                published_at_value.isoformat()
                if isinstance(published_at_value, dt.datetime)
                else self._coerce_str_or_none(published_at_value)
            )
            _, extracted_text = self._article_text_payload(article_url)
            effective_summary = summary or self._news_summary_sentences(extracted_text, limit=2)
            screenshot_path, screenshot_reason = self._capture_news_screenshot(source_id, article_url)
            source_card_path = self._build_news_source_card(
                source_id=source_id,
                title=title,
                publisher=publisher,
                domain=domain,
                published_at=published_at,
            )
            candidates.append(
                NewsSourceCandidate(
                    source_id=source_id,
                    feed_url=str(spec.get("feed_url") or "").strip(),
                    article_url=article_url,
                    canonical_url=canonical_url,
                    domain=domain,
                    title=title,
                    publisher=publisher,
                    summary=effective_summary,
                    dek=summary or None,
                    byline=None,
                    published_at=published_at,
                    extracted_text=extracted_text,
                    screenshot_path=screenshot_path,
                    source_card_path=source_card_path,
                    screenshot_available=bool(screenshot_path),
                    screenshot_reason=screenshot_reason,
                    content_sha256=hashlib.sha256(extracted_text.encode("utf-8")).hexdigest() if extracted_text else None,
                )
            )
        self._news_source_candidates = list(candidates)

        state = self._load_news_review_state()
        for candidate in candidates:
            record = state.get(candidate.source_id)
            if record is None:
                state[candidate.source_id] = {
                    "decision": "pending",
                    "updated_at": None,
                    "domain": candidate.domain,
                }
            elif str(record.get("decision") or "").strip().lower() == "approve-screenshot" and not candidate.screenshot_available:
                record["decision"] = "approve-facts"
                record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._save_news_review_state(state)

        payload = {
            "schema_version": 1,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "content_mode": self._content_mode(),
            "feed_urls": feed_urls,
            "news_min_approved_sources": int(self.config.news_min_approved_sources),
            "news_jurisdiction": self._news_jurisdiction(),
            "news_require_manual_source_approval": bool(self.config.news_require_manual_source_approval),
            "raw_entry_count": raw_entry_count,
            "eligible_candidate_count": len(candidate_specs),
            "candidates": [candidate.to_dict() for candidate in candidates],
        }
        self._write_json(self.paths["news_source_candidates"], payload)

        self.news_stats = {
            "content_mode": self._content_mode(),
            "source_stage_run": True,
            "raw_entry_count": raw_entry_count,
            "eligible_candidate_count": len(candidate_specs),
            "candidate_count": len(candidates),
            "feed_count": len(feed_urls),
            "approved_source_count": 0,
            "approved_domain_count": 0,
        }
        return {"candidates": len(candidates), "approved_sources": 0}

    def _approved_editorial_sources_from_state(
        self,
        candidates: list[NewsSourceCandidate],
        state: dict[str, dict[str, Any]],
    ) -> list[ApprovedEditorialSource]:
        candidates_by_id = {candidate.source_id: candidate for candidate in candidates}
        approved: list[ApprovedEditorialSource] = []
        for source_id, record in state.items():
            decision = str(record.get("decision") or "").strip().lower()
            if decision not in {"approve-facts", "approve-screenshot"}:
                continue
            candidate = candidates_by_id.get(source_id)
            if candidate is None:
                continue
            if decision == "approve-screenshot" and not candidate.screenshot_available:
                decision = "approve-facts"
            approved.append(
                ApprovedEditorialSource(
                    source_id=candidate.source_id,
                    article_url=candidate.article_url,
                    canonical_url=candidate.canonical_url,
                    domain=candidate.domain,
                    title=candidate.title,
                    publisher=candidate.publisher,
                    summary=candidate.summary,
                    dek=candidate.dek,
                    byline=candidate.byline,
                    published_at=candidate.published_at,
                    decision=decision,
                    screenshot_path=candidate.screenshot_path,
                    source_card_path=candidate.source_card_path,
                    screenshot_available=bool(candidate.screenshot_available),
                    approved_at=self._coerce_str_or_none(record.get("updated_at")),
                    rationale=self._coerce_str_or_none(record.get("rationale")),
                )
            )
        approved.sort(key=lambda item: (item.published_at or "", item.domain, item.title.lower()), reverse=True)
        return approved

    def _load_approved_editorial_sources(self) -> list[ApprovedEditorialSource]:
        candidates = self._load_news_source_candidates()
        state = self._load_news_review_state()
        approved = self._approved_editorial_sources_from_state(candidates, state)
        self._approved_editorial_sources = approved
        self._approved_editorial_sources_by_id = {source.source_id: source for source in approved}
        return approved

    def _validate_news_review_gate(self) -> None:
        if not self._news_mode_enabled():
            return

        candidate_payload = self._load_json_state(self.paths["news_source_candidates"]) or {}
        candidates = self._load_news_source_candidates()
        if not candidates:
            raw_entry_count = self._coerce_optional_int(candidate_payload.get("raw_entry_count")) or 0
            if raw_entry_count <= 0:
                raise RuntimeError(
                    "News sources stage completed, but the configured feeds returned zero entries. "
                    "Check News sources and use RSS/Atom feed URLs instead of regular web pages."
                )
            raise RuntimeError(
                "News sources stage completed, but it produced zero usable article candidates. "
                "Try different feeds or broaden the recency window before draft/preview/finalize."
            )
        state = self._load_news_review_state()
        approved = self._approved_editorial_sources_from_state(candidates, state)
        min_sources = max(1, int(self.config.news_min_approved_sources))
        distinct_domains = {source.domain for source in approved if source.domain}
        self.news_stats.update(
            {
                "content_mode": self._content_mode(),
                "candidate_count": len(candidates),
                "approved_source_count": len(approved),
                "approved_domain_count": len(distinct_domains),
                "source_review_complete": len(approved) >= min_sources and len(distinct_domains) >= min_sources,
            }
        )
        if len(approved) < min_sources or len(distinct_domains) < min_sources:
            raise RuntimeError(
                "News mode requires at least "
                f"{min_sources} approved editorial sources from {min_sources} distinct domains before continuing."
            )

        self._approved_editorial_sources = approved
        self._approved_editorial_sources_by_id = {source.source_id: source for source in approved}

    def _ensure_news_brief(self) -> NewsBrief:
        if not self._news_mode_enabled():
            brief = NewsBrief(title="", summary="", sources=[], facts=[])
            self._news_brief = brief
            return brief

        if self._news_brief is not None:
            return self._news_brief

        candidates = self._load_news_source_candidates()
        state = self._load_news_review_state()
        approved = self._approved_editorial_sources_from_state(candidates, state)
        if not approved:
            raise RuntimeError("News brief cannot be built because no editorial sources are approved.")

        candidate_by_id = {candidate.source_id: candidate for candidate in candidates}
        facts: list[dict[str, Any]] = []
        for source in approved:
            candidate = candidate_by_id.get(source.source_id)
            if candidate is None:
                continue
            fact_text = self._news_summary_sentences(candidate.extracted_text or source.summary, limit=2) or source.summary
            if not fact_text:
                continue
            facts.append(
                {
                    "fact_id": f"fact-{source.source_id}",
                    "text": fact_text,
                    "source_ids": [source.source_id],
                    "citations": [
                        {
                            "source_id": source.source_id,
                            "publisher": source.publisher,
                            "title": source.title,
                            "article_url": source.article_url,
                            "published_at": source.published_at,
                        }
                    ],
                }
            )

        brief = NewsBrief(
            title=f"News explainer: {self.config.prompt.strip() or 'current event'}",
            summary=(
                f"Editorial brief based on {len(approved)} approved sources for a U.S.-reviewed news explainer on "
                f"{self.config.prompt.strip() or 'current events'}."
            ),
            sources=approved,
            facts=facts,
        )
        self._news_brief = brief
        self._approved_editorial_sources = approved
        self._approved_editorial_sources_by_id = {source.source_id: source for source in approved}
        self.news_stats.update(
            {
                "approved_source_count": len(approved),
                "approved_domain_count": len({source.domain for source in approved if source.domain}),
                "brief_fact_count": len(facts),
                "source_review_complete": True,
            }
        )
        self._write_json(
            self.paths["news_brief"],
            {
                "schema_version": 1,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                **brief.to_dict(),
            },
        )
        return brief

    def _require_binary(self, binary_name: str) -> None:
        if shutil.which(binary_name) is None:
            raise RuntimeError(f"Missing required binary: {binary_name}")

    def _validate_render_filter_requirements(self) -> None:
        requires_bookend_text = self._intro_bookend_seconds() > 0.0 or self._outro_bookend_seconds() > 0.0
        if (requires_bookend_text or self._news_mode_enabled()) and not self._ffmpeg_supports_drawtext():
            raise RuntimeError(
                "ffmpeg drawtext filter is required for intro/outro text rendering and news source overlays. "
                "Install an ffmpeg build with drawtext support or disable news mode and intro/outro bookends."
            )

        if self.config.burn_subtitles and not self._ffmpeg_supports_subtitles_filter():
            raise RuntimeError(
                "ffmpeg subtitles filter is required for burned subtitles. "
                "Install an ffmpeg build with libass/subtitles support or disable burned subtitles."
            )

    def _script_prompt_controls(self) -> dict[str, str]:
        tone = str(self.config.script_tone or "conversational").strip().lower()
        if tone not in {"conversational", "documentary", "curiosity-driven", "analytical"}:
            tone = "conversational"

        hook_style = str(self.config.hook_style or "surprising-fact").strip().lower()
        if hook_style not in {"surprising-fact", "question", "problem-first", "story-first"}:
            hook_style = "surprising-fact"

        narrative_mode = str(self.config.narrative_mode or "story-led").strip().lower()
        if narrative_mode not in {"story-led", "explainer", "argument-led"}:
            narrative_mode = "story-led"

        example_density = str(self.config.example_density or "balanced").strip().lower()
        if example_density not in {"light", "balanced", "heavy"}:
            example_density = "balanced"

        target_audience = str(self.config.target_audience or "curious general audience").strip()
        if not target_audience:
            target_audience = "curious general audience"

        return {
            "tone": tone,
            "hook_style": hook_style,
            "narrative_mode": narrative_mode,
            "example_density": example_density,
            "target_audience": target_audience,
        }

    def _script_scene_target(self) -> int:
        controls = self._script_prompt_controls()
        base = max(10, self.config.minutes * 3)
        if controls["narrative_mode"] == "story-led":
            base = max(8, (self.config.minutes * 3) - 1)
        elif controls["narrative_mode"] == "argument-led":
            base = max(9, self.config.minutes * 3)
        return min(self.config.max_scenes, base)

    def _template_section_heads(self) -> list[str]:
        narrative_mode = self._script_prompt_controls()["narrative_mode"]
        if narrative_mode == "story-led":
            return [
                "Hook",
                "Opening Lens",
                "What People Notice",
                "What Outsiders Miss",
                "Daily Pattern",
                "Real Example",
                "Quiet Tension",
                "Why It Lands",
                "Closing Takeaway",
            ]
        if narrative_mode == "argument-led":
            return [
                "Hook",
                "Main Claim",
                "Why It Matters",
                "What Supports It",
                "Counterpoint",
                "Example",
                "Implication",
                "Closing Takeaway",
            ]
        return [
            "Hook",
            "Big Idea",
            "Context",
            "How It Works",
            "What Shapes It",
            "Real Example",
            "Common Misread",
            "Practical Takeaway",
            "Closing Takeaway",
        ]

    def _template_topic_reference(self) -> str:
        topic = re.sub(r"\s+", " ", str(self.config.prompt or "").strip()).strip(" .!?")
        if not topic:
            return "this topic"
        lowered = topic.lower()
        if lowered.startswith(
            (
                "why ",
                "how ",
                "what ",
                "when ",
                "where ",
                "who ",
                "explain ",
                "describe ",
                "show ",
                "tell ",
                "make ",
                "create ",
                "write ",
                "give ",
                "list ",
                "outline ",
                "discuss ",
            )
        ):
            return "this topic"
        return topic

    def _template_scene_voiceover(self, idx: int, total_scenes: int, heading: str) -> str:
        controls = self._script_prompt_controls()
        topic = self._template_topic_reference()
        tone = controls["tone"]
        hook_style = controls["hook_style"]
        example_density = controls["example_density"]

        if idx == 0:
            hook_map = {
                "surprising-fact": (
                    f"Most people think {topic} can be explained with a few obvious facts, "
                    "but the part that stays with viewers is usually the detail they were not expecting."
                ),
                "question": (
                    f"What is it about {topic} that feels instantly memorable to someone seeing it from the outside?"
                ),
                "problem-first": (
                    f"The problem with a lot of explanations of {topic} is that they flatten the details that make it worth watching."
                ),
                "story-first": (
                    f"Imagine stepping into {topic} for the first time and realizing the smallest details are doing most of the storytelling."
                ),
            }
            second = {
                "documentary": "That is the thread this video follows, slowly enough to make the logic behind it feel visible.",
                "curiosity-driven": "Once that clicks, the rest of the video becomes less about facts and more about why the experience feels so distinctive.",
                "analytical": "The useful move is to look past surface impressions and ask what pattern keeps repeating underneath them.",
                "conversational": "That is the angle here: not a list of facts, but a clearer feel for why it all fits together the way it does.",
            }
            return f"{hook_map.get(hook_style, hook_map['surprising-fact'])} {second.get(tone, second['conversational'])}"

        if idx == total_scenes - 1:
            closing = {
                "documentary": (
                    f"Seen this way, {topic} stops feeling like a collection of isolated details and starts reading as a coherent cultural pattern."
                ),
                "curiosity-driven": (
                    f"By the end, {topic} feels more interesting not because it is mysterious, but because the logic behind it becomes easier to notice."
                ),
                "analytical": (
                    f"The closing takeaway is that {topic} makes more sense once you connect the visible rituals to the quieter systems underneath them."
                ),
                "conversational": (
                    f"The big takeaway is that {topic} becomes much more engaging once you see the connection between the visible details and the deeper habits behind them."
                ),
            }
            return closing.get(tone, closing["conversational"])

        lead_map = {
            "documentary": [
                f"From there, the next layer of {topic} becomes easier to read when you slow down and watch what people actually repeat.",
                f"A more useful way to read {topic} is to focus on the pattern underneath the surface detail.",
                f"This part of {topic} stands out because it quietly shapes how the rest of the experience is interpreted.",
            ],
            "curiosity-driven": [
                f"Once you notice that, {topic} starts to feel much richer than the first impression suggests.",
                f"This is where {topic} gets especially interesting, because the visible detail hints at something larger underneath.",
                f"The next layer of {topic} is often the one outsiders remember, even when they cannot immediately explain why.",
            ],
            "analytical": [
                f"The next step is to treat {topic} less like a mood and more like a system with repeated signals.",
                f"That makes this part of {topic} useful, because it reveals how one visible choice affects the rest of the pattern.",
                f"Look at this layer of {topic} closely and a clearer structure starts to emerge.",
            ],
            "conversational": [
                f"The easiest way to think about this part of {topic} is to notice what keeps showing up again and again.",
                f"This is usually the moment when {topic} starts to feel less abstract and more lived-in.",
                f"Here is where {topic} gets easier to connect with, because the pattern becomes visible in everyday details.",
            ],
        }
        detail_map = {
            "light": [
                "Even one good example is enough to make that shift feel concrete.",
                "That single contrast gives the viewer something specific to hold onto.",
                "It works best when the explanation stays concrete instead of drifting into labels.",
            ],
            "balanced": [
                "A simple real-world example helps: the meaning lands faster when viewers can picture it in an ordinary setting.",
                "That comes through most clearly in a small scene a viewer can picture rather than a broad definition.",
                "One grounded example usually does more work here than a longer abstract explanation.",
            ],
            "heavy": [
                "Picture it in a real setting: one small choice, one visible reaction, and suddenly the larger pattern becomes easy to understand.",
                "The clearest version is usually a mini-scenario the viewer can imagine step by step instead of a summary label.",
                "A concrete scene makes the point land harder here, because viewers can feel the behavior before they fully name it.",
            ],
        }
        lead = lead_map.get(tone, lead_map["conversational"])[idx % 3]
        detail = detail_map.get(example_density, detail_map["balanced"])[idx % 3]
        return f"{lead} {detail}"

    def _template_support_sentence(self, heading: str, idx: int, variant: int = 0) -> str:
        controls = self._script_prompt_controls()
        topic = self._template_topic_reference()
        example_density = controls["example_density"]
        heading_label = re.sub(r"\s+\d+$", "", heading).strip().lower() or "this moment"
        detail_seed = [
            f"In practice, that is where {topic} starts to feel real instead of theoretical.",
                "That shift is easier to understand when the viewer can picture one specific moment instead of a broad category.",
                "The useful part here is the contrast between what looks obvious on the surface and what is actually guiding the moment.",
        ]
        heavier_seed = [
            f"Picture a small everyday scene and the logic of {heading_label} becomes much easier to feel.",
            f"A concrete example around {heading_label} usually explains more than a polished definition ever could.",
            f"That is why a mini-scenario works so well here: it turns {heading_label} into something a viewer can actually visualize.",
        ]
        if example_density == "heavy":
            bank = heavier_seed
        elif example_density == "light":
            bank = detail_seed[:2]
        else:
            bank = detail_seed + heavier_seed[:1]
        return bank[(idx + variant) % len(bank)]

    def _trim_voiceover_to_words(self, text: str, cap_words: int) -> str:
        cap_words = max(8, int(cap_words))
        sentences = self._split_sentences(text)
        if not sentences:
            return ""

        kept: list[str] = []
        total_words = 0
        for sentence in sentences:
            words = self._word_count_text(sentence)
            if kept and total_words + words > cap_words:
                break
            if words > cap_words and not kept:
                tokens = sentence.split()
                sentence = " ".join(tokens[:cap_words]).rstrip(",;:-")
                words = self._word_count_text(sentence)
            kept.append(sentence.strip())
            total_words += words

        if not kept:
            kept = [sentences[0].strip()]

        trimmed = " ".join(part for part in kept if part).strip()
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."
        return trimmed

    def _generate_script_plan(self) -> ScriptPlan:
        raw_plan: dict[str, Any] | None = None
        if self._news_mode_enabled():
            brief = self._ensure_news_brief()
            if self.config.script_engine == "ollama" and self._ollama_ready:
                raw_plan = self._generate_news_script_plan_ollama(brief)
                if raw_plan is None:
                    raise RuntimeError(
                        "Ollama news script generation failed. Fix the Ollama model/server or switch to --script-engine template."
                    )

            if raw_plan is None:
                self.used_template_fallback = True
                raw_plan = self._generate_news_script_plan_template(brief)

            return self._normalize_script_plan(raw_plan)

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

    def _generate_news_script_plan_ollama(self, brief: NewsBrief) -> dict[str, Any] | None:
        controls = self._script_prompt_controls()
        scene_target = self._script_scene_target()
        source_lines: list[str] = []
        for source in brief.sources[:8]:
            source_lines.append(
                " | ".join(
                    part
                    for part in (
                        source.source_id,
                        source.publisher or source.domain,
                        source.title,
                        source.summary,
                        source.published_at or "unknown date",
                        source.visual_strategy(),
                    )
                    if part
                )
            )

        fact_lines: list[str] = []
        for item in brief.facts[:10]:
            if not isinstance(item, dict):
                continue
            text_value = str(item.get("text") or "").strip()
            source_ids = [str(value).strip() for value in item.get("source_ids") or [] if str(value).strip()]
            if text_value:
                fact_lines.append(f"- {text_value} [{', '.join(source_ids)}]")

        prompt = textwrap.dedent(
            f"""
            You are a script planner for faceless YouTube news explainers.
            Return JSON only. Do not include markdown.

            Requirements:
            - topic: {self.config.prompt}
            - output duration target: {self.config.minutes} minutes
            - tone: {controls["tone"]}
            - audience: {controls["target_audience"]}
            - hook style: {controls["hook_style"]}
            - narrative mode: {controls["narrative_mode"]}
            - example density: {controls["example_density"]}
            - scene count target: around {scene_target}
            - jurisdiction: U.S. fair-use review workflow
            - use only the approved editorial brief below
            - do not copy article phrasing, long quotes, or body text
            - paraphrase facts in fresh spoken language
            - no speculation beyond the brief
            - no host or face references
            - write for spoken YouTube narration, not article prose
            - prefer 2-4 source-backed scenes that use source_refs

            Approved sources:
            {chr(10).join(source_lines)}

            Fact bullets:
            {chr(10).join(fact_lines)}

            JSON schema:
            {{
              "title": "string",
              "summary": "1-2 sentence summary",
              "scenes": [
                {{
                  "heading": "short heading",
                  "voiceover": "narration for this scene",
                  "search_terms": ["keyword 1", "keyword 2", "keyword 3"],
                  "source_refs": ["source_id"],
                  "visual_strategy": "stock|news-source-screenshot|source-card"
                }}
              ]
            }}

            Use visual_strategy=news-source-screenshot only when the source's visual_strategy says news-source-screenshot.
            """
        ).strip()
        stdout = self._ollama_generate(prompt, timeout=600)
        if stdout is None:
            return None

        return self._extract_json_object(stdout)

    def _generate_news_script_plan_template(self, brief: NewsBrief) -> dict[str, Any]:
        approved = list(brief.sources)
        scene_count = max(6, min(self._script_scene_target(), max(6, len(approved) + 4)))
        topic = self.config.prompt.strip() or "current event"
        scenes: list[dict[str, Any]] = []

        for index, source in enumerate(approved[: max(2, min(4, len(approved)))]):
            voice_parts = [
                f"Start with the latest verified frame from {source.publisher or source.domain}.",
                source.summary or f"{source.title} is one of the approved reports shaping this explainer.",
                "The goal here is to paraphrase the reported facts clearly, not repeat the article's wording.",
            ]
            scenes.append(
                {
                    "heading": f"Source check {index + 1}",
                    "voiceover": " ".join(part.strip() for part in voice_parts if part).strip(),
                    "search_terms": [source.publisher or source.domain, topic, "news"],
                    "source_refs": [source.source_id],
                    "visual_strategy": source.visual_strategy(),
                }
            )

        section_heads = [
            "What changed",
            "Why it matters",
            "What is confirmed",
            "What remains unclear",
            "Regional context",
            "What to watch next",
        ]
        while len(scenes) < scene_count:
            index = len(scenes)
            fact = brief.facts[index % len(brief.facts)] if brief.facts else {}
            fact_text = str(fact.get("text") or "").strip()
            scenes.append(
                {
                    "heading": section_heads[index % len(section_heads)],
                    "voiceover": (
                        f"{fact_text} Keep the explanation grounded in the approved brief and connect it back to {topic}."
                        if fact_text
                        else f"This section keeps the explainer focused on verified developments around {topic}."
                    ),
                    "search_terms": [topic, "analysis", "timeline"],
                    "source_refs": [],
                    "visual_strategy": "stock",
                }
            )

        return {
            "title": f"News explainer: {topic}",
            "summary": brief.summary,
            "scenes": scenes,
        }

    def _ollama_generate(self, prompt: str, timeout: int) -> str | None:
        """Return Ollama's raw completion via the HTTP API.

        We use /api/generate instead of `ollama run` because the CLI injects
        terminal reflow/ANSI control codes into stdout even when piped, which
        corrupt the JSON the model emits. Returns None on any failure.
        """
        host = (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        try:
            response = self.http.post(
                f"{host.rstrip('/')}/api/generate",
                # format=json constrains the model to emit syntactically valid JSON.
                # Every caller asks for JSON, so this is safe and kills the
                # intermittent parse failures from prose-wrapped/malformed output.
                json={
                    "model": self.config.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=(5, timeout),
            )
            response.raise_for_status()
            return str(response.json().get("response") or "")
        except requests.exceptions.RequestException as exc:
            self._warn(f"Ollama error: {exc}")
            return None

    def _generate_script_plan_ollama(self) -> dict[str, Any] | None:
        controls = self._script_prompt_controls()
        scene_target = self._script_scene_target()
        prompt = textwrap.dedent(
            f"""
            You are a script planner for faceless YouTube explainers.
            Return JSON only. Do not include markdown.

            Requirements:
            - topic: {self.config.prompt}
            - output duration target: {self.config.minutes} minutes
            - tone: {controls["tone"]}
            - audience: {controls["target_audience"]}
            - hook style: {controls["hook_style"]}
            - narrative mode: {controls["narrative_mode"]}
            - example density: {controls["example_density"]}
            - scene count target: around {scene_target}
            - no host or face references
            - write for spoken YouTube narration, not article prose
            - scene 1 must open with a strong hook
            - use concrete examples, comparisons, or mini-scenarios whenever useful
            - vary sentence length and avoid repetitive scene openers
            - avoid generic filler like "in this section", "let's dive in", "it is important to note", or "in practical terms"

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

            Ensure each scene voiceover sounds natural when spoken aloud.
            Prefer 2-4 sentences per scene, with some variation when needed.
            Keep all claims practical and avoid speculative statements.
            """
        ).strip()

        _SCRIPT_LANG_NAMES = {"pt-br": "Brazilian Portuguese", "es": "Spanish", "fr": "French"}
        if getattr(self.config, "script_language", "en") not in ("en", "", None):
            lang_name = _SCRIPT_LANG_NAMES.get(
                self.config.script_language, self.config.script_language
            )
            prompt += f"\n- language: Generate ALL content (scene titles, voiceovers, narration) in {lang_name}. Do NOT use English."

        stdout = self._ollama_generate(prompt, timeout=600)
        if stdout is None:
            return None

        parsed = self._extract_json_object(stdout)
        if parsed is None:
            return None
        return parsed

    def _generate_script_plan_template(self) -> dict[str, Any]:
        scene_count = self._script_scene_target()
        section_heads = self._template_section_heads()
        controls = self._script_prompt_controls()
        topic = self.config.prompt.strip() or "this topic"

        scenes: list[dict[str, Any]] = []
        for idx in range(scene_count):
            head = section_heads[idx % len(section_heads)]
            heading = f"{head} {idx + 1}"
            voiceover = self._template_scene_voiceover(idx, scene_count, heading)
            search_terms = self._default_search_terms(heading)
            scenes.append(
                {
                    "heading": heading,
                    "voiceover": voiceover,
                    "search_terms": search_terms,
                }
            )

        return {
            "title": f"Explainer: {topic}",
            "summary": (
                f"A {controls['tone']} long-form video for {controls['target_audience']} "
                f"about {topic}."
            ),
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

            scene_id = str(item.get("scene_id") or f"scene_{idx + 1:03d}").strip()
            if not scene_id:
                scene_id = f"scene_{idx + 1:03d}"
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
            raw_source_refs = item.get("source_refs")
            if isinstance(raw_source_refs, list):
                source_refs = [str(value).strip() for value in raw_source_refs if str(value).strip()]
            else:
                source_refs = []
            visual_strategy = normalize_news_visual_strategy(item.get("visual_strategy"), "stock")

            clip_name_raw = str(item.get("clip_name") or "").strip().lower()
            if clip_name_raw:
                clip_name = clip_name_raw
                suffix = 2
                while clip_name in used_clip_names:
                    clip_name = f"{clip_name_raw}-{suffix}"
                    suffix += 1
                used_clip_names.add(clip_name)
            else:
                clip_name = self._build_scene_clip_name(idx=idx, heading=heading, used=used_clip_names)

            scenes.append(
                Scene(
                    scene_id=scene_id,
                    clip_name=clip_name,
                    heading=heading,
                    voiceover=voiceover,
                    search_terms=search_terms[:4],
                    seconds=0.0,
                    source_refs=source_refs[:2],
                    visual_strategy=visual_strategy,
                )
            )

        if not scenes:
            if self.config.script_engine == "ollama":
                raise RuntimeError("Ollama returned an invalid script plan with no usable scenes.")
            fallback = (
                self._generate_news_script_plan_template(self._ensure_news_brief())
                if self._news_mode_enabled()
                else self._generate_script_plan_template()
            )
            return self._normalize_script_plan(fallback)

        # Set initial equal durations before voiceover timing rebalance.
        per_scene = max(self.config.min_scene_seconds, self.config.target_seconds() / len(scenes))
        for scene in scenes:
            scene.seconds = per_scene

        plan = ScriptPlan(title=title, summary=summary, scenes=scenes)
        if self._news_mode_enabled():
            self._apply_news_scene_defaults(plan)
        return plan

    def _apply_news_scene_defaults(self, plan: ScriptPlan) -> None:
        approved = self._approved_editorial_sources or self._load_approved_editorial_sources()
        if not approved:
            return

        approved_by_id = {source.source_id: source for source in approved}
        target_source_scene_count = min(len(approved), max(2, min(4, len(plan.scenes))))
        already_assigned = {
            source_id
            for scene in plan.scenes
            for source_id in scene.source_refs
            if source_id in approved_by_id
        }

        for scene in plan.scenes:
            scene.visual_strategy = normalize_news_visual_strategy(scene.visual_strategy, "stock")
            scene.source_refs = [source_id for source_id in scene.source_refs if source_id in approved_by_id][:2]
            if scene.source_refs:
                source = approved_by_id.get(scene.source_refs[0])
                if source is not None and scene.visual_strategy == "stock":
                    scene.visual_strategy = source.visual_strategy()

        missing_sources = [source for source in approved if source.source_id not in already_assigned]
        for scene in plan.scenes[:target_source_scene_count]:
            if not missing_sources:
                break
            if scene.source_refs:
                continue
            source = missing_sources.pop(0)
            scene.source_refs = [source.source_id]
            scene.visual_strategy = source.visual_strategy()

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
            raw_source_refs = item.get("source_refs")
            if isinstance(raw_source_refs, list):
                source_refs = [str(value).strip() for value in raw_source_refs if str(value).strip()]
            else:
                source_refs = []
            visual_strategy = normalize_news_visual_strategy(item.get("visual_strategy"), "stock")

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
                    source_refs=source_refs[:2],
                    visual_strategy=visual_strategy,
                    asset_path=asset_path_raw or None,
                    asset_provider=asset_provider_raw or None,
                )
            )

        if not scenes:
            raise RuntimeError("script.json has no valid scenes to process")

        plan = ScriptPlan(title=title, summary=summary, scenes=scenes)
        if self._news_mode_enabled():
            self._apply_news_scene_defaults(plan)
        return plan

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
                    media_type=(str(item.get("media_type") or "").strip() or None),
                    width=self._coerce_optional_int(item.get("width")),
                    height=self._coerce_optional_int(item.get("height")),
                    duration_seconds=self._coerce_optional_float(item.get("duration_seconds")),
                    restriction_flags=restriction_flags,
                    attribution_required=bool(item.get("attribution_required", False)),
                    attribution_text=(str(item.get("attribution_text") or "").strip() or None),
                    scene_components=[
                        dict(component)
                        for component in item.get("scene_components") or []
                        if isinstance(component, dict)
                    ]
                    if isinstance(item.get("scene_components"), list)
                    else [],
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
            total += len(re.findall(r"[^\W_]+(?:'[^\W_]+)?", scene.voiceover or ""))
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
            self._warn("Ollama script expansion underperformed; preserving current script instead of adding filler.")
            return plan

        return self._expand_script_plan_template(plan, target_words)

    def _compress_plan_to_target_words(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        current_words = self._word_count_plan(plan)
        if current_words <= target_words:
            return plan

        if self.config.script_engine == "ollama" and self._ollama_ready:
            compressed = self._compress_script_plan_ollama(plan, target_words)
            if compressed is not None and self._word_count_plan(compressed) < current_words:
                return compressed
            self._warn("Ollama script compression underperformed; preserving current script instead of flattening it.")
            return plan

        return self._compress_script_plan_template(plan, target_words)

    def _expand_script_plan_ollama(self, plan: ScriptPlan, target_words: int) -> ScriptPlan | None:
        controls = self._script_prompt_controls()
        current_words = self._word_count_plan(plan)
        scene_target = len(plan.scenes)
        current_json = json.dumps(plan.to_dict(), ensure_ascii=True)
        prompt = textwrap.dedent(
            f"""
            You are expanding an existing faceless explainer script.
            Return JSON only. Do not include markdown.

            Current total words: {current_words}
            Target total words: at least {target_words}
            Tone: {controls["tone"]}
            Audience: {controls["target_audience"]}
            Hook style: {controls["hook_style"]}
            Narrative mode: {controls["narrative_mode"]}
            Example density: {controls["example_density"]}
            Keep topic: {self.config.prompt}
            Keep scene count exactly {scene_target}
            Preserve scene order and scene ids.
            Make minimal-diff edits only.
            Protect the opener in scene_001 and preserve the closing payoff.
            Prefer adding specificity, examples, contrast, and connective tissue over adding generic filler.
            Avoid generic phrases like "in practical terms" or "it is important to note".

            Input JSON:
            {current_json}

            Output schema:
            {{
              "title": "string",
              "summary": "string",
              "scenes": [
                {{
                  "scene_id": "scene_001",
                  "clip_name": "scene clip slug",
                  "heading": "short heading",
                  "voiceover": "2-5 spoken sentences",
                  "search_terms": ["keyword1", "keyword2", "keyword3"]
                }}
              ]
            }}
            """
        ).strip()

        stdout = self._ollama_generate(prompt, timeout=900)
        if stdout is None:
            return None

        parsed = self._extract_json_object(stdout)
        if parsed is None:
            return None
        return self._normalize_script_plan(parsed)

    def _compress_script_plan_ollama(self, plan: ScriptPlan, target_words: int) -> ScriptPlan | None:
        controls = self._script_prompt_controls()
        current_words = self._word_count_plan(plan)
        scene_target = len(plan.scenes)
        current_json = json.dumps(plan.to_dict(), ensure_ascii=True)
        prompt = textwrap.dedent(
            f"""
            You are compressing an existing faceless explainer script.
            Return JSON only. Do not include markdown.

            Current total words: {current_words}
            Target total words: around {target_words}
            Tone: {controls["tone"]}
            Audience: {controls["target_audience"]}
            Hook style: {controls["hook_style"]}
            Narrative mode: {controls["narrative_mode"]}
            Example density: {controls["example_density"]}
            Keep the same core meaning and topic: {self.config.prompt}
            Keep scene count exactly {scene_target}, preserve scene order and scene ids.
            Make minimal-diff edits only.
            Protect the opener in scene_001 and preserve the closing payoff.
            Trim repetition and throat-clearing before cutting examples or transitions.

            Input JSON:
            {current_json}

            Output schema:
            {{
              "title": "string",
              "summary": "string",
              "scenes": [
                {{
                  "scene_id": "scene_001",
                  "clip_name": "scene clip slug",
                  "heading": "short heading",
                  "voiceover": "1-3 spoken sentences",
                  "search_terms": ["keyword1", "keyword2", "keyword3"]
                }}
              ]
            }}
            """
        ).strip()

        stdout = self._ollama_generate(prompt, timeout=900)
        if stdout is None:
            return None

        parsed = self._extract_json_object(stdout)
        if parsed is None:
            return None
        return self._normalize_script_plan(parsed)

    def _expand_script_plan_template(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        expanded_scenes: list[dict[str, Any]] = []
        for idx, scene in enumerate(plan.scenes):
            voiceover = scene.voiceover.strip()
            if voiceover and voiceover[-1] not in ".!?":
                voiceover += "."
            voiceover = (voiceover + " " + self._template_support_sentence(scene.heading, idx, variant=0)).strip()
            expanded_scenes.append(
                {
                    "scene_id": scene.scene_id,
                    "clip_name": scene.clip_name,
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
        variant = 1
        while words < target_words:
            changed = False
            for idx, scene in enumerate(expanded_scenes):
                scene_voiceover = str(scene.get("voiceover") or "").strip()
                if not scene_voiceover:
                    continue
                scene["voiceover"] = (
                    scene_voiceover + " " + self._template_support_sentence(str(scene.get("heading") or ""), idx, variant=variant)
                ).strip()
                changed = True
                fallback_plan["scenes"] = expanded_scenes
                normalized = self._normalize_script_plan(fallback_plan)
                words = self._word_count_plan(normalized)
                if words >= target_words:
                    break
            if not changed:
                break
            variant += 1
            if variant > 3:
                break

        return normalized

    def _compress_script_plan_template(self, plan: ScriptPlan, target_words: int) -> ScriptPlan:
        cap_words = 28
        reduced_scenes: list[dict[str, Any]] = []
        for scene in plan.scenes:
            reduced_scenes.append(
                {
                    "scene_id": scene.scene_id,
                    "clip_name": scene.clip_name,
                    "heading": scene.heading,
                    "voiceover": self._trim_voiceover_to_words(scene.voiceover, cap_words=cap_words),
                    "search_terms": scene.search_terms[:4],
                }
            )

        compressed_plan = {
            "title": plan.title,
            "summary": plan.summary,
            "scenes": reduced_scenes,
        }
        normalized = self._normalize_script_plan(compressed_plan)

        while self._word_count_plan(normalized) > target_words and cap_words > 14:
            cap_words -= 3
            for scene_dict, source_scene in zip(reduced_scenes, plan.scenes, strict=False):
                scene_dict["voiceover"] = self._trim_voiceover_to_words(source_scene.voiceover, cap_words=cap_words)
            compressed_plan["scenes"] = reduced_scenes
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
        narration_text = self._prepare_tts_narration_text(text)
        chunks = self._build_narration_chunks(narration_text)
        if not chunks:
            raise RuntimeError("Narration text produced no speakable chunks")

        tts_policy = describe_tts_config_policy(self.config)
        if str(tts_policy.get("policy_result") or "") == "deny":
            selection = str(tts_policy.get("voice_display") or self.config.tts_engine).strip() or self.config.tts_engine
            raise RuntimeError(
                "Narration blocked by TTS policy: "
                f"{selection}. {str(tts_policy.get('reason') or '').strip()}"
            )

        if self.config.tts_engine == "melo":
            self._tts_with_melo(chunks, output_raw_wav)
            return

        if self.config.tts_engine == "piper":
            self._tts_with_piper(chunks, output_raw_wav)
            return

        if self.config.tts_engine == "kokoro":
            self._tts_with_kokoro(chunks, output_raw_wav)
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
                "max_words_per_chunk": 26,
            },
            "balanced": {
                "speed_multiplier": 1.0,
                "clause_pause": 0.11,
                "sentence_pause": 0.26,
                "paragraph_pause": 0.65,
                "max_words_per_chunk": 30,
            },
            "energetic-explainer": {
                "speed_multiplier": 1.07,
                "clause_pause": 0.07,
                "sentence_pause": 0.17,
                "paragraph_pause": 0.5,
                "max_words_per_chunk": 34,
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

        candidates = [part.strip() for part in re.split(r"(?<=[;:])\s+", sentence) if part.strip()]
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
        tokens = re.findall(r"[^\W_]+(?:'[^\W_]+)?", text or "")
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
        return len(re.findall(r"[^\W_]+(?:'[^\W_]+)?", text or ""))

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
        active_chunks = [
            (
                idx,
                str(chunk.get("text") or "").strip(),
                float(chunk.get("pause_after") or 0.0),
            )
            for idx, chunk in enumerate(chunks)
            if str(chunk.get("text") or "").strip()
        ]

        def synthesize_chunk(
            idx: int,
            text: str,
            pause_seconds: float,
        ) -> tuple[int, list[Path]]:
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
            chunk_files = [wav_path]

            if pause_seconds > 0.0:
                pause_path = parts_dir / f"pause_{idx:04d}.wav"
                self._generate_silence_wav(pause_path, pause_seconds)
                chunk_files.append(pause_path)
            return idx, chunk_files

        chunk_workers = min(4, len(active_chunks))
        synthesized_parts: dict[int, list[Path]] = {}
        if chunk_workers <= 1:
            for idx, text, pause_seconds in active_chunks:
                chunk_index, chunk_files = synthesize_chunk(idx, text, pause_seconds)
                synthesized_parts[chunk_index] = chunk_files
        else:
            with ThreadPoolExecutor(max_workers=chunk_workers) as executor:
                futures = [
                    executor.submit(synthesize_chunk, idx, text, pause_seconds)
                    for idx, text, pause_seconds in active_chunks
                ]
            for future in futures:
                chunk_index, chunk_files = future.result()
                synthesized_parts[chunk_index] = chunk_files

        for idx, chunk in enumerate(chunks):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            part_files.extend(synthesized_parts.get(idx, []))

        if not part_files:
            raise RuntimeError("Piper TTS did not produce any audio parts")

        self._concat_wav_parts(part_files, output_raw_wav)

    def _tts_with_kokoro(self, chunks: list[dict[str, Any]], output_raw_wav: Path) -> None:
        pipeline = self._kokoro_pipeline()
        voice = self._resolve_kokoro_voice()
        parts_dir = self.paths["tmp"] / "tts_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        part_files: list[Path] = []
        settings = self._voice_profile_settings()
        effective_speed = max(0.5, min(2.0, float(self.config.voice_speed) * float(settings["speed_multiplier"])))

        for idx, chunk in enumerate(chunks):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue

            produced_audio = False
            try:
                for part_idx, result in enumerate(pipeline(text, voice=voice, speed=effective_speed, split_pattern=None)):
                    output = getattr(result, "output", None)
                    audio = getattr(output, "audio", None)
                    if audio is None:
                        continue

                    raw_path = parts_dir / f"kokoro_{idx:04d}_{part_idx:02d}.raw.wav"
                    wav_path = parts_dir / f"kokoro_{idx:04d}_{part_idx:02d}.wav"
                    self._write_kokoro_audio_wav(audio, raw_path)
                    self._standardize_wav(raw_path, wav_path)
                    part_files.append(wav_path)
                    produced_audio = True
            except Exception as exc:
                raise RuntimeError(f"Kokoro synthesis failed: {exc}") from exc

            if not produced_audio:
                raise RuntimeError("Kokoro did not produce audio for one or more narration chunks")

            pause_seconds = float(chunk.get("pause_after") or 0.0)
            if pause_seconds > 0.0:
                pause_path = parts_dir / f"pause_{idx:04d}.wav"
                self._generate_silence_wav(pause_path, pause_seconds)
                part_files.append(pause_path)

        if not part_files:
            raise RuntimeError("Kokoro TTS did not produce any audio parts")

        self._concat_wav_parts(part_files, output_raw_wav)
        if not output_raw_wav.exists() or output_raw_wav.stat().st_size == 0:
            raise RuntimeError("Kokoro TTS did not produce audio output")

    def _kokoro_pipeline(self) -> Any:
        lang_code = normalize_kokoro_lang_code(self.config.kokoro_lang_code)
        cached = self._kokoro_pipelines.get(lang_code)
        if cached is not None:
            return cached

        try:
            from kokoro import KPipeline  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Kokoro TTS not available. Install voice deps with: python -m pip install -e '.[voice]'"
            ) from exc

        try:
            pipeline = KPipeline(lang_code=lang_code)
        except Exception as exc:
            msg = str(exc)
            if lang_code not in ("en-us", "en-gb"):
                raise RuntimeError(
                    f"Kokoro {lang_code} requires espeak-ng for phonemization. "
                    "Install: brew install espeak-ng && pip install phonemizer  "
                    "then set: export PHONEMIZER_ESPEAK_LIBRARY=/opt/homebrew/lib/libespeak-ng.dylib"
                ) from exc
            raise RuntimeError(f"Kokoro pipeline init failed: {msg}") from exc
        # Espeak-backed g2p can return (phoneme_string, tokens) tuples for
        # non-English languages, while Kokoro expects a plain phoneme string.
        # English uses Kokoro's internal `a`/`b` aliases and must be left alone.
        pipeline_lang_code = str(getattr(pipeline, "lang_code", lang_code) or "").strip().lower()
        if hasattr(pipeline, "g2p") and pipeline_lang_code not in {"a", "b"}:
            _orig_g2p = pipeline.g2p

            class _G2PWrapper:
                # kokoro >=0.9 unpacks `ps, _ = self.g2p(chunk)` in its
                # non-English path, so return a 2-tuple (phoneme string first),
                # not a bare string (which unpacks char-by-char and explodes).
                def __call__(self_, text: str):  # noqa: N805
                    result = _orig_g2p(text)
                    ps = result[0] if isinstance(result, tuple) else result
                    return ps, None

            pipeline.g2p = _G2PWrapper()

        self._kokoro_pipelines[lang_code] = pipeline
        return pipeline

    def _resolve_kokoro_voice(self) -> str:
        configured = str(self.config.kokoro_voice or "").strip()
        voice = configured or default_kokoro_voice(self.config.kokoro_lang_code)
        parts = [part.strip() for part in voice.split(",") if part.strip()]
        if not parts:
            raise RuntimeError("Kokoro voice is required when --tts-engine kokoro is selected")

        available = set(KOKORO_VOICE_PRESETS)
        invalid = [part for part in parts if part not in available]
        if invalid:
            raise RuntimeError(
                "Unknown Kokoro voice id(s): "
                f"{', '.join(invalid)}. Available ids: {', '.join(KOKORO_VOICE_PRESETS)}"
            )
        return ",".join(parts)

    def _write_kokoro_audio_wav(self, audio_tensor: Any, output_wav: Path, sample_rate: int = 24000) -> None:
        try:
            from array import array

            import torch  # type: ignore
        except Exception as exc:
            raise RuntimeError("Kokoro runtime is missing required audio conversion dependencies") from exc

        if audio_tensor is None or not hasattr(audio_tensor, "detach"):
            raise RuntimeError("Kokoro did not return a valid audio tensor")

        samples = (
            audio_tensor.detach()
            .cpu()
            .flatten()
            .clamp(-1.0, 1.0)
            .mul(32767.0)
            .round()
            .to(dtype=torch.int16)
            .tolist()
        )
        if not samples:
            raise RuntimeError("Kokoro returned empty audio data")

        pcm = array("h", samples)
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(max(8000, int(sample_rate)))
            handle.writeframes(pcm.tobytes())

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

        resolved = piper_voice_preset_meta(voice_id, speaker_id)
        if resolved is None:
            available_ids = sorted({str(item.get("id") or "").strip() for item in PIPER_VOICE_PRESETS if item.get("id")})
            raise RuntimeError(
                "Unknown Piper voice id: "
                f"{voice_id}. Available ids: {', '.join(available_ids)}"
            )
        return resolved

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

        remaining_tail = tail
        outro_narration_wav = self.paths["outro_narration_wav"]
        if remaining_tail > 0.0 and outro_narration_wav.exists() and outro_narration_wav.stat().st_size > 0:
            lead_silence = self._outro_lead_silence_seconds()
            if lead_silence > 0.0:
                lead_pause_path = parts_dir / "outro_lead_silence.wav"
                self._generate_silence_wav(lead_pause_path, lead_silence, sample_rate=48000)
                part_files.append(lead_pause_path)
                remaining_tail = max(0.0, remaining_tail - lead_silence)
            part_files.append(outro_narration_wav)
            remaining_tail = max(0.0, remaining_tail - self._media_duration(outro_narration_wav))

        if remaining_tail > 0.0:
            tail_path = parts_dir / "outro_silence.wav"
            self._generate_silence_wav(tail_path, remaining_tail, sample_rate=48000)
            part_files.append(tail_path)

        self._concat_wav_parts(
            part_files=part_files,
            output_wav=output_audio,
            sample_rate=48000,
            concat_list_path=parts_dir / "concat_bookends.txt",
        )

    def _resolved_outro_spoken_text(self) -> str:
        explicit = re.sub(r"\s+", " ", str(self.config.outro_spoken_text or "").strip())
        if explicit:
            return explicit
        outro_text = re.sub(r"\s+", " ", str(self.config.outro_text or "").strip())
        outro_tagline = re.sub(r"\s+", " ", str(self.config.outro_tagline or "").strip())
        if outro_text and outro_tagline:
            return f"{outro_text}. {outro_tagline}".strip(". ")
        return outro_text or outro_tagline

    def _ensure_outro_narration_audio(self) -> None:
        raw_path = self.paths["outro_narration_raw"]
        wav_path = self.paths["outro_narration_wav"]
        spoken_text = self._clean_narration_text(self._resolved_outro_spoken_text())
        if not self.config.include_outro or not spoken_text:
            raw_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            return

        self._synthesize_narration(spoken_text, raw_path)
        self._normalize_audio(raw_path, wav_path)

    def _outro_spoken_audio_duration(self) -> float:
        path = self.paths["outro_narration_wav"]
        if not path.exists() or path.stat().st_size <= 0:
            return 0.0
        return max(0.0, self._media_duration(path))

    def _outro_lead_silence_seconds(self) -> float:
        if not self.config.include_outro:
            return 0.0
        if self._outro_spoken_audio_duration() <= 0.0:
            return 0.0
        return 2.0

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
        if self.config.tts_engine not in {"melo", "kokoro"}:
            raise RuntimeError("Voice A/B samples currently support only Melo and Kokoro TTS")

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

        original_selection = (
            self.config.melo_speaker if self.config.tts_engine == "melo" else self.config.kokoro_voice
        )
        sample_entries: list[dict[str, Any]] = []
        comparison_parts: list[Path] = []

        try:
            for idx, speaker in enumerate(unique_speakers, start=1):
                if self.config.tts_engine == "melo":
                    self.config.melo_speaker = speaker
                else:
                    self.config.kokoro_voice = speaker
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", speaker).strip("_") or f"speaker_{idx}"
                raw_path = output_dir / f"{idx:02d}_{safe_name}.raw.wav"
                final_path = output_dir / f"{idx:02d}_{safe_name}.wav"

                self._synthesize_narration(excerpt, raw_path)
                self._normalize_audio(raw_path, final_path)
                duration = self._media_duration(final_path)

                sample_entries.append(
                    {
                        "speaker": speaker,
                        "voice": speaker,
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
            if self.config.tts_engine == "melo":
                self.config.melo_speaker = original_selection
            else:
                self.config.kokoro_voice = original_selection

        compare_mix = output_dir / "ab_compare.wav"
        self._concat_wav_parts(comparison_parts, compare_mix)

        report = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "tts_engine": self.config.tts_engine,
            "voice_profile": self.config.voice_profile,
            "voice_speed": self.config.voice_speed,
            "melo_language": self.config.melo_language,
            "kokoro_lang_code": normalize_kokoro_lang_code(self.config.kokoro_lang_code),
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

    def _enabled_provider_order(self) -> list[str]:
        provider_order: list[str] = []
        if self.config.enable_pexels_provider:
            provider_order.append("pexels")
        if self.config.enable_pixabay_provider:
            provider_order.append("pixabay")
        if self.config.enable_coverr_provider:
            provider_order.append("coverr")
        if self.config.enable_vecteezy_provider:
            provider_order.append("vecteezy")
        return provider_order

    def _primary_provider_order(self, provider_order: list[str]) -> list[str]:
        return [provider_name for provider_name in provider_order if provider_name not in FALLBACK_ONLY_ASSET_PROVIDERS]

    def _fallback_provider_order(self, provider_order: list[str]) -> list[str]:
        return [provider_name for provider_name in provider_order if provider_name in FALLBACK_ONLY_ASSET_PROVIDERS]

    def _provider_is_configured(self, provider_name: str) -> bool:
        key = str(provider_name or "").strip().lower()
        if key == "pexels":
            return bool(self._coerce_str_or_none(self.config.pexels_api_key))
        if key == "pixabay":
            return bool(self._coerce_str_or_none(self.config.pixabay_api_key))
        if key == "coverr":
            return bool(self._coerce_str_or_none(self.config.coverr_api_key))
        if key == "vecteezy":
            return bool(
                self._coerce_str_or_none(self.config.vecteezy_api_key)
                and self._coerce_str_or_none(self.config.vecteezy_account_id)
            )
        return False

    def _enabled_provider_labels(self) -> list[str]:
        labels: list[str] = []
        if self.config.enable_pexels_provider:
            labels.append(ASSET_PROVIDER_LABELS.get("pexels", "Pexels"))
        if self.config.enable_pixabay_provider:
            labels.append(ASSET_PROVIDER_LABELS.get("pixabay", "Pixabay"))
        if self.config.enable_coverr_provider:
            labels.append(ASSET_PROVIDER_LABELS.get("coverr", "Coverr"))
        if self.config.enable_vecteezy_provider:
            labels.append(ASSET_PROVIDER_LABELS.get("vecteezy", "Vecteezy"))
        return labels

    def _provider_display_name(self, provider_name: str | None) -> str:
        key = str(provider_name or "").strip().lower()
        return ASSET_PROVIDER_LABELS.get(key, key.title() or "Unknown")

    def _normalized_asset_mode(self) -> str:
        value = str(self.config.asset_mode or "prefer-video").strip().lower()
        if value not in ASSET_MODE_CHOICES:
            return "prefer-video"
        return value

    def _normalized_image_motion_style(self, raw_style: str | None = None) -> str:
        value = str(raw_style if raw_style is not None else self.config.image_motion_style or "slow").strip().lower()
        value = IMAGE_MOTION_STYLE_ALIASES.get(value, value)
        if value not in IMAGE_MOTION_STYLE_CHOICES:
            return "slow"
        return value

    def _image_assets_enabled(self) -> bool:
        return bool(self.config.allow_image_assets) or self._normalized_asset_mode() in {"prefer-images", "images-only"}

    def _candidate_policy_block_reason(self, candidate: AssetCandidate) -> str | None:
        if candidate.media_type == "image" and not self._image_assets_enabled():
            return "image assets are disabled by policy"

        if candidate.media_type != "image" and self._normalized_asset_mode() == "images-only":
            return "video assets are disabled by images-only mode"

        if candidate.attribution_required and not self.config.allow_attribution_required_assets:
            return "attribution-required assets are disabled by policy"

        provider_name = str(candidate.source_platform or "").strip().lower()
        if provider_name == "vecteezy" and not self._candidate_is_cached(candidate):
            remaining_downloads = self._vecteezy_estimated_remaining_downloads()
            if remaining_downloads is not None and remaining_downloads <= 0:
                return "Vecteezy monthly download quota is exhausted"

        if not self.config.strict_commercial_safe:
            return None

        restriction_flags = {
            str(flag).strip().lower()
            for flag in candidate.restriction_flags
            if str(flag).strip()
        }
        blocked_flags = sorted(flag for flag in restriction_flags if flag in STRICT_SAFE_BLOCKING_FLAGS)
        if blocked_flags:
            return "blocked by strict-safe policy (" + ", ".join(blocked_flags) + ")"
        return None

    def _resolve_editorial_scene_asset(self, scene: Scene) -> AssetRight | None:
        if not self._news_mode_enabled():
            return None
        if normalize_news_visual_strategy(scene.visual_strategy, "stock") == "stock":
            return None
        if not scene.source_refs:
            return None

        source = self._approved_editorial_sources_by_id.get(scene.source_refs[0])
        if source is None:
            approved = self._approved_editorial_sources or self._load_approved_editorial_sources()
            self._approved_editorial_sources_by_id = {item.source_id: item for item in approved}
            source = self._approved_editorial_sources_by_id.get(scene.source_refs[0])
        if source is None:
            return None

        local_path_value = source.visual_path()
        if not local_path_value:
            return None
        local_path = Path(local_path_value).expanduser().resolve()
        if not local_path.exists() or local_path.stat().st_size <= 0:
            return None

        scene.asset_path = str(local_path)
        scene.asset_provider = "editorial-source"
        right = AssetRight(
            scene_id=scene.scene_id,
            source_platform="editorial-source",
            source_asset_id=source.source_id,
            source_url=source.article_url,
            creator_name=source.publisher,
            creator_profile_url=None,
            license_name="Editorial source (manual U.S. fair-use review required)",
            license_url=None,
            downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            local_path=str(local_path),
            sha256=self._file_sha256(local_path),
            media_type="image",
            width=None,
            height=None,
            duration_seconds=None,
            restriction_flags=["manual-review-required"],
            attribution_required=False,
            attribution_text=None,
        )
        self._selected_assets_by_scene.pop(scene.scene_id, None)
        return right

    def _resolve_assets(
        self,
        plan: ScriptPlan,
        *,
        scenes: list[Scene] | None = None,
        preused_asset_keys: set[str] | None = None,
        preferred_candidates: dict[str, AssetCandidate] | None = None,
        strict_query_override_scene_ids: set[str] | None = None,
    ) -> list[AssetRight]:
        rights: list[AssetRight] = []
        query_cache: dict[tuple[str, str], list[AssetCandidate]] = {}
        download_failures = 0
        placeholder_scenes = 0
        duplicate_candidate_rejections = 0
        short_duration_rejections = 0
        resolved_video_scenes = 0
        resolved_image_scenes = 0
        unique_shortfall_scene_ids: list[str] = []
        unique_shortfall_clip_names: list[str] = []
        duration_shortfall_scene_ids: list[str] = []
        duration_shortfall_clip_names: list[str] = []
        unresolved_scene_ids: list[str] = []
        policy_rejections = 0
        editorial_resolved_scenes = 0
        used_asset_keys: set[str] = set(preused_asset_keys or set())
        target_scenes = list(scenes) if scenes is not None else list(plan.scenes)

        provider_order = self._enabled_provider_order()
        provider_usage: dict[str, Any] = {}
        if self.config.enable_coverr_provider:
            provider_usage["coverr"] = self._refresh_coverr_usage_state()
        if self.config.enable_vecteezy_provider:
            provider_usage["vecteezy"] = self._refresh_vecteezy_usage_state()

        if not any(self._provider_is_configured(provider_name) for provider_name in provider_order):
            enabled_labels = ", ".join(self._enabled_provider_labels())
            if enabled_labels:
                message = f"No stock API keys configured for enabled providers ({enabled_labels})."
            else:
                message = "No stock providers are enabled."
            if self.config.require_external_assets:
                raise RuntimeError(message + " --require-external-assets is enabled.")
            self._warn(message + " Using generated placeholders.")

        if self.config.asset_keywords:
            self._log(f"Asset keyword constraint enabled: {', '.join(self.config.asset_keywords)}")

        for scene in target_scenes:
            editorial_right = self._resolve_editorial_scene_asset(scene)
            if editorial_right is not None:
                rights.append(editorial_right)
                resolved_image_scenes += 1
                editorial_resolved_scenes += 1
                continue

            queries = self._queries_for_scene(scene)
            scene_candidates = self._rank_scene_candidates(
                scene,
                provider_order=provider_order,
                query_cache=query_cache,
                ignore_global_keywords=scene.scene_id in (strict_query_override_scene_ids or set()),
            )
            preferred_candidate = (preferred_candidates or {}).get(scene.scene_id)
            if preferred_candidate is not None:
                scene_candidates = self._prioritize_preferred_candidate(
                    scene,
                    preferred_candidate=preferred_candidate,
                    scene_candidates=scene_candidates,
                )
            self._scene_asset_shortlists[scene.scene_id] = scene_candidates[: max(1, int(self.config.asset_shortlist_size))]
            resolved = False
            saw_candidates = bool(scene_candidates)
            scene_duplicate_rejections = 0
            scene_short_duration_rejections = 0
            preferred_candidate_key = self._asset_uniqueness_key(preferred_candidate) if preferred_candidate is not None else None
            for candidate in scene_candidates:
                unique_key = self._asset_uniqueness_key(candidate)
                if unique_key in used_asset_keys:
                    if preferred_candidate_key and unique_key == preferred_candidate_key:
                        self._log(
                            f"Preferred candidate rejected for {scene.scene_id}: asset is already used elsewhere in the project."
                        )
                    duplicate_candidate_rejections += 1
                    scene_duplicate_rejections += 1
                    continue

                policy_block_reason = self._candidate_policy_block_reason(candidate)
                if policy_block_reason is not None:
                    if preferred_candidate_key and unique_key == preferred_candidate_key:
                        self._log(
                            f"Preferred candidate rejected for {scene.scene_id}: {policy_block_reason}."
                        )
                    policy_rejections += 1
                    continue

                candidate_duration = self._candidate_duration_seconds(candidate)
                if candidate.media_type == "video" and self._duration_is_too_short_for_scene(candidate_duration, scene.seconds):
                    if preferred_candidate_key and unique_key == preferred_candidate_key:
                        self._log(
                            f"Preferred candidate rejected for {scene.scene_id}: "
                            f"{float(candidate_duration or 0.0):.1f}s is too short for a {float(scene.seconds):.1f}s scene."
                        )
                    short_duration_rejections += 1
                    scene_short_duration_rejections += 1
                    continue

                try:
                    self._increment_optimization_counter("asset_resolution", "download_attempts")
                    with self._timed_optimization_block("asset_resolution", "download_seconds"):
                        local_path, resolved_candidate = self._download_candidate_asset(candidate)
                    actual_duration = self._downloaded_asset_duration_seconds(
                        local_path,
                        scene.scene_id,
                        media_type=resolved_candidate.media_type,
                    )
                    if resolved_candidate.media_type == "video" and self._duration_is_too_short_for_scene(actual_duration, scene.seconds):
                        if preferred_candidate_key and unique_key == preferred_candidate_key:
                            self._log(
                                f"Preferred candidate rejected for {scene.scene_id} after download: "
                                f"{float(actual_duration or 0.0):.1f}s is too short for a {float(scene.seconds):.1f}s scene."
                            )
                        short_duration_rejections += 1
                        scene_short_duration_rejections += 1
                        self._log(
                            f"Rejected short asset for {scene.scene_id}: "
                            f"{local_path.name} is only {float(actual_duration or 0.0):.1f}s "
                            f"for a {float(scene.seconds):.1f}s scene."
                        )
                        try:
                            local_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        continue

                    scene.asset_path = str(local_path)
                    scene.asset_provider = resolved_candidate.source_platform or "unknown"
                    used_asset_keys.add(unique_key)
                    selected_candidate = replace(
                        resolved_candidate,
                        duration_seconds=actual_duration if actual_duration is not None else resolved_candidate.duration_seconds,
                    )
                    self._selected_assets_by_scene[scene.scene_id] = selected_candidate

                    right = AssetRight(
                        scene_id=scene.scene_id,
                        source_platform=resolved_candidate.source_platform or "unknown",
                        source_asset_id=resolved_candidate.source_asset_id,
                        source_url=resolved_candidate.source_url or resolved_candidate.download_url,
                        creator_name=resolved_candidate.creator_name,
                        creator_profile_url=resolved_candidate.creator_profile_url,
                        license_name=resolved_candidate.license_name,
                        license_url=resolved_candidate.license_url,
                        downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        local_path=str(local_path.resolve()),
                        sha256=self._file_sha256(local_path),
                        media_type=resolved_candidate.media_type,
                        width=resolved_candidate.width,
                        height=resolved_candidate.height,
                        duration_seconds=actual_duration if actual_duration is not None else resolved_candidate.duration_seconds,
                        restriction_flags=list(resolved_candidate.restriction_flags),
                        attribution_required=bool(resolved_candidate.attribution_required),
                        attribution_text=resolved_candidate.attribution_text,
                    )
                    rights.append(right)
                    self._increment_optimization_counter("asset_resolution", "download_successes")
                    if resolved_candidate.media_type == "image":
                        resolved_image_scenes += 1
                    else:
                        resolved_video_scenes += 1
                    resolved = True
                    break
                except Exception as exc:
                    download_failures += 1
                    self._log(
                        f"Asset download failed for {scene.scene_id} "
                        f"({candidate.source_platform}, {candidate.query or queries[0]}): {exc}"
                    )

            if not resolved:
                placeholder_scenes += 1
                unresolved_scene_ids.append(scene.scene_id)
                if saw_candidates and scene_duplicate_rejections > 0:
                    unique_shortfall_scene_ids.append(scene.scene_id)
                    unique_shortfall_clip_names.append(scene.clip_name)
                if saw_candidates and scene_short_duration_rejections > 0:
                    duration_shortfall_scene_ids.append(scene.scene_id)
                    duration_shortfall_clip_names.append(scene.clip_name)

        if self.config.enable_coverr_provider:
            if self._coverr_usage_state is not None:
                provider_usage["coverr"] = dict(self._coverr_usage_state)

        if self.config.enable_vecteezy_provider:
            if self._vecteezy_downloads_this_run > 0:
                provider_usage["vecteezy"] = self._refresh_vecteezy_usage_state()
            elif self._vecteezy_usage_state is not None:
                provider_usage["vecteezy"] = dict(self._vecteezy_usage_state)

        montage_scene_count = 0
        montage_asset_count = 0
        montage_download_failures = 0
        if rights:
            montage_scene_count, montage_asset_count, montage_download_failures = self._prepare_scene_image_montages(
                plan,
                rights,
            )

        asset_optimization_stats = self.optimization_stats.get("asset_resolution")
        if not isinstance(asset_optimization_stats, dict):
            asset_optimization_stats = {}
        self.asset_stats = {
            "resolved_scene_count": len(rights),
            "resolved_video_scene_count": resolved_video_scenes,
            "resolved_image_scene_count": resolved_image_scenes,
            "editorial_resolved_scene_count": editorial_resolved_scenes,
            "image_montage_scene_count": montage_scene_count,
            "image_montage_asset_count": montage_asset_count,
            "image_montage_download_failures": montage_download_failures,
            "placeholder_scene_count": placeholder_scenes,
            "download_failures": download_failures,
            "policy_rejections": policy_rejections,
            "duplicate_candidate_rejections": duplicate_candidate_rejections,
            "short_duration_rejections": short_duration_rejections,
            "unique_shortfall_count": len(unique_shortfall_scene_ids),
            "unique_shortfall_scene_ids": unique_shortfall_scene_ids,
            "unique_shortfall_clip_names": unique_shortfall_clip_names,
            "duration_shortfall_count": len(duration_shortfall_scene_ids),
            "duration_shortfall_scene_ids": duration_shortfall_scene_ids,
            "duration_shortfall_clip_names": duration_shortfall_clip_names,
            "unresolved_scene_ids": unresolved_scene_ids,
            "asset_keywords": list(self.config.asset_keywords),
            "enabled_asset_providers": self._enabled_provider_labels(),
            "allow_image_assets": bool(self._image_assets_enabled()),
            "allow_attribution_required_assets": bool(self.config.allow_attribution_required_assets),
            "asset_mode": self._normalized_asset_mode(),
            "image_motion_style": self._normalized_image_motion_style(),
            "provider_usage": provider_usage,
            "query_cache_hits": int(asset_optimization_stats.get("query_cache_hits") or 0),
            "query_cache_misses": int(asset_optimization_stats.get("query_cache_misses") or 0),
            "persistent_query_cache_hits": int(asset_optimization_stats.get("persistent_query_cache_hits") or 0),
            "persistent_query_cache_misses": int(asset_optimization_stats.get("persistent_query_cache_misses") or 0),
            "download_attempts": int(asset_optimization_stats.get("download_attempts") or 0),
            "download_successes": int(asset_optimization_stats.get("download_successes") or 0),
            "montage_download_attempts": int(asset_optimization_stats.get("montage_download_attempts") or 0),
            "montage_download_successes": int(asset_optimization_stats.get("montage_download_successes") or 0),
            "provider_retry_attempts": int(asset_optimization_stats.get("provider_retry_attempts") or 0),
        }

        if download_failures > 0:
            self._warn(
                f"{download_failures} asset downloads failed (network or provider timeout). "
                "Placeholders were used for affected scenes."
            )
        if montage_download_failures > 0:
            self._warn(
                f"{montage_download_failures} extra image montage downloads failed. "
                "Affected scenes fell back to fewer image components."
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

        if duration_shortfall_scene_ids:
            clip_hint = ", ".join(duration_shortfall_clip_names[:8])
            self._warn(
                "Some stock clips were rejected because they were too short for their scene duration. "
                f"Placeholders were used when no duration-safe clip was found. Affected clips: {clip_hint}"
            )

        if not rights:
            self._warn("No external assets resolved for this run. Final video uses generated placeholders only.")
        return rights

    def _build_internal_shot_card(self, shot: PlannedShot) -> AssetRight:
        shot_dir = self._shot_preview_dir(shot.shot_id)
        shot_dir.mkdir(parents=True, exist_ok=True)
        image_path = shot_dir / "internal-card.png"
        title_path = shot_dir / "internal-card-title.txt"
        body_path = shot_dir / "internal-card-body.txt"
        meta_path = shot_dir / "internal-card-meta.txt"

        title_text = self._wrap_bookend_text(shot.heading or shot.clip_name or "Explain")
        body_text = self._wrap_bookend_text(shot.key_info or shot.narration_text or shot.shot_objective or "Explain")
        meta_bits = list(shot.required_entities[:3]) or [normalize_shot_confidence(shot.match_confidence, "medium")]
        meta_text = " | ".join(meta_bits)
        self._write_text(title_path, title_text + "\n")
        self._write_text(body_path, body_text + "\n")
        self._write_text(meta_path, meta_text + "\n")

        title_file = self._escape_drawtext_path(title_path)
        body_file = self._escape_drawtext_path(body_path)
        meta_file = self._escape_drawtext_path(meta_path)
        draw_vf = (
            "drawbox=x=0:y=0:w=iw:h=ih:color=0x0f172a:t=fill,"
            "drawbox=x=iw*0.06:y=ih*0.11:w=iw*0.88:h=ih*0.78:color=0x111827:t=fill,"
            "drawbox=x=iw*0.06:y=ih*0.11:w=iw*0.88:h=4:color=0xeab308:t=fill,"
            f"drawtext=textfile='{title_file}':fontcolor=white:fontsize=46:x=w*0.10:y=h*0.18:"
            "line_spacing=10:borderw=4:bordercolor=black@0.75,"
            f"drawtext=textfile='{body_file}':fontcolor=0xe5e7eb:fontsize=30:x=w*0.10:y=h*0.40:"
            "line_spacing=8:borderw=3:bordercolor=black@0.70,"
            f"drawtext=textfile='{meta_file}':fontcolor=0xfde68a:fontsize=22:x=w*0.10:y=h*0.80"
        )
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x0f172a:s={self.config.width}x{self.config.height}:r={self.config.fps}",
            "-frames:v",
            "1",
            "-vf",
            draw_vf,
            str(image_path),
        ]
        result = self._run_command(command, timeout=180, check=False)
        if result.returncode != 0:
            fallback = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x111827:s={self.config.width}x{self.config.height}:r={self.config.fps}",
                "-frames:v",
                "1",
                str(image_path),
            ]
            fallback_result = self._run_command(fallback, timeout=120, check=False)
            if fallback_result.returncode != 0:
                raise RuntimeError(f"Failed to render internal shot card: {fallback_result.stderr.strip()}")

        shot.asset_path = str(image_path.resolve())
        shot.asset_provider = "internal-shot-card"
        shot.visual_strategy = "stock"
        shot.fallback_level = "internal-card"
        return AssetRight(
            scene_id=shot.shot_id,
            source_platform="internal-shot-card",
            source_asset_id=shot.shot_id,
            source_url="internal://shot-card",
            creator_name=None,
            creator_profile_url=None,
            license_name="Internal generated card",
            license_url=None,
            downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            local_path=str(image_path.resolve()),
            sha256=self._file_sha256(image_path),
            media_type="image",
            width=self.config.width,
            height=self.config.height,
            duration_seconds=None,
        )

    def _resolve_shot_assets(
        self,
        shot_plan: ShotPlan,
        *,
        preused_asset_keys: set[str] | None = None,
        preferred_candidates: dict[str, AssetCandidate] | None = None,
        persist_state: bool = True,
        strict_query_override_scene_ids: set[str] | None = None,
    ) -> list[AssetRight]:
        synthetic_scenes: list[Scene] = []
        rights: list[AssetRight] = []
        for shot in shot_plan.shots:
            if shot.visual_type in {"internal-card", "map-card", "timeline-card", "data-card"}:
                rights.append(self._build_internal_shot_card(shot))
                continue
            synthetic_scenes.append(
                Scene(
                    scene_id=shot.shot_id,
                    clip_name=f"{shot.clip_name}-shot-{shot.shot_index:02d}",
                    heading=shot.key_info or shot.heading,
                    voiceover=shot.narration_text,
                    search_terms=list(shot.search_queries),
                    seconds=float(shot.seconds),
                    source_refs=list(shot.source_refs),
                    visual_strategy=normalize_news_visual_strategy(shot.visual_strategy, "stock"),
                )
            )

        if synthetic_scenes:
            stock_plan = ScriptPlan(title=shot_plan.title, summary=shot_plan.summary, scenes=synthetic_scenes)
            stock_rights = self._resolve_assets(
                stock_plan,
                preused_asset_keys=preused_asset_keys,
                preferred_candidates=preferred_candidates,
                strict_query_override_scene_ids=strict_query_override_scene_ids,
            )
            rights.extend(stock_rights)
            scene_by_id = {scene.scene_id: scene for scene in stock_plan.scenes}
            for shot in shot_plan.shots:
                scene = scene_by_id.get(shot.shot_id)
                if scene is None:
                    continue
                shot.asset_path = scene.asset_path
                shot.asset_provider = scene.asset_provider
                self._shot_asset_shortlists[shot.shot_id] = list(self._scene_asset_shortlists.get(shot.shot_id, []))
                if shot.shot_id in self._selected_assets_by_scene:
                    self._selected_assets_by_shot[shot.shot_id] = self._selected_assets_by_scene[shot.shot_id]

        best_scores: dict[str, float] = {}
        for shot in shot_plan.shots:
            shortlist = self._shot_asset_shortlists.get(shot.shot_id, [])
            best_scores[shot.shot_id] = max((float(item.ranking_score) for item in shortlist), default=0.0)
            if shot.asset_path:
                continue
            rights.append(self._build_internal_shot_card(shot))

        for shot in shot_plan.shots:
            best_score = float(best_scores.get(shot.shot_id) or 0.0)
            if shot.required_entities and best_score < 4.0:
                shot.match_confidence = "low"
            elif best_score < 4.4:
                shot.match_confidence = "medium"
            else:
                shot.match_confidence = normalize_shot_confidence(shot.match_confidence, "medium")
            if shot.asset_provider == "internal-shot-card" and shot.fallback_level == "exact":
                shot.fallback_level = "internal-card"

        if persist_state:
            prior_state = self._load_json_state(self.paths["shot_review_state"])
            self._write_json(self.paths["shot_plan"], shot_plan.to_dict())
            self._write_json(self.paths["shot_review_state"], self._build_shot_review_state(shot_plan, prior_state))
        self.asset_stats["shot_count"] = len(shot_plan.shots)
        self.asset_stats["blocked_shot_count"] = sum(
            1 for shot in shot_plan.shots if normalize_shot_confidence(shot.match_confidence, "medium") == "low"
        )
        self.asset_stats["internal_card_shot_count"] = sum(1 for shot in shot_plan.shots if shot.asset_provider == "internal-shot-card")
        return rights

    def _write_shot_clip_catalog(self, shot_plan: ShotPlan, rights: list[AssetRight]) -> None:
        rights_by_shot: dict[str, AssetRight] = {item.scene_id: item for item in rights}
        editorial_sources = (
            (self._approved_editorial_sources or self._load_approved_editorial_sources())
            if self._news_mode_enabled()
            else []
        )
        editorial_by_id = {source.source_id: source for source in editorial_sources}
        clips: list[dict[str, Any]] = []
        for shot in shot_plan.shots:
            right = rights_by_shot.get(shot.shot_id)
            selected_candidate = self._selected_assets_by_shot.get(shot.shot_id)
            shortlist = self._shot_asset_shortlists.get(shot.shot_id, [])
            selected_key = self._asset_uniqueness_key(selected_candidate) if selected_candidate is not None else None
            candidates: list[dict[str, Any]] = []
            for candidate in shortlist:
                payload = candidate.to_dict()
                payload["selected"] = bool(selected_key and self._asset_uniqueness_key(candidate) == selected_key)
                candidates.append(payload)

            editorial_source_id = shot.source_refs[0] if shot.source_refs else None
            editorial_source = editorial_by_id.get(editorial_source_id) if editorial_source_id else None
            clips.append(
                {
                    "scene_id": shot.scene_id,
                    "shot_id": shot.shot_id,
                    "clip_name": f"{shot.clip_name}-shot-{shot.shot_index:02d}",
                    "heading": shot.heading,
                    "shot_objective": shot.shot_objective,
                    "key_info": shot.key_info,
                    "channel_vocabulary_key": self._channel_profile_key(),
                    "matched_channel_terms": list(shot.matched_channel_terms),
                    "seconds": round(float(shot.seconds), 3),
                    "search_terms": list(shot.search_queries),
                    "effective_search_queries": list(shot.effective_search_queries),
                    "required_entities": list(shot.required_entities),
                    "match_confidence": normalize_shot_confidence(shot.match_confidence, "medium"),
                    "fallback_level": shot.fallback_level,
                    "visual_type": shot.visual_type,
                    "visual_strategy": normalize_news_visual_strategy(shot.visual_strategy, "stock"),
                    "editorial_source_id": editorial_source_id,
                    "editorial_source_title": editorial_source.title if editorial_source is not None else None,
                    "editorial_source_publisher": editorial_source.publisher if editorial_source is not None else None,
                    "asset_provider": shot.asset_provider,
                    "asset_path": shot.asset_path,
                    "source_asset_id": right.source_asset_id if right else None,
                    "source_url": right.source_url if right else None,
                    "asset_media_type": right.media_type if right else self._media_type_from_path(Path(shot.asset_path)) if shot.asset_path else None,
                    "asset_width": right.width if right else None,
                    "asset_height": right.height if right else None,
                    "asset_duration_seconds": round(right.duration_seconds, 3) if right and right.duration_seconds is not None else None,
                    "license_name": right.license_name if right else None,
                    "license_url": right.license_url if right else None,
                    "attribution_required": bool(right.attribution_required) if right else False,
                    "attribution_text": right.attribution_text if right else None,
                    "restriction_flags": list(right.restriction_flags) if right else [],
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "preview_path": str(self._shot_preview_path(shot.shot_id).resolve()),
                }
            )
        payload = {
            "title": shot_plan.title,
            "summary": shot_plan.summary,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "asset_keywords": list(self.config.asset_keywords),
            "channel_vocabulary_key": self._channel_profile_key(),
            "clips": clips,
        }
        self._write_json(self.paths["clip_catalog"], payload)

    def _ensure_shot_previews(self, shot_plan: ShotPlan) -> None:
        preview_jobs: list[tuple[str, TimelineClip, Path, Path, Path]] = []
        for shot in shot_plan.shots:
            shot_dir = self._shot_preview_dir(shot.shot_id)
            shot_dir.mkdir(parents=True, exist_ok=True)
            preview_path = self._shot_preview_path(shot.shot_id)
            audio_path = self._shot_preview_audio_path(shot.shot_id)
            srt_path, ass_path = self._shot_preview_captions_path(shot.shot_id)

            sample = self.synthesize_scene_narration_preview(shot.shot_id, shot.narration_text)
            shutil.copy2(Path(sample["wav_path"]), audio_path)
            audio_seconds = max(0.3, self._media_duration(audio_path))
            single_plan = ScriptPlan(
                title=shot.heading,
                summary=shot.shot_objective,
                scenes=[
                    Scene(
                        scene_id=shot.shot_id,
                        clip_name=shot.clip_name,
                        heading=shot.heading,
                        voiceover=shot.narration_text,
                        search_terms=list(shot.search_queries),
                        seconds=audio_seconds,
                    )
                ],
            )
            captions = self._captions_heuristic(single_plan)
            self._write_srt(srt_path, captions)
            self._write_ass(ass_path, captions)
            clip = TimelineClip(
                scene_id=shot.shot_id,
                clip_name=f"{shot.clip_name}-shot-{shot.shot_index:02d}",
                start=0.0,
                end=audio_seconds,
                seconds=audio_seconds,
                source_path=shot.asset_path,
                heading=shot.heading,
                narration_start=0.0,
                narration_end=audio_seconds,
                visual_strategy=normalize_news_visual_strategy(shot.visual_strategy, "stock"),
                editorial_source_id=shot.source_refs[0] if shot.source_refs else None,
                shot_id=shot.shot_id,
                parent_scene_id=shot.scene_id,
                match_confidence=normalize_shot_confidence(shot.match_confidence, "medium"),
                fallback_level=shot.fallback_level,
            )
            preview_jobs.append((shot.shot_id, clip, audio_path, preview_path, ass_path))

        def render_preview_job(job: tuple[str, TimelineClip, Path, Path, Path]) -> None:
            shot_id, clip, audio_path, preview_path, ass_path = job
            self._render_video(
                [clip],
                audio_path,
                preview_path,
                captions_ass_path=ass_path,
                intro_seconds=0.0,
                outro_seconds=0.0,
                render_subdir=f"shot-{shot_id}",
                metrics_section="shot_preview",
            )

        if not preview_jobs:
            return

        preview_workers = min(4, len(preview_jobs))
        self._set_optimization_value("shot_preview", "render_workers", preview_workers)
        if preview_workers == 1:
            for job in preview_jobs:
                render_preview_job(job)
            return

        with ThreadPoolExecutor(max_workers=preview_workers) as executor:
            futures = [executor.submit(render_preview_job, job) for job in preview_jobs]
        for future in futures:
            future.result()

    def _montage_motion_profile(self, style: str | None = None) -> dict[str, float | str]:
        normalized_style = self._normalized_image_motion_style(style)
        profiles: dict[str, dict[str, float | str]] = {
            "static": {
                "segment_motion_style": "static",
                "crossfade_ratio": 0.04,
                "crossfade_min": 0.12,
                "crossfade_max": 0.2,
                "min_visible_seconds": 3.2,
            },
            "slow": {
                "segment_motion_style": "slow",
                "crossfade_ratio": 0.045,
                "crossfade_min": 0.14,
                "crossfade_max": 0.24,
                "min_visible_seconds": 2.8,
            },
            "balanced": {
                "segment_motion_style": "slow",
                "crossfade_ratio": 0.05,
                "crossfade_min": 0.16,
                "crossfade_max": 0.3,
                "min_visible_seconds": 2.5,
            },
            "fast": {
                "segment_motion_style": "balanced",
                "crossfade_ratio": 0.06,
                "crossfade_min": 0.2,
                "crossfade_max": 0.36,
                "min_visible_seconds": 2.1,
            },
        }
        return dict(profiles.get(normalized_style, profiles["slow"]))

    def _scene_montage_target_count(self, scene_seconds: float, *, style: str | None = None) -> int:
        duration = max(0.0, float(scene_seconds))
        normalized_style = self._normalized_image_motion_style(style)
        if normalized_style == "fast":
            if duration >= 8.5:
                return 3
            if duration >= 4.8:
                return 2
            return 1
        if normalized_style == "balanced":
            if duration >= 10.5:
                return 3
            if duration >= 6.2:
                return 2
            return 1
        if duration >= 10.5:
            return 2
        return 1

    def _prepare_scene_image_montages(self, plan: ScriptPlan, rights: list[AssetRight]) -> tuple[int, int, int]:
        rights_by_scene: dict[str, AssetRight] = {right.scene_id: right for right in rights}
        used_asset_keys: set[str] = {self._asset_uniqueness_key_from_right(right) for right in rights}
        self._scene_montage_assets = {}
        montage_scene_count = 0
        montage_asset_count = 0
        montage_download_failures = 0

        for scene in plan.scenes:
            right = rights_by_scene.get(scene.scene_id)
            if right is None or str(right.media_type or "").strip().lower() != "image":
                continue
            if right.source_platform == "editorial-source" or normalize_news_visual_strategy(scene.visual_strategy, "stock") != "stock":
                continue

            component_payloads: list[dict[str, Any]] = [self._asset_component_payload_from_right(right)]
            target_count = self._scene_montage_target_count(
                scene.seconds,
                style=self._normalized_image_motion_style(),
            )
            scene_keys: set[str] = {self._asset_uniqueness_key_from_right(right)}
            shortlist = self._scene_asset_shortlists.get(scene.scene_id, [])

            if target_count > 1:
                eligible_candidates: list[AssetCandidate] = []
                for candidate in shortlist:
                    if candidate.media_type != "image":
                        continue
                    unique_key = self._asset_uniqueness_key(candidate)
                    if unique_key in scene_keys or unique_key in used_asset_keys:
                        continue
                    policy_block_reason = self._candidate_policy_block_reason(candidate)
                    if policy_block_reason is not None:
                        continue
                    eligible_candidates.append(candidate)

                candidate_index = 0
                while len(component_payloads) < target_count and candidate_index < len(eligible_candidates):
                    batch: list[AssetCandidate] = []
                    remaining_vecteezy_downloads = self._vecteezy_estimated_remaining_downloads()
                    while len(batch) < 4 and candidate_index < len(eligible_candidates):
                        candidate = eligible_candidates[candidate_index]
                        candidate_index += 1
                        if candidate.source_platform == "vecteezy" and remaining_vecteezy_downloads is not None:
                            if remaining_vecteezy_downloads <= 0:
                                continue
                            remaining_vecteezy_downloads -= 1
                        batch.append(candidate)

                    if not batch:
                        continue

                    def download_montage_candidate(
                        candidate: AssetCandidate,
                    ) -> tuple[Path | None, AssetCandidate | None, Exception | None]:
                        session = self._new_http_session()
                        try:
                            self._increment_optimization_counter("asset_resolution", "montage_download_attempts")
                            with self._timed_optimization_block("asset_resolution", "montage_download_seconds"):
                                local_path, resolved_candidate = self._download_candidate_asset(candidate, session=session)
                            return local_path, resolved_candidate, None
                        except Exception as exc:
                            return None, None, exc
                        finally:
                            session.close()

                    with ThreadPoolExecutor(max_workers=min(4, len(batch))) as executor:
                        futures = [executor.submit(download_montage_candidate, candidate) for candidate in batch]

                    for candidate, future in zip(batch, futures):
                        local_path, resolved_candidate, error = future.result()
                        if error is not None or local_path is None or resolved_candidate is None:
                            montage_download_failures += 1
                            self._log(
                                f"Montage asset download failed for {scene.scene_id} "
                                f"({candidate.source_platform}, {candidate.query or 'shortlist'}): {error}"
                            )
                            continue

                        resolved_key = self._asset_uniqueness_key(resolved_candidate)
                        if resolved_key in scene_keys or resolved_key in used_asset_keys:
                            continue

                        self._increment_optimization_counter("asset_resolution", "montage_download_successes")
                        component_right = AssetRight(
                            scene_id=scene.scene_id,
                            source_platform=resolved_candidate.source_platform or "unknown",
                            source_asset_id=resolved_candidate.source_asset_id,
                            source_url=resolved_candidate.source_url or resolved_candidate.download_url,
                            creator_name=resolved_candidate.creator_name,
                            creator_profile_url=resolved_candidate.creator_profile_url,
                            license_name=resolved_candidate.license_name,
                            license_url=resolved_candidate.license_url,
                            downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                            local_path=str(local_path.resolve()),
                            sha256=self._file_sha256(local_path),
                            media_type=resolved_candidate.media_type,
                            width=resolved_candidate.width,
                            height=resolved_candidate.height,
                            duration_seconds=resolved_candidate.duration_seconds,
                            restriction_flags=list(resolved_candidate.restriction_flags),
                            attribution_required=bool(resolved_candidate.attribution_required),
                            attribution_text=resolved_candidate.attribution_text,
                        )
                        component_payloads.append(self._asset_component_payload_from_right(component_right))
                        scene_keys.add(resolved_key)
                        used_asset_keys.add(resolved_key)
                        if len(component_payloads) >= target_count:
                            break

            right.scene_components = component_payloads
            self._scene_montage_assets[scene.scene_id] = [dict(item) for item in component_payloads]
            montage_asset_count += len(component_payloads)
            if len(component_payloads) > 1:
                montage_scene_count += 1

        return montage_scene_count, montage_asset_count, montage_download_failures

    def _rank_scene_candidates(
        self,
        scene: Scene,
        *,
        provider_order: list[str],
        query_cache: dict[tuple[str, str], list[AssetCandidate]],
        ignore_global_keywords: bool = False,
        deprioritized_asset_keys: set[str] | None = None,
    ) -> list[AssetCandidate]:
        ranked_by_key: dict[str, AssetCandidate] = {}
        queries = self._queries_for_scene(scene, ignore_global_keywords=ignore_global_keywords)

        def collect_from_providers(provider_names: list[str], *, provider_index_offset: int) -> None:
            ordered_searches: list[tuple[int, int, str, str, tuple[str, str]]] = []
            pending_searches: list[tuple[str, str, tuple[str, str]]] = []
            for provider_offset, provider_name in enumerate(provider_names):
                if not self._provider_is_configured(provider_name):
                    continue

                provider_index = provider_index_offset + provider_offset
                for query_index, query in enumerate(queries):
                    cache_key = (provider_name, query.lower())
                    ordered_searches.append((provider_index, query_index, provider_name, query, cache_key))
                    if cache_key in query_cache:
                        self._increment_optimization_counter("asset_resolution", "query_cache_hits")
                        continue

                    candidates = query_cache.get(cache_key)
                    if candidates is None:
                        self._increment_optimization_counter("asset_resolution", "query_cache_misses")
                        cached_candidates = self._load_persistent_query_cache(provider_name, query)
                        if cached_candidates is not None:
                            self._increment_optimization_counter("asset_resolution", "persistent_query_cache_hits")
                            candidates = list(cached_candidates)
                            query_cache[cache_key] = list(candidates)
                        else:
                            self._increment_optimization_counter("asset_resolution", "persistent_query_cache_misses")
                            pending_searches.append((provider_name, query, cache_key))

            def run_search_task(
                provider_name: str,
                query: str,
            ) -> tuple[list[AssetCandidate], float, Exception | None]:
                session = self._new_http_session()
                started_at = time.perf_counter()
                try:
                    candidates = self._search_provider_candidates(provider_name, query, session=session)
                    return list(candidates), time.perf_counter() - started_at, None
                except Exception as exc:
                    return [], time.perf_counter() - started_at, exc
                finally:
                    session.close()

            if pending_searches:
                parallel_pending = [
                    (provider_name, query, cache_key)
                    for provider_name, query, cache_key in pending_searches
                    if provider_name != "coverr"
                ]
                serial_pending = [
                    (provider_name, query, cache_key)
                    for provider_name, query, cache_key in pending_searches
                    if provider_name == "coverr"
                ]

                future_entries = []
                if parallel_pending:
                    with ThreadPoolExecutor(max_workers=min(4, len(parallel_pending))) as executor:
                        future_entries.extend(
                            [
                                (provider_name, query, cache_key, executor.submit(run_search_task, provider_name, query))
                                for provider_name, query, cache_key in parallel_pending
                            ]
                        )

                for provider_name, query, cache_key in serial_pending:
                    candidates, elapsed_seconds, error = run_search_task(provider_name, query)
                    self._record_optimization_time("asset_resolution", "search_seconds", elapsed_seconds)
                    if error is not None:
                        self._log(f"{provider_name} search failed for {scene.scene_id} ({query}): {error}")
                        query_cache[cache_key] = []
                        continue
                    query_cache[cache_key] = list(candidates)
                    self._write_persistent_query_cache(provider_name, query, list(candidates))

                for provider_name, query, cache_key, future in future_entries:
                    candidates, elapsed_seconds, error = future.result()
                    self._record_optimization_time("asset_resolution", "search_seconds", elapsed_seconds)
                    if error is not None:
                        self._log(f"{provider_name} search failed for {scene.scene_id} ({query}): {error}")
                        query_cache[cache_key] = []
                        continue
                    query_cache[cache_key] = list(candidates)
                    self._write_persistent_query_cache(provider_name, query, list(candidates))

            for provider_index, query_index, _, query, cache_key in ordered_searches:
                for base_candidate in query_cache.get(cache_key, []):
                    quality_score = self._candidate_quality_score(base_candidate, scene)
                    ranking_score = self._candidate_ranking_score(
                        base_candidate,
                        scene,
                        quality_score=quality_score,
                        provider_rank=provider_index,
                        query_rank=query_index,
                    )
                    ranked_candidate = replace(
                        base_candidate,
                        query=query,
                        quality_score=quality_score,
                        ranking_score=ranking_score,
                    )
                    unique_key = self._asset_uniqueness_key(ranked_candidate)
                    existing = ranked_by_key.get(unique_key)
                    if existing is None or ranked_candidate.ranking_score > existing.ranking_score:
                        ranked_by_key[unique_key] = ranked_candidate

        primary_providers = self._primary_provider_order(provider_order)
        fallback_providers = self._fallback_provider_order(provider_order)
        collect_from_providers(primary_providers, provider_index_offset=0)

        current_candidates = list(ranked_by_key.values())
        if fallback_providers and self._shortlist_needs_fallback(current_candidates):
            collect_from_providers(fallback_providers, provider_index_offset=len(primary_providers))

        ordered_candidates = self._sort_candidates(list(ranked_by_key.values()))
        lowered_deprioritized_keys = {
            str(key).strip().lower()
            for key in (deprioritized_asset_keys or set())
            if str(key).strip()
        }
        if not lowered_deprioritized_keys:
            return ordered_candidates

        preferred: list[AssetCandidate] = []
        deprioritized: list[AssetCandidate] = []
        for candidate in ordered_candidates:
            unique_key = self._asset_uniqueness_key(candidate).strip().lower()
            if unique_key and unique_key in lowered_deprioritized_keys:
                deprioritized.append(candidate)
            else:
                preferred.append(candidate)
        return [*preferred, *deprioritized]

    def _shortlist_needs_fallback(self, candidates: list[AssetCandidate]) -> bool:
        if not candidates:
            return True

        asset_mode = self._normalized_asset_mode()
        desired_candidates = max(2, min(3, int(self.config.asset_shortlist_size)))
        if asset_mode in {"prefer-images", "images-only"}:
            image_candidates = [candidate for candidate in candidates if candidate.media_type == "image"]
            if len(image_candidates) < desired_candidates:
                return True

            best_image_score = max((float(candidate.ranking_score) for candidate in image_candidates), default=0.0)
            threshold = 3.4 if asset_mode == "images-only" else 3.9
            return best_image_score < threshold

        video_candidates = [candidate for candidate in candidates if candidate.media_type == "video"]
        if len(video_candidates) < desired_candidates:
            return True

        best_video_score = max((float(candidate.ranking_score) for candidate in video_candidates), default=0.0)
        threshold = 4.0 if asset_mode == "balanced" else 4.2
        return best_video_score < threshold

    def _prioritize_preferred_candidate(
        self,
        scene: Scene,
        *,
        preferred_candidate: AssetCandidate,
        scene_candidates: list[AssetCandidate],
    ) -> list[AssetCandidate]:
        quality_score = self._candidate_quality_score(preferred_candidate, scene)
        ranking_score = self._candidate_ranking_score(
            preferred_candidate,
            scene,
            quality_score=quality_score,
            provider_rank=-1,
            query_rank=-1,
        )
        pinned_candidate = replace(
            preferred_candidate,
            query=preferred_candidate.query or "selected-shortlist",
            quality_score=quality_score,
            ranking_score=max(float(preferred_candidate.ranking_score), ranking_score + 1.0),
        )
        preferred_key = self._asset_uniqueness_key(pinned_candidate)
        remaining = [candidate for candidate in scene_candidates if self._asset_uniqueness_key(candidate) != preferred_key]
        return [pinned_candidate, *remaining]

    def _search_provider_candidates(
        self,
        provider_name: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []
        asset_mode = self._normalized_asset_mode()
        image_assets_enabled = self._image_assets_enabled()
        image_only = asset_mode == "images-only"
        if provider_name == "pexels":
            api_key = self._coerce_str_or_none(self.config.pexels_api_key)
            if not api_key:
                return []
            if not image_only:
                candidates.extend(self._search_pexels_videos(api_key, query=query, session=session))
            if image_assets_enabled:
                candidates.extend(self._search_pexels_images(api_key, query=query, session=session))
        elif provider_name == "pixabay":
            api_key = self._coerce_str_or_none(self.config.pixabay_api_key)
            if not api_key:
                return []
            if not image_only:
                candidates.extend(self._search_pixabay_videos(api_key, query=query, session=session))
            if image_assets_enabled:
                candidates.extend(self._search_pixabay_images(api_key, query=query, session=session))
        elif provider_name == "coverr":
            api_key = self._coerce_str_or_none(self.config.coverr_api_key)
            if not api_key:
                return []
            if image_only:
                return []
            remaining_requests = self._coverr_estimated_remaining_requests()
            if remaining_requests is not None and remaining_requests <= 0:
                return []
            candidates.extend(self._search_coverr_videos(api_key, query=query, session=session))
        elif provider_name == "vecteezy":
            api_key = self._coerce_str_or_none(self.config.vecteezy_api_key)
            account_id = self._coerce_str_or_none(self.config.vecteezy_account_id)
            if not api_key or not account_id:
                return []
            if not image_only:
                candidates.extend(self._search_vecteezy_videos(account_id, api_key, query=query, session=session))
            if image_assets_enabled:
                candidates.extend(self._search_vecteezy_images(account_id, api_key, query=query, session=session))
        return candidates

    def _candidate_quality_score(self, candidate: AssetCandidate, scene: Scene) -> float:
        score = 0.0
        asset_mode = self._normalized_asset_mode()
        query_context = self._scene_query_context(scene)
        matched_terms = [
            str(item).strip()
            for item in query_context.get("matched_terms") or []
            if str(item).strip()
        ]
        vocabulary = self._channel_visual_vocabulary()

        if candidate.media_type == "video":
            score += 2.2
            if asset_mode == "balanced":
                score -= 0.45
            elif asset_mode == "prefer-images":
                score -= 1.1
            elif asset_mode == "images-only":
                score -= 6.0
        elif candidate.media_type == "image":
            score += 0.65
            if asset_mode == "balanced":
                score += 0.45
            elif asset_mode == "prefer-images":
                score += 1.25
            elif asset_mode == "images-only":
                score += 2.0

        if candidate.width and candidate.height and candidate.width > 0 and candidate.height > 0:
            pixels = float(candidate.width * candidate.height)
            target_pixels = float(max(1, self.config.width * self.config.height))
            score += min(2.25, math.log2(max(1.0, pixels / target_pixels) + 1.0))

            target_ratio = float(self.config.width) / float(max(1, self.config.height))
            candidate_ratio = float(candidate.width) / float(max(1, candidate.height))
            ratio_gap = abs(math.log(max(candidate_ratio, 0.01) / max(target_ratio, 0.01)))
            score += max(0.0, 1.35 - (ratio_gap * 2.3))

        duration = self._candidate_duration_seconds(candidate)
        if candidate.media_type == "video":
            if duration is None:
                score += 0.15
            elif self._duration_is_too_short_for_scene(duration, scene.seconds):
                score -= 3.0
            else:
                overflow = max(0.0, duration - float(scene.seconds))
                score += 1.8
                score += max(0.0, 0.9 - min(overflow, 24.0) / 8.0)
        else:
            score += max(0.0, 0.45 - min(float(scene.seconds), 12.0) / 40.0)

        desired_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", f"{scene.heading} {' '.join(scene.search_terms)}")
            if token and token.lower() not in FUNCTION_WORDS
        }
        candidate_text = " ".join(
            part
            for part in (
                candidate.description or "",
                candidate.query or "",
                candidate.source_url or "",
            )
            if str(part).strip()
        ).lower()
        matched_tokens = [token for token in desired_tokens if token in candidate_text]
        score += min(2.2, float(len(matched_tokens)) * 0.4)
        if desired_tokens and not matched_tokens:
            score -= 0.65
        if matched_terms:
            matched_vocab_terms = [
                term
                for term in matched_terms
                if normalize_match_text(term) and normalize_match_text(term) in normalize_match_text(candidate_text)
            ]
            score += min(2.6, float(len(matched_vocab_terms)) * 0.65)
        if vocabulary is not None:
            normalized_candidate = f" {normalize_match_text(candidate_text)} "
            negative_hits = 0
            for raw_term in [*vocabulary.negative_terms, *vocabulary.negative_aliases]:
                normalized_term = normalize_match_text(raw_term)
                if not normalized_term:
                    continue
                if f" {normalized_term} " in normalized_candidate:
                    negative_hits += 1
            if negative_hits:
                score -= min(2.4, float(negative_hits) * 0.9)

        return score

    def _candidate_ranking_score(
        self,
        candidate: AssetCandidate,
        scene: Scene,
        *,
        quality_score: float,
        provider_rank: int,
        query_rank: int,
    ) -> float:
        score = float(quality_score)
        score += max(0.0, 0.7 - (query_rank * 0.2))
        score += max(0.0, 0.2 - (provider_rank * 0.05))
        if candidate.source_asset_id:
            score += 0.05
        query_context = self._scene_query_context(scene)
        matched_terms = [
            str(item).strip()
            for item in query_context.get("matched_terms") or []
            if str(item).strip()
        ]
        desired_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", f"{scene.heading} {' '.join(scene.search_terms)}")
            if token and token.lower() not in FUNCTION_WORDS
        }
        candidate_text = " ".join(
            part
            for part in (
                candidate.description or "",
                candidate.query or "",
                candidate.source_url or "",
            )
            if str(part).strip()
        ).lower()
        exact_hits = sum(1 for token in desired_tokens if token in candidate_text)
        score += min(1.5, float(exact_hits) * 0.25)
        if matched_terms:
            normalized_candidate = normalize_match_text(candidate_text)
            vocab_hits = sum(1 for term in matched_terms if normalize_match_text(term) in normalized_candidate)
            score += min(1.8, float(vocab_hits) * 0.45)
        return score

    def _sort_candidates(self, candidates: list[AssetCandidate]) -> list[AssetCandidate]:
        return sorted(
            candidates,
            key=lambda item: (
                -float(item.ranking_score),
                -float(item.quality_score),
                item.source_platform,
                item.media_type,
                item.source_asset_id or "",
                item.download_url,
            ),
        )

    def _candidate_duration_seconds(self, candidate: AssetCandidate) -> float | None:
        raw_value = candidate.duration_seconds
        if raw_value is None:
            return None
        try:
            duration = float(raw_value)
        except (TypeError, ValueError):
            return None
        if duration <= 0.0:
            return None
        return duration

    def _duration_is_too_short_for_scene(self, duration_seconds: float | None, scene_seconds: float) -> bool:
        if duration_seconds is None:
            return False
        required = max(0.3, float(scene_seconds))
        return duration_seconds + 0.05 < required

    def _downloaded_asset_duration_seconds(
        self,
        local_path: Path,
        scene_id: str,
        *,
        media_type: str | None = None,
    ) -> float | None:
        resolved_media_type = (media_type or self._media_type_from_path(local_path)).strip().lower()
        if resolved_media_type == "image":
            return None
        try:
            duration = self._media_duration(local_path)
        except Exception as exc:
            self._log(f"Could not probe downloaded asset duration for {scene_id}: {exc}")
            return None
        if duration <= 0.0:
            return None
        return duration

    def _asset_uniqueness_key(self, candidate: AssetCandidate | dict[str, Any]) -> str:
        if isinstance(candidate, AssetCandidate):
            platform = candidate.source_platform.strip().lower() or "unknown"
            source_asset_id = (candidate.source_asset_id or "").strip()
            source_url = (candidate.source_url or "").strip().lower()
            download_url = candidate.download_url.strip().lower()
            fallback_payload: dict[str, Any] = candidate.to_dict()
        else:
            platform = str(candidate.get("source_platform") or "unknown").strip().lower()
            source_asset_id = str(candidate.get("source_asset_id") or "").strip()
            source_url = str(candidate.get("source_url") or "").strip().lower()
            download_url = str(candidate.get("download_url") or "").strip().lower()
            fallback_payload = candidate

        if source_asset_id:
            return f"{platform}:id:{source_asset_id}"

        if source_url:
            return f"{platform}:url:{source_url}"

        if download_url:
            return f"{platform}:download:{download_url}"

        fallback = json.dumps(fallback_payload, sort_keys=True, ensure_ascii=True)
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

    def _asset_component_payload_from_right(self, right: AssetRight) -> dict[str, Any]:
        return {
            "source_platform": right.source_platform,
            "source_asset_id": right.source_asset_id,
            "source_url": right.source_url,
            "creator_name": right.creator_name,
            "creator_profile_url": right.creator_profile_url,
            "license_name": right.license_name,
            "license_url": right.license_url,
            "downloaded_at": right.downloaded_at,
            "local_path": right.local_path,
            "sha256": right.sha256,
            "media_type": right.media_type,
            "width": right.width,
            "height": right.height,
            "duration_seconds": round(right.duration_seconds, 3) if right.duration_seconds is not None else None,
            "restriction_flags": list(right.restriction_flags),
            "attribution_required": bool(right.attribution_required),
            "attribution_text": right.attribution_text,
        }

    def _asset_right_from_component_payload(self, scene_id: str, payload: dict[str, Any]) -> AssetRight | None:
        source_platform = str(payload.get("source_platform") or "").strip()
        source_url = str(payload.get("source_url") or "").strip()
        local_path = str(payload.get("local_path") or "").strip()
        sha256 = str(payload.get("sha256") or "").strip()
        downloaded_at = str(payload.get("downloaded_at") or "").strip()
        if not (source_platform and source_url and local_path and sha256):
            return None

        restriction_flags_raw = payload.get("restriction_flags")
        if isinstance(restriction_flags_raw, list):
            restriction_flags = [str(flag).strip() for flag in restriction_flags_raw if str(flag).strip()]
        else:
            restriction_flags = []

        return AssetRight(
            scene_id=scene_id,
            source_platform=source_platform,
            source_asset_id=self._coerce_str_or_none(payload.get("source_asset_id")),
            source_url=source_url,
            creator_name=self._coerce_str_or_none(payload.get("creator_name")),
            creator_profile_url=self._coerce_str_or_none(payload.get("creator_profile_url")),
            license_name=self._coerce_str_or_none(payload.get("license_name")),
            license_url=self._coerce_str_or_none(payload.get("license_url")),
            downloaded_at=downloaded_at,
            local_path=local_path,
            sha256=sha256,
            media_type=self._coerce_str_or_none(payload.get("media_type")),
            width=self._coerce_optional_int(payload.get("width")),
            height=self._coerce_optional_int(payload.get("height")),
            duration_seconds=self._coerce_optional_float(payload.get("duration_seconds")),
            restriction_flags=restriction_flags,
            attribution_required=bool(payload.get("attribution_required", False)),
            attribution_text=self._coerce_str_or_none(payload.get("attribution_text")),
        )

    def _iter_right_credit_records(self, rights: list[AssetRight]) -> list[AssetRight]:
        records: list[AssetRight] = []
        for right in rights:
            records.append(right)
            for payload in right.scene_components:
                if not isinstance(payload, dict):
                    continue
                component = self._asset_right_from_component_payload(right.scene_id, payload)
                if component is not None:
                    records.append(component)
        return records

    def _stable_rotate_candidates(self, candidates: list[AssetCandidate], seed: str) -> list[AssetCandidate]:
        if not candidates:
            return []

        ordered = sorted(
            candidates,
            key=lambda item: (
                item.source_asset_id or "",
                item.download_url,
            ),
        )

        if len(ordered) <= 1:
            return ordered

        pivot = self._stable_pivot(seed, len(ordered))
        return ordered[pivot:] + ordered[:pivot]

    def _search_pexels_videos(
        self,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = f"https://api.pexels.com/videos/search?query={quote_plus(query)}&orientation=landscape&per_page=20"
        response = self._http_get_with_retries(
            url,
            headers={"Authorization": api_key},
            timeout=(5, 15),
            session=session,
        )
        if response.status_code != 200:
            return []

        payload = response.json()
        videos = payload.get("videos")
        if not isinstance(videos, list) or not videos:
            return []

        candidates: list[AssetCandidate] = []
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
                AssetCandidate(
                    source_platform="pexels",
                    source_asset_id=source_asset_id or None,
                    media_type="video",
                    download_url=str(selected.get("link")),
                    preview_url=str(best.get("image") or "") or None,
                    duration_seconds=self._coerce_optional_float(best.get("duration")),
                    source_url=f"https://www.pexels.com/video/{best.get('id')}/",
                    creator_name=(str(user.get("name") or "").strip() or None),
                    creator_profile_url=(str(user.get("url") or "").strip() or None),
                    license_name="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    width=self._coerce_optional_int(selected.get("width") or best.get("width")),
                    height=self._coerce_optional_int(selected.get("height") or best.get("height")),
                    restriction_flags=[],
                    attribution_required=False,
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"pexels:{query.lower()}")

    def _search_pexels_images(
        self,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = f"https://api.pexels.com/v1/search?query={quote_plus(query)}&orientation=landscape&per_page=20"
        response = self._http_get_with_retries(
            url,
            headers={"Authorization": api_key},
            timeout=(5, 15),
            session=session,
        )
        if response.status_code != 200:
            return []

        payload = response.json()
        photos = payload.get("photos")
        if not isinstance(photos, list) or not photos:
            return []

        candidates: list[AssetCandidate] = []
        seen: set[str] = set()
        for best in photos:
            if not isinstance(best, dict):
                continue

            src = best.get("src")
            if not isinstance(src, dict):
                continue

            download_url = str(src.get("large2x") or src.get("large") or src.get("original") or "").strip()
            if not download_url:
                continue

            source_asset_id = str(best.get("id") or "")
            if source_asset_id and source_asset_id in seen:
                continue
            if source_asset_id:
                seen.add(source_asset_id)

            candidates.append(
                AssetCandidate(
                    source_platform="pexels",
                    source_asset_id=source_asset_id or None,
                    media_type="image",
                    download_url=download_url,
                    preview_url=(str(src.get("medium") or src.get("small") or "").strip() or None),
                    source_url=str(best.get("url") or ""),
                    creator_name=(str(best.get("photographer") or "").strip() or None),
                    creator_profile_url=(str(best.get("photographer_url") or "").strip() or None),
                    license_name="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    description=(str(best.get("alt") or "").strip() or None),
                    width=self._coerce_optional_int(best.get("width")),
                    height=self._coerce_optional_int(best.get("height")),
                    restriction_flags=[],
                    attribution_required=False,
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"pexels-images:{query.lower()}")

    def _search_pixabay_videos(
        self,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = (
            "https://pixabay.com/api/videos/"
            f"?key={quote_plus(api_key)}&q={quote_plus(query)}&safesearch=true&per_page=20"
        )
        response = self._http_get_with_retries(url, timeout=(5, 15), session=session)
        if response.status_code != 200:
            return []

        payload = response.json()
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            return []

        candidates: list[AssetCandidate] = []
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

            selected_width = None
            selected_height = None
            for key in quality_order:
                block = videos.get(key)
                if isinstance(block, dict) and str(block.get("url") or "") == selected_url:
                    selected_width = self._coerce_optional_int(block.get("width"))
                    selected_height = self._coerce_optional_int(block.get("height"))
                    break

            candidates.append(
                AssetCandidate(
                    source_platform="pixabay",
                    source_asset_id=source_asset_id or None,
                    media_type="video",
                    download_url=selected_url,
                    duration_seconds=self._coerce_optional_float(best.get("duration")),
                    source_url=str(best.get("pageURL") or ""),
                    creator_name=(str(best.get("user") or "").strip() or None),
                    creator_profile_url=None,
                    license_name="Pixabay License",
                    license_url="https://pixabay.com/service/license/",
                    description=(str(best.get("tags") or "").strip() or None),
                    width=selected_width,
                    height=selected_height,
                    restriction_flags=[],
                    attribution_required=False,
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"pixabay:{query.lower()}")

    def _search_pixabay_images(
        self,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = (
            "https://pixabay.com/api/"
            f"?key={quote_plus(api_key)}&q={quote_plus(query)}&image_type=photo&orientation=horizontal"
            "&safesearch=true&per_page=20"
        )
        response = self._http_get_with_retries(url, timeout=(5, 15), session=session)
        if response.status_code != 200:
            return []

        payload = response.json()
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            return []

        candidates: list[AssetCandidate] = []
        seen: set[str] = set()

        for best in hits:
            if not isinstance(best, dict):
                continue

            download_url = str(best.get("largeImageURL") or best.get("webformatURL") or "").strip()
            if not download_url:
                continue

            source_asset_id = str(best.get("id") or "")
            if source_asset_id and source_asset_id in seen:
                continue
            if source_asset_id:
                seen.add(source_asset_id)

            candidates.append(
                AssetCandidate(
                    source_platform="pixabay",
                    source_asset_id=source_asset_id or None,
                    media_type="image",
                    download_url=download_url,
                    preview_url=(str(best.get("webformatURL") or "").strip() or None),
                    source_url=str(best.get("pageURL") or ""),
                    creator_name=(str(best.get("user") or "").strip() or None),
                    creator_profile_url=None,
                    license_name="Pixabay License",
                    license_url="https://pixabay.com/service/license/",
                    description=(str(best.get("tags") or "").strip() or None),
                    width=self._coerce_optional_int(best.get("imageWidth")),
                    height=self._coerce_optional_int(best.get("imageHeight")),
                    restriction_flags=[],
                    attribution_required=False,
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"pixabay-images:{query.lower()}")

    def _search_coverr_videos(
        self,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = "https://api.coverr.co/videos"
        response = self._http_get_with_retries(
            url,
            headers=self._coverr_headers(api_key),
            params={
                "query": query,
                "page_size": 20,
                "urls": "true",
            },
            timeout=(5, 15),
            session=session,
        )
        self._record_coverr_request_event(endpoint="videos", query=query)
        if response.status_code != 200:
            return []

        payload = response.json()
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            return []

        candidates: list[AssetCandidate] = []
        seen: set[str] = set()
        for best in hits:
            if not isinstance(best, dict):
                continue

            source_asset_id = str(best.get("id") or best.get("video_id") or "").strip()
            if not source_asset_id or source_asset_id in seen:
                continue

            urls = best.get("urls")
            if not isinstance(urls, dict):
                continue

            download_url = self._coerce_str_or_none(urls.get("mp4_download")) or self._coerce_str_or_none(urls.get("mp4"))
            if not download_url:
                continue

            seen.add(source_asset_id)
            title = self._coerce_str_or_none(best.get("title")) or f"Coverr asset {source_asset_id}"
            description = self._coerce_str_or_none(best.get("description"))
            if not description:
                raw_tags = best.get("tags")
                if isinstance(raw_tags, list):
                    description = ", ".join(str(item).strip() for item in raw_tags if str(item).strip()) or None

            candidates.append(
                AssetCandidate(
                    source_platform="coverr",
                    source_asset_id=source_asset_id,
                    media_type="video",
                    download_url=download_url,
                    preview_url=(
                        self._coerce_str_or_none(urls.get("mp4_preview"))
                        or self._coerce_str_or_none(best.get("poster"))
                        or self._coerce_str_or_none(best.get(_COVERR_PREVIEW_FALLBACK_KEY))
                    ),
                    source_url=f"https://api.coverr.co/videos/{source_asset_id}",
                    license_name="Coverr License",
                    license_url="https://coverr.co/license",
                    description=description or title,
                    width=self._coerce_optional_int(best.get("max_width")),
                    height=self._coerce_optional_int(best.get("max_height")),
                    duration_seconds=self._coerce_optional_float(best.get("duration")),
                    restriction_flags=[
                        "experimental-provider",
                        "demo-hourly-request-cap",
                        "api-branding-required",
                    ],
                    attribution_required=True,
                    attribution_text=(
                        f"Coverr | {title} | Coverr API usage and license notes should be reflected in the video "
                        "description or publishing credits."
                    ),
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"coverr:{query.lower()}")

    def _search_vecteezy_videos(
        self,
        account_id: str,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = (
            f"https://api.vecteezy.com/v2/{quote_plus(account_id)}/resources"
            f"?term={quote_plus(query)}&content_type=video&license_type=commercial"
            "&family_friendly=true&sort_by=relevance&per_page=20"
        )
        response = self._http_get_with_retries(
            url,
            headers=self._vecteezy_headers(api_key),
            timeout=(5, 15),
            session=session,
        )
        if response.status_code != 200:
            return []

        payload = response.json()
        resources = payload.get("resources")
        if not isinstance(resources, list) or not resources:
            return []

        candidates: list[AssetCandidate] = []
        seen: set[str] = set()
        for best in resources:
            if not isinstance(best, dict):
                continue

            source_asset_id = str(best.get("id") or "").strip()
            if not source_asset_id or source_asset_id in seen:
                continue

            file_metadata = best.get("file_metadata")
            if not isinstance(file_metadata, dict):
                continue

            available_file_types = file_metadata.get("available_file_types")
            download_extension = None
            if isinstance(available_file_types, list):
                for raw_file_type in available_file_types:
                    if not isinstance(raw_file_type, dict):
                        continue
                    extension = str(raw_file_type.get("extension") or "").strip().lower()
                    if extension:
                        download_extension = "." + extension.lstrip(".")
                        break

            available_sizes = file_metadata.get("available_download_sizes")
            best_size: dict[str, Any] | None = None
            if isinstance(available_sizes, list):
                size_candidates = [item for item in available_sizes if isinstance(item, dict)]
                if size_candidates:
                    best_size = max(
                        size_candidates,
                        key=lambda item: (
                            int(item.get("width") or 0) * int(item.get("height") or 0),
                            int(item.get("width") or 0),
                        ),
                    )

            preview_url = self._coerce_str_or_none(best.get("preview_url")) or self._coerce_str_or_none(
                best.get(_VECTEEZY_PREVIEW_FALLBACK_KEY)
            )
            seen.add(source_asset_id)
            candidates.append(
                AssetCandidate(
                    source_platform="vecteezy",
                    source_asset_id=source_asset_id,
                    media_type="video",
                    download_url=f"vecteezy://resource/{source_asset_id}",
                    preview_url=preview_url,
                    source_url=f"https://api.vecteezy.com/v2/{account_id}/resources/{source_asset_id}",
                    license_name="Vecteezy Free License",
                    license_url="https://www.vecteezy.com/licensing-agreement",
                    description=self._coerce_str_or_none(best.get("title")),
                    width=self._coerce_optional_int(best_size.get("width") if isinstance(best_size, dict) else None),
                    height=self._coerce_optional_int(best_size.get("height") if isinstance(best_size, dict) else None),
                    download_extension=download_extension,
                    restriction_flags=[
                        "experimental-provider",
                        "monthly-download-cap",
                        "free-license-production-budget-cap",
                    ],
                    attribution_required=True,
                    attribution_text=(
                        f"Vecteezy | {self._coerce_str_or_none(best.get('title')) or source_asset_id} | "
                        "Attribution required under the Vecteezy free license."
                    ),
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"vecteezy:{query.lower()}")

    def _search_vecteezy_images(
        self,
        account_id: str,
        api_key: str,
        query: str,
        *,
        session: requests.Session | None = None,
    ) -> list[AssetCandidate]:
        url = (
            f"https://api.vecteezy.com/v2/{quote_plus(account_id)}/resources"
            f"?term={quote_plus(query)}&content_type=photo&license_type=commercial"
            "&orientation=horizontal&family_friendly=true&sort_by=relevance&per_page=20"
        )
        response = self._http_get_with_retries(
            url,
            headers=self._vecteezy_headers(api_key),
            timeout=(5, 15),
            session=session,
        )
        if response.status_code != 200:
            return []

        payload = response.json()
        resources = payload.get("resources")
        if not isinstance(resources, list) or not resources:
            return []

        candidates: list[AssetCandidate] = []
        seen: set[str] = set()
        for best in resources:
            if not isinstance(best, dict):
                continue

            source_asset_id = str(best.get("id") or "").strip()
            if not source_asset_id or source_asset_id in seen:
                continue

            file_metadata = best.get("file_metadata")
            if not isinstance(file_metadata, dict):
                continue

            available_file_types = file_metadata.get("available_file_types")
            download_extension = None
            if isinstance(available_file_types, list):
                for raw_file_type in available_file_types:
                    if not isinstance(raw_file_type, dict):
                        continue
                    extension = str(raw_file_type.get("extension") or "").strip().lower()
                    if extension:
                        download_extension = "." + extension.lstrip(".")
                        break

            available_sizes = file_metadata.get("available_download_sizes")
            best_size: dict[str, Any] | None = None
            if isinstance(available_sizes, list):
                size_candidates = [item for item in available_sizes if isinstance(item, dict)]
                if size_candidates:
                    best_size = max(
                        size_candidates,
                        key=lambda item: (
                            int(item.get("width") or 0) * int(item.get("height") or 0),
                            int(item.get("width") or 0),
                        ),
                    )

            preview_url = self._coerce_str_or_none(best.get("preview_url")) or self._coerce_str_or_none(
                best.get(_VECTEEZY_PREVIEW_FALLBACK_KEY)
            )
            seen.add(source_asset_id)
            candidates.append(
                AssetCandidate(
                    source_platform="vecteezy",
                    source_asset_id=source_asset_id,
                    media_type="image",
                    download_url=f"vecteezy://resource/{source_asset_id}",
                    preview_url=preview_url,
                    source_url=f"https://api.vecteezy.com/v2/{account_id}/resources/{source_asset_id}",
                    license_name="Vecteezy Free License",
                    license_url="https://www.vecteezy.com/licensing-agreement",
                    description=self._coerce_str_or_none(best.get("title")),
                    width=self._coerce_optional_int(best_size.get("width") if isinstance(best_size, dict) else None),
                    height=self._coerce_optional_int(best_size.get("height") if isinstance(best_size, dict) else None),
                    download_extension=download_extension or ".jpg",
                    restriction_flags=[
                        "experimental-provider",
                        "monthly-download-cap",
                        "free-license-production-budget-cap",
                    ],
                    attribution_required=True,
                    attribution_text=(
                        f"Vecteezy | {self._coerce_str_or_none(best.get('title')) or source_asset_id} | "
                        "Attribution required under the Vecteezy free license."
                    ),
                )
            )

        return self._stable_rotate_candidates(candidates, seed=f"vecteezy-images:{query.lower()}")

    def _coverr_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def _coverr_usage_ledger_path(self) -> Path:
        return (Path.home() / ".imagine" / "provider_usage" / "coverr_requests.json").resolve()

    def _current_hour_key(self) -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H")

    def _coverr_usage_subject(self) -> str:
        return self._coerce_str_or_none(self.config.coverr_app_id) or "default"

    def _load_coverr_usage_ledger(self) -> dict[str, Any]:
        payload = self._load_json_state(self._coverr_usage_ledger_path())
        if isinstance(payload, dict):
            return payload
        return {
            "schema_version": 1,
            "apps": {},
        }

    def _save_coverr_usage_ledger(self, payload: dict[str, Any]) -> None:
        path = self._coverr_usage_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._write_json(path, payload)

    def _coverr_local_hour_request_count(self, subject: str) -> int:
        payload = self._load_coverr_usage_ledger()
        apps = payload.get("apps")
        if not isinstance(apps, dict):
            return 0

        app_payload = apps.get(subject)
        if not isinstance(app_payload, dict):
            return 0

        hours = app_payload.get("hours")
        if not isinstance(hours, dict):
            return 0

        hour_payload = hours.get(self._current_hour_key())
        if not isinstance(hour_payload, dict):
            return 0

        events = hour_payload.get("events")
        if isinstance(events, list):
            return len(events)
        return self._coerce_optional_int(hour_payload.get("request_count")) or 0

    def _record_coverr_request_event(self, *, endpoint: str, query: str | None = None) -> None:
        with self._provider_usage_lock:
            subject = self._coverr_usage_subject()
            payload = self._load_coverr_usage_ledger()
            apps = payload.setdefault("apps", {})
            if not isinstance(apps, dict):
                payload["apps"] = {}
                apps = payload["apps"]

            app_payload = apps.setdefault(subject, {})
            if not isinstance(app_payload, dict):
                app_payload = {}
                apps[subject] = app_payload

            hours = app_payload.setdefault("hours", {})
            if not isinstance(hours, dict):
                app_payload["hours"] = {}
                hours = app_payload["hours"]

            hour_key = self._current_hour_key()
            hour_payload = hours.setdefault(hour_key, {})
            if not isinstance(hour_payload, dict):
                hour_payload = {}
                hours[hour_key] = hour_payload

            events = hour_payload.setdefault("events", [])
            if not isinstance(events, list):
                hour_payload["events"] = []
                events = hour_payload["events"]

            events.append(
                {
                    "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "project_id": self.config.project_dir.name,
                    "endpoint": endpoint,
                    "query": self._coerce_str_or_none(query),
                }
            )
            hour_payload["request_count"] = len(events)
            self._save_coverr_usage_ledger(payload)

            self._coverr_requests_this_run += 1
            if isinstance(self._coverr_usage_state, dict):
                local_count = self._coerce_optional_int(self._coverr_usage_state.get("local_hour_request_count")) or 0
                self._coverr_usage_state["local_hour_request_count"] = local_count + 1
                self._coverr_usage_state["requests_this_run"] = int(self._coverr_requests_this_run)
                remaining = self._coerce_optional_int(self._coverr_usage_state.get("estimated_remaining_requests"))
                if remaining is not None:
                    self._coverr_usage_state["estimated_remaining_requests"] = max(0, remaining - 1)

    def _refresh_coverr_usage_state(self) -> dict[str, Any]:
        subject = self._coverr_usage_subject()
        local_count = self._coverr_local_hour_request_count(subject)
        remaining_requests = max(0, COVERR_HOURLY_REQUEST_LIMIT - local_count)
        snapshot: dict[str, Any] = {
            "provider": "coverr",
            "enabled": bool(self.config.enable_coverr_provider),
            "configured": bool(self._coerce_str_or_none(self.config.coverr_api_key)),
            "app_id": self._coerce_str_or_none(self.config.coverr_app_id),
            "requests_this_run": int(self._coverr_requests_this_run),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "hour_key": self._current_hour_key(),
            "local_hour_request_count": local_count,
            "hourly_request_limit": COVERR_HOURLY_REQUEST_LIMIT,
            "estimated_remaining_requests": remaining_requests,
        }
        self._coverr_usage_state = snapshot
        return snapshot

    def _coverr_estimated_remaining_requests(self) -> int | None:
        with self._provider_usage_lock:
            if self._coverr_usage_state is None:
                self._refresh_coverr_usage_state()

            if not isinstance(self._coverr_usage_state, dict):
                return None

            remaining = self._coverr_usage_state.get("estimated_remaining_requests")
            if remaining is None:
                return None
            try:
                return int(remaining)
            except (TypeError, ValueError):
                return None

    def _vecteezy_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def _vecteezy_usage_ledger_path(self) -> Path:
        return (Path.home() / ".imagine" / "provider_usage" / "vecteezy_downloads.json").resolve()

    def _current_month_key(self) -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")

    def _load_vecteezy_usage_ledger(self) -> dict[str, Any]:
        payload = self._load_json_state(self._vecteezy_usage_ledger_path())
        if isinstance(payload, dict):
            return payload
        return {
            "schema_version": 1,
            "accounts": {},
        }

    def _save_vecteezy_usage_ledger(self, payload: dict[str, Any]) -> None:
        path = self._vecteezy_usage_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._write_json(path, payload)

    def _vecteezy_local_month_download_count(self, account_id: str) -> int:
        payload = self._load_vecteezy_usage_ledger()
        accounts = payload.get("accounts")
        if not isinstance(accounts, dict):
            return 0

        account_payload = accounts.get(account_id)
        if not isinstance(account_payload, dict):
            return 0

        months = account_payload.get("months")
        if not isinstance(months, dict):
            return 0

        month_payload = months.get(self._current_month_key())
        if not isinstance(month_payload, dict):
            return 0

        events = month_payload.get("events")
        if isinstance(events, list):
            return len(events)
        return self._coerce_optional_int(month_payload.get("download_count")) or 0

    def _record_vecteezy_download_event(self, asset_id: str, source_url: str | None) -> None:
        with self._provider_usage_lock:
            account_id = self._coerce_str_or_none(self.config.vecteezy_account_id)
            if not account_id:
                return

            payload = self._load_vecteezy_usage_ledger()
            accounts = payload.setdefault("accounts", {})
            if not isinstance(accounts, dict):
                payload["accounts"] = {}
                accounts = payload["accounts"]

            account_payload = accounts.setdefault(account_id, {})
            if not isinstance(account_payload, dict):
                account_payload = {}
                accounts[account_id] = account_payload

            months = account_payload.setdefault("months", {})
            if not isinstance(months, dict):
                account_payload["months"] = {}
                months = account_payload["months"]

            month_key = self._current_month_key()
            month_payload = months.setdefault(month_key, {})
            if not isinstance(month_payload, dict):
                month_payload = {}
                months[month_key] = month_payload

            events = month_payload.setdefault("events", [])
            if not isinstance(events, list):
                month_payload["events"] = []
                events = month_payload["events"]

            events.append(
                {
                    "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "project_id": self.config.project_dir.name,
                    "asset_id": asset_id,
                    "source_url": source_url or "",
                }
            )
            month_payload["download_count"] = len(events)
            self._save_vecteezy_usage_ledger(payload)

    def _fetch_vecteezy_account_info(self, account_id: str, api_key: str) -> dict[str, Any]:
        url = f"https://api.vecteezy.com/v2/{quote_plus(account_id)}/account/info"
        response = self.http.get(url, headers=self._vecteezy_headers(api_key), timeout=(5, 15))
        if response.status_code != 200:
            raise RuntimeError(f"Vecteezy account info failed, status={response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Vecteezy account info response was not a JSON object")
        return payload

    def _refresh_vecteezy_usage_state(self) -> dict[str, Any]:
        account_id = self._coerce_str_or_none(self.config.vecteezy_account_id)
        api_key = self._coerce_str_or_none(self.config.vecteezy_api_key)
        snapshot: dict[str, Any] = {
            "provider": "vecteezy",
            "enabled": bool(self.config.enable_vecteezy_provider),
            "configured": bool(account_id and api_key),
            "fallback_only": True,
            "account_id": account_id,
            "downloads_this_run": int(self._vecteezy_downloads_this_run),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

        if not account_id or not api_key:
            self._vecteezy_usage_state = snapshot
            return snapshot

        local_download_count = self._vecteezy_local_month_download_count(account_id)
        snapshot["local_month_download_count"] = local_download_count

        try:
            account_info = self._fetch_vecteezy_account_info(account_id, api_key)
            current = account_info.get("current")
            if isinstance(current, dict):
                general = current.get("general") if isinstance(current.get("general"), dict) else {}
                download = current.get("download") if isinstance(current.get("download"), dict) else {}
                general_count = self._coerce_optional_int(general.get("call_count")) or 0
                general_limit = self._coerce_optional_int(general.get("call_limit"))
                general_hard_limit = self._coerce_optional_int(general.get("hard_limit"))
                download_count = self._coerce_optional_int(download.get("call_count")) or 0
                download_limit = self._coerce_optional_int(download.get("call_limit"))
                download_hard_limit = self._coerce_optional_int(download.get("hard_limit"))

                tracked_download_count = max(download_count, local_download_count)
                remaining_downloads = None
                if download_limit is not None and download_limit > 0:
                    remaining_downloads = max(0, download_limit - tracked_download_count)

                snapshot["remote"] = {
                    "general_call_count": general_count,
                    "general_call_limit": general_limit,
                    "general_hard_limit": general_hard_limit,
                    "download_call_count": download_count,
                    "download_call_limit": download_limit,
                    "download_hard_limit": download_hard_limit,
                }
                snapshot["tracked_download_count"] = tracked_download_count
                snapshot["estimated_remaining_downloads"] = remaining_downloads
        except Exception as exc:
            snapshot["sync_error"] = str(exc)

        self._vecteezy_usage_state = snapshot
        return snapshot

    def _vecteezy_estimated_remaining_downloads(self) -> int | None:
        with self._provider_usage_lock:
            if self._vecteezy_usage_state is None:
                self._refresh_vecteezy_usage_state()

            if not isinstance(self._vecteezy_usage_state, dict):
                return None

            remaining = self._vecteezy_usage_state.get("estimated_remaining_downloads")
            if remaining is None:
                return None
            try:
                return int(remaining)
            except (TypeError, ValueError):
                return None

    def _merge_restriction_flags(self, existing_flags: list[str], extra_flags: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for raw_flag in [*existing_flags, *extra_flags]:
            flag = str(raw_flag or "").strip()
            if not flag:
                continue
            lowered = flag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(flag)
        return merged

    def _candidate_cache_key(self, candidate: AssetCandidate) -> str | None:
        if candidate.source_platform == "vecteezy" and candidate.source_asset_id:
            return f"vecteezy:{candidate.source_asset_id}"
        return None

    def _candidate_cache_path(self, candidate: AssetCandidate) -> Path:
        return self._asset_cache_path(
            candidate.download_url,
            cache_key=self._candidate_cache_key(candidate),
            suffix_hint=candidate.download_extension,
        )

    def _candidate_is_cached(self, candidate: AssetCandidate) -> bool:
        cache_path = self._candidate_cache_path(candidate)
        return cache_path.exists() and cache_path.stat().st_size > 0

    def _download_candidate_asset(
        self,
        candidate: AssetCandidate,
        *,
        session: requests.Session | None = None,
    ) -> tuple[Path, AssetCandidate]:
        if self._candidate_is_cached(candidate):
            return self._candidate_cache_path(candidate), candidate

        if candidate.source_platform == "vecteezy":
            download_url, resolved_candidate = self._resolve_vecteezy_download(candidate, session=session)
            local_path = self._download_asset(
                download_url,
                cache_key=self._candidate_cache_key(resolved_candidate),
                suffix_hint=resolved_candidate.download_extension,
                session=session,
            )
            return local_path, resolved_candidate

        return self._download_asset(candidate.download_url, session=session), candidate

    def _resolve_vecteezy_download(
        self,
        candidate: AssetCandidate,
        *,
        session: requests.Session | None = None,
    ) -> tuple[str, AssetCandidate]:
        account_id = self._coerce_str_or_none(self.config.vecteezy_account_id)
        api_key = self._coerce_str_or_none(self.config.vecteezy_api_key)
        asset_id = self._coerce_str_or_none(candidate.source_asset_id)
        if not account_id or not api_key or not asset_id:
            raise RuntimeError("Vecteezy provider is missing account credentials or asset id")

        url = f"https://api.vecteezy.com/v2/{quote_plus(account_id)}/resources/{quote_plus(asset_id)}/download"
        file_extension = str(candidate.download_extension or "").strip().lower().lstrip(".")
        if file_extension:
            url += f"?file_type={quote_plus(file_extension)}"

        response = self._http_get_with_retries(
            url,
            headers=self._vecteezy_headers(api_key),
            timeout=(5, 20),
            session=session,
        )
        if response.status_code == 402:
            raise RuntimeError("Vecteezy monthly download quota exceeded")
        if response.status_code != 200:
            raise RuntimeError(f"Vecteezy download request failed, status={response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Vecteezy download response was not a JSON object")

        download_url = self._coerce_str_or_none(payload.get("url")) or self._coerce_str_or_none(payload.get("inline_url"))
        download_status_url = self._coerce_str_or_none(payload.get("download_status_url"))
        if not download_url and download_status_url:
            download_url = self._poll_vecteezy_download_status(download_status_url, api_key, session=session)
        if not download_url:
            raise RuntimeError("Vecteezy download response did not include a usable URL")

        requires_attribution = bool(payload.get("requires_attribution"))
        attribution_url = self._coerce_str_or_none(payload.get("required_attribution_url"))
        title = self._coerce_str_or_none(candidate.description) or f"Vecteezy asset {asset_id}"
        attribution_text = candidate.attribution_text
        if requires_attribution:
            if attribution_url:
                attribution_text = f"Vecteezy | {title} | Attribution link: {attribution_url}"
            elif not attribution_text:
                attribution_text = f"Vecteezy | {title} | Attribution required under the free license."

        resolved_candidate = replace(
            candidate,
            source_url=attribution_url or candidate.source_url,
            attribution_required=requires_attribution or candidate.attribution_required,
            attribution_text=attribution_text,
            restriction_flags=self._merge_restriction_flags(
                list(candidate.restriction_flags),
                [
                    "experimental-provider",
                    "monthly-download-cap",
                    "free-license-production-budget-cap",
                ],
            ),
        )
        self._record_vecteezy_download_event(asset_id, resolved_candidate.source_url)
        with self._provider_usage_lock:
            self._vecteezy_downloads_this_run += 1
            if isinstance(self._vecteezy_usage_state, dict):
                current_tracked = self._coerce_optional_int(self._vecteezy_usage_state.get("tracked_download_count")) or 0
                self._vecteezy_usage_state["tracked_download_count"] = current_tracked + 1
                self._vecteezy_usage_state["downloads_this_run"] = int(self._vecteezy_downloads_this_run)
                remaining = self._coerce_optional_int(self._vecteezy_usage_state.get("estimated_remaining_downloads"))
                if remaining is not None:
                    self._vecteezy_usage_state["estimated_remaining_downloads"] = max(0, remaining - 1)
        return download_url, resolved_candidate

    def _poll_vecteezy_download_status(
        self,
        status_url: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
    ) -> str:
        active_session = session or self.http
        for _ in range(12):
            response = active_session.get(status_url, headers=self._vecteezy_headers(api_key), timeout=(5, 20))
            if response.status_code != 200:
                raise RuntimeError(f"Vecteezy download status failed, status={response.status_code}")
            payload = response.json()
            if isinstance(payload, dict):
                download_url = self._coerce_str_or_none(payload.get("url")) or self._coerce_str_or_none(payload.get("inline_url"))
                if download_url:
                    return download_url
            time.sleep(1.0)
        raise RuntimeError("Vecteezy download status did not produce a signed URL in time")

    def _query_for_scene(self, scene: Scene) -> str:
        return self._queries_for_scene(scene)[0]

    def _coerce_optional_int(self, raw_value: Any) -> int | None:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    def _coerce_optional_float(self, raw_value: Any) -> float | None:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        if value <= 0.0:
            return None
        return value

    def _coerce_str_or_none(self, raw_value: Any) -> str | None:
        value = str(raw_value or "").strip()
        return value or None

    def _channel_profile_key(self) -> str | None:
        key = str(self.config.channel_profile or "").strip().lower()
        return key or None

    def _channel_visual_vocabulary(self):
        return resolve_channel_visual_vocabulary(self._channel_profile_key())

    def _short_query_phrase(self, raw_text: str, *, max_words: int = 3) -> str:
        words = re.findall(r"[A-Za-z0-9À-ÿ']+", str(raw_text or ""))
        if not words:
            return ""
        return " ".join(words[: max(1, int(max_words))]).strip()

    def _scene_query_context(self, scene: Scene, *, ignore_global_keywords: bool = False) -> dict[str, Any]:
        cache_key = json.dumps(
            {
                "scene_id": scene.scene_id,
                "heading": scene.heading,
                "voiceover": scene.voiceover,
                "search_terms": list(scene.search_terms),
                "ignore_global_keywords": bool(ignore_global_keywords),
                "channel_profile": self._channel_profile_key(),
                "asset_keywords": [] if ignore_global_keywords else list(self.config.asset_keywords),
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        cached = self._scene_query_context_cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        vocabulary = self._channel_visual_vocabulary()
        text_parts = [
            str(scene.heading or "").strip(),
            str(scene.voiceover or "").strip(),
            *[str(item).strip() for item in scene.search_terms if str(item).strip()],
        ]
        normalized_blob = f" {normalize_match_text(' '.join(text_parts))} ".strip()
        normalized_blob = f" {normalized_blob} " if normalized_blob else ""
        token_set = set(normalized_blob.split()) if normalized_blob else set()

        matched_scored: list[tuple[float, str]] = []
        if vocabulary is not None and normalized_blob:
            for vocab_term in vocabulary.terms:
                canonical = normalize_match_text(vocab_term.term)
                if not canonical:
                    continue
                variants = [canonical, *[normalize_match_text(alias) for alias in vocab_term.aliases]]
                best_score = 0.0
                for variant in variants:
                    if not variant:
                        continue
                    variant_tokens = [item for item in variant.split() if item]
                    if not variant_tokens:
                        continue
                    if len(variant_tokens) == 1:
                        if variant_tokens[0] in token_set:
                            best_score = max(best_score, float(vocab_term.weight) + 0.35)
                    else:
                        phrase_match = f" {variant} " in normalized_blob
                        token_match = all(token in token_set for token in variant_tokens)
                        if phrase_match or token_match:
                            best_score = max(best_score, float(vocab_term.weight) + 0.6 + (len(variant_tokens) * 0.05))
                if best_score > 0.0:
                    matched_scored.append((best_score, vocab_term.term))

        matched_terms = [
            term
            for _, term in sorted(matched_scored, key=lambda item: (-item[0], normalize_match_text(item[1])))
        ][:5]
        normalized_matched = {normalize_match_text(term) for term in matched_terms if term}

        local_cues: list[str] = []
        seen_cues: set[str] = set()
        cue_sources = [
            *self._extract_required_entities(f"{scene.heading} {scene.voiceover}"),
            *[str(item).strip() for item in scene.search_terms if str(item).strip()],
            self._short_query_phrase(scene.heading, max_words=2),
        ]
        for cue in cue_sources:
            cleaned = self._short_query_phrase(str(cue).strip(), max_words=2)
            if not cleaned:
                continue
            normalized = normalize_match_text(cleaned)
            if not normalized or normalized in seen_cues or normalized in normalized_matched:
                continue
            seen_cues.add(normalized)
            local_cues.append(cleaned)
            if len(local_cues) >= 3:
                break

        fallback_queries: list[str] = []
        seen_fallback: set[str] = set()
        scene_terms = [term.strip() for term in scene.search_terms if term.strip()]
        for candidate in (
            scene_terms[0] if scene_terms else "",
            " ".join(scene_terms[:2]).strip() if len(scene_terms) > 1 else "",
            self._short_query_phrase(scene.heading, max_words=3),
            self._short_query_phrase(self.config.prompt, max_words=3),
        ):
            cleaned = re.sub(r"\s+", " ", str(candidate or "").strip())
            lowered = cleaned.lower()
            if not cleaned or lowered in seen_fallback:
                continue
            seen_fallback.add(lowered)
            fallback_queries.append(cleaned)

        if not ignore_global_keywords:
            keyword_phrase = " ".join(
                self._short_query_phrase(str(item).strip(), max_words=2)
                for item in self.config.asset_keywords[:3]
                if str(item).strip()
            ).strip()
            if keyword_phrase and keyword_phrase.lower() not in seen_fallback:
                fallback_queries.append(keyword_phrase)

        effective_queries: list[str] = []
        seen_queries: set[str] = set()

        def add_query(value: str) -> None:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip())
            lowered = cleaned.lower()
            if not cleaned or lowered in seen_queries:
                return
            seen_queries.add(lowered)
            effective_queries.append(cleaned)

        if ignore_global_keywords and scene_terms:
            for manual_query in scene_terms[:3]:
                add_query(self._short_query_phrase(manual_query, max_words=3))

        if matched_terms:
            add_query(self._short_query_phrase(matched_terms[0], max_words=2))
        if len(matched_terms) >= 2:
            add_query(" ".join(self._short_query_phrase(item, max_words=2) for item in matched_terms[:2]).strip())
        if matched_terms and local_cues:
            add_query(
                f"{self._short_query_phrase(matched_terms[0], max_words=2)} "
                f"{self._short_query_phrase(local_cues[0], max_words=2)}".strip()
            )
        for fallback_query in fallback_queries:
            add_query(fallback_query)

        context = {
            "matched_terms": matched_terms,
            "local_cues": local_cues,
            "effective_queries": effective_queries[:5],
            "channel_profile": self._channel_profile_key(),
        }
        self._scene_query_context_cache[cache_key] = dict(context)
        return context

    def _media_type_from_path(self, media_path: Path) -> str:
        ext = media_path.suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            return "image"
        return "video"

    def _stable_pivot(self, seed: str, upper_bound: int) -> int:
        limit = max(1, int(upper_bound))
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
        return int(digest[:12], 16) % limit

    def _queries_for_scene(self, scene: Scene, *, ignore_global_keywords: bool = False) -> list[str]:
        context = self._scene_query_context(scene, ignore_global_keywords=ignore_global_keywords)
        queries = [
            str(item).strip()
            for item in context.get("effective_queries") or []
            if str(item).strip()
        ]
        if queries:
            return queries
        heading = self._short_query_phrase(scene.heading, max_words=3)
        if heading:
            return [heading]
        return [self._short_query_phrase(self.config.prompt, max_words=3) or self.config.prompt]

    def _asset_cache_path(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        suffix_hint: str | None = None,
    ) -> Path:
        parsed = urlparse(url)
        ext = str(suffix_hint or Path(parsed.path).suffix).strip().lower()
        if ext not in {".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png"}:
            ext = ".mp4"

        digest_source = str(cache_key or url)
        digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()
        return self.paths["assets_cache"] / f"{digest}{ext}"

    def _download_asset(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        suffix_hint: str | None = None,
        session: requests.Session | None = None,
    ) -> Path:
        output = self._asset_cache_path(url, cache_key=cache_key, suffix_hint=suffix_hint)
        if output.exists() and output.stat().st_size > 0:
            return output

        output.parent.mkdir(parents=True, exist_ok=True)
        active_session = session or self.http
        cache_lock = self._asset_cache_lock(output)
        with cache_lock:
            if output.exists() and output.stat().st_size > 0:
                return output
            if output.exists():
                try:
                    output.unlink()
                except FileNotFoundError:
                    pass

            temp_output = output.with_name(f".{output.name}.part-{os.getpid()}-{threading.get_ident()}")
            try:
                if temp_output.exists():
                    temp_output.unlink()

                try:
                    with active_session.get(url, stream=True, timeout=(8, 20)) as response:
                        if response.status_code != 200:
                            raise RuntimeError(f"Failed to download asset, status={response.status_code}")

                        with temp_output.open("wb") as handle:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    handle.write(chunk)
                except requests.exceptions.RequestException as exc:
                    raise RuntimeError(f"Failed to download asset from {url}: {exc}") from exc

                if not temp_output.exists() or temp_output.stat().st_size == 0:
                    raise RuntimeError("Downloaded asset is empty")
                temp_output.replace(output)
            finally:
                if temp_output.exists():
                    try:
                        temp_output.unlink()
                    except FileNotFoundError:
                        pass
        return output

    def _generate_captions(self, plan: ScriptPlan, narration_wav: Path) -> list[CaptionCue]:
        captions: list[CaptionCue] = []

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

    def _captions_heuristic(self, plan: ScriptPlan) -> list[CaptionCue]:
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

    def _chunk_word_events(self, words: list[tuple[float, float, str]]) -> list[CaptionCue]:
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

        chunks: list[CaptionCue] = []
        current: list[tuple[float, float, str]] = []

        def flush_current() -> None:
            if not current:
                return
            start = float(current[0][0])
            end = float(current[-1][1])
            text = self._join_caption_tokens([item[2] for item in current])
            if text:
                chunks.append(
                    CaptionCue(
                        start=start,
                        end=max(start + 0.05, end),
                        text=text,
                        words=tuple(
                            CaptionWordTiming(
                                start=float(word_start),
                                end=float(word_end),
                                token=str(word_token),
                            )
                            for word_start, word_end, word_token in current
                        ),
                    )
                )
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

        stabilized: list[CaptionCue] = []
        for index, cue in enumerate(chunks):
            next_start = chunks[index + 1].start if index + 1 < len(chunks) else None
            target_end = cue.end
            if (cue.end - cue.start) < min_seconds:
                target_end = cue.start + min_seconds
                if next_start is not None:
                    target_end = min(target_end, max(cue.start + 0.08, next_start - 0.03))
            if target_end <= cue.start:
                target_end = cue.end
            stabilized.append(replace(cue, end=target_end))

        return stabilized

    def _tokenize_caption_text(self, text: str) -> list[str]:
        return re.findall(r"[^\W_]+(?:'[^\W_]+)?|[.,!?;:]", text)

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

    def _update_caption_stats(self, captions: list[CaptionCue]) -> None:
        if not captions:
            self.caption_stats["entries"] = 0
            self.caption_stats["avg_words_per_entry"] = 0.0
            self.caption_stats["subtitle_preset"] = normalize_subtitle_preset(self.config.subtitle_preset, "regular")
            self.caption_stats["subtitle_position"] = normalize_subtitle_position(self.config.subtitle_position, "bottom")
            self.caption_stats["subtitle_accent_color"] = normalize_subtitle_accent_color(
                self.config.subtitle_accent_color,
                "sunflower",
            )
            self.caption_stats["subtitle_box_color"] = normalize_subtitle_box_color(
                self.config.subtitle_box_color,
                normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower"),
            )
            self.caption_stats["subtitle_bold"] = bool(self.config.subtitle_bold)
            self.caption_stats["subtitle_outline"] = bool(self.config.subtitle_outline)
            self.caption_stats["caption_font_scale"] = round(float(self.config.caption_font_scale), 3)
            return

        word_counts = [len(cue.text.split()) for cue in captions if cue.text.strip()]
        durations = [max(0.01, cue.end - cue.start) for cue in captions]
        avg_words = (sum(word_counts) / len(word_counts)) if word_counts else 0.0
        avg_duration = (sum(durations) / len(durations)) if durations else 0.0

        self.caption_stats["entries"] = len(captions)
        self.caption_stats["avg_words_per_entry"] = round(avg_words, 2)
        self.caption_stats["avg_duration_seconds"] = round(avg_duration, 2)
        self.caption_stats["style"] = self.config.caption_style
        self.caption_stats["subtitle_preset"] = normalize_subtitle_preset(self.config.subtitle_preset, "regular")
        self.caption_stats["subtitle_position"] = normalize_subtitle_position(self.config.subtitle_position, "bottom")
        self.caption_stats["subtitle_accent_color"] = normalize_subtitle_accent_color(
            self.config.subtitle_accent_color,
            "sunflower",
        )
        self.caption_stats["subtitle_box_color"] = normalize_subtitle_box_color(
            self.config.subtitle_box_color,
            normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower"),
        )
        self.caption_stats["subtitle_bold"] = bool(self.config.subtitle_bold)
        self.caption_stats["subtitle_outline"] = bool(self.config.subtitle_outline)
        self.caption_stats["caption_font_scale"] = round(float(self.config.caption_font_scale), 3)

    def _write_srt(self, srt_path: Path, captions: list[CaptionCue]) -> None:
        lines: list[str] = []
        for idx, cue in enumerate(captions, start=1):
            lines.append(str(idx))
            lines.append(f"{self._format_srt_time(cue.start)} --> {self._format_srt_time(cue.end)}")
            lines.append(cue.text)
            lines.append("")
        self._write_text(srt_path, "\n".join(lines))

    def _subtitle_ass_style_name(self) -> str:
        return "Caption"

    def _subtitle_box_background_ass_style_name(self) -> str:
        return "CaptionBoxBackground"

    def _subtitle_box_text_ass_style_name(self) -> str:
        return "CaptionBoxText"

    def _subtitle_margin_ratio(self) -> float:
        position = normalize_subtitle_position(self.config.subtitle_position, "bottom")
        if position == "mid-safe":
            return 0.30
        return max(0.02, min(0.2, float(self.config.caption_bottom_ratio)))

    def _caption_token_is_highlightable(self, token: str) -> bool:
        candidate = re.sub(r"^[^\w']+|[^\w']+$", "", token).strip()
        return bool(re.fullmatch(r"[^\W_]+(?:'[^\W_]+)?", candidate))

    def _subtitle_follow_intervals(
        self,
        cue: CaptionCue,
    ) -> list[tuple[float, float, int | None]]:
        if not cue.words:
            return [(cue.start, cue.end, None)]

        highlightable_indices = [
            index for index, word in enumerate(cue.words) if self._caption_token_is_highlightable(word.token)
        ]
        if not highlightable_indices:
            return [(cue.start, cue.end, None)]

        intervals: list[tuple[float, float, int | None]] = []
        first_start = max(cue.start, cue.words[highlightable_indices[0]].start)
        if first_start > cue.start:
            intervals.append((cue.start, first_start, None))

        for offset, token_index in enumerate(highlightable_indices):
            start = max(cue.start, cue.words[token_index].start)
            if offset + 1 < len(highlightable_indices):
                end = min(cue.end, cue.words[highlightable_indices[offset + 1]].start)
            else:
                end = cue.end
            if end <= start:
                end = min(cue.end, max(start + 0.05, cue.words[token_index].end))
            if end > start:
                intervals.append((start, end, token_index))

        if not intervals:
            intervals.append((cue.start, cue.end, None))
        return intervals

    def _format_subtitle_ass_text(
        self,
        cue: CaptionCue,
        style_name: str,
        active_token_index: int | None = None,
    ) -> str:
        preset = normalize_subtitle_preset(self.config.subtitle_preset, "regular")
        if preset != "highlight-follow" or active_token_index is None or not cue.words:
            return self._escape_ass_text(cue.text)

        text_color_name = normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower")
        text_color = SUBTITLE_ACCENT_ASS_COLORS.get(text_color_name, SUBTITLE_ACCENT_ASS_COLORS["sunflower"])

        punctuation = {".", ",", "!", "?", ";", ":"}
        parts: list[str] = []
        for index, word in enumerate(cue.words):
            safe_token = self._escape_ass_text(word.token)
            if index == active_token_index:
                safe_token = f"{{\\1c{text_color}}}{safe_token}{{\\r{style_name}}}"

            if not parts:
                parts.append(safe_token)
            elif word.token in punctuation:
                parts.append(safe_token)
            else:
                parts.append(" " + safe_token)

        return "".join(parts).strip()

    def _format_subtitle_ass_box_base_text(
        self,
        cue: CaptionCue,
        style_name: str,
        active_token_index: int,
    ) -> str:
        punctuation = {".", ",", "!", "?", ";", ":"}
        parts: list[str] = []
        for index, word in enumerate(cue.words):
            safe_token = self._escape_ass_text(word.token)
            if index == active_token_index:
                safe_token = f"{{\\alpha&HFF&}}{safe_token}{{\\r{style_name}}}"

            if not parts:
                parts.append(safe_token)
            elif word.token in punctuation:
                parts.append(safe_token)
            else:
                parts.append(" " + safe_token)
        return "".join(parts).strip()

    def _format_subtitle_ass_box_background_text(
        self,
        cue: CaptionCue,
        style_name: str,
        background_style_name: str,
        active_token_index: int,
    ) -> str:
        punctuation = {".", ",", "!", "?", ";", ":"}
        parts: list[str] = []
        for index, word in enumerate(cue.words):
            safe_token = self._escape_ass_text(word.token)
            if index == active_token_index:
                stripped_token = word.token.strip(".,!?;:\"'()[]{}")
                box_padding = "\\h" if len(stripped_token) > 3 else ""
                safe_token = (
                    f"{{\\r{background_style_name}\\1a&HFF&\\blur{SUBTITLE_ACTIVE_BOX_BLUR}}}"
                    f"{box_padding}{safe_token}{box_padding}"
                    f"{{\\r{style_name}}}"
                )
            else:
                safe_token = f"{{\\alpha&HFF&}}{safe_token}{{\\r{style_name}}}"

            if not parts:
                parts.append(safe_token)
            elif word.token in punctuation:
                parts.append(safe_token)
            else:
                parts.append(" " + safe_token)
        return "".join(parts).strip()

    def _format_subtitle_ass_box_text_text(
        self,
        cue: CaptionCue,
        style_name: str,
        text_style_name: str,
        active_token_index: int,
    ) -> str:
        punctuation = {".", ",", "!", "?", ";", ":"}
        parts: list[str] = []
        for index, word in enumerate(cue.words):
            safe_token = self._escape_ass_text(word.token)
            if index == active_token_index:
                safe_token = f"{{\\r{text_style_name}}}{safe_token}{{\\r{style_name}}}"
            else:
                safe_token = f"{{\\alpha&HFF&}}{safe_token}{{\\r{style_name}}}"

            if not parts:
                parts.append(safe_token)
            elif word.token in punctuation:
                parts.append(safe_token)
            else:
                parts.append(" " + safe_token)
        return "".join(parts).strip()

    def _write_ass(self, ass_path: Path, captions: list[CaptionCue]) -> None:
        base_scale = 0.046 if self.config.caption_style == "engagement" else 0.04
        font_scale = normalize_caption_font_scale(self.config.caption_font_scale, 0.9)
        font_size = max(16, int(round(self.config.height * base_scale * font_scale)))
        margin_v = max(18, int(self.config.height * self._subtitle_margin_ratio()))
        margin_lr = max(40, int(self.config.width * 0.045))
        outline = (2.6 if self.config.caption_style == "engagement" else 2.0) if self.config.subtitle_outline else 0.0
        shadow = (0.8 if self.config.caption_style == "engagement" else 0.5) if self.config.subtitle_outline else 0.45
        bold_value = -1 if self.config.subtitle_bold else 0
        style_name = self._subtitle_ass_style_name()
        box_background_style_name = self._subtitle_box_background_ass_style_name()
        box_text_style_name = self._subtitle_box_text_ass_style_name()
        preset = normalize_subtitle_preset(self.config.subtitle_preset, "regular")
        text_color_name = normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower")
        text_color = SUBTITLE_ACCENT_ASS_COLORS.get(text_color_name, SUBTITLE_ACCENT_ASS_COLORS["sunflower"])
        box_color_name = normalize_subtitle_box_color(self.config.subtitle_box_color, text_color_name)
        box_color = SUBTITLE_ACCENT_ASS_COLORS.get(box_color_name, SUBTITLE_ACCENT_ASS_COLORS["sunflower"])
        active_box_outline = SUBTITLE_ACTIVE_BOX_OUTLINE if self.config.subtitle_outline else SUBTITLE_ACTIVE_BOX_OUTLINE_NO_STROKE
        active_text_outline = outline if self.config.subtitle_outline else 0.0
        active_text_shadow = 0.0

        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "Encoding: UTF-8",
            f"PlayResX: {self.config.width}",
            f"PlayResY: {self.config.height}",
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
                f"{bold_value},0,0,0,100,100,0,0,1,"
                f"{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1"
            ),
        ]
        if preset == "highlight-box-follow":
            lines.append(
                "Style: CaptionBoxBackground,Helvetica Neue,"
                f"{font_size},&H00000000&,&H00000000&,"
                f"{box_color},{box_color},"
                f"{bold_value},0,0,0,100,100,0,0,3,"
                f"{active_box_outline},0,2,{margin_lr},{margin_lr},{margin_v},1"
            )
            lines.append(
                "Style: CaptionBoxText,Helvetica Neue,"
                f"{font_size},{text_color},{text_color},&H00101010&,&H64000000&,"
                f"{bold_value},0,0,0,100,100,0,0,1,"
                f"{active_text_outline},{active_text_shadow},2,{margin_lr},{margin_lr},{margin_v},1"
            )
        lines.extend(
            [
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            ]
        )
        for cue in captions:
            if preset == "highlight-box-follow":
                for start, end, active_token_index in self._subtitle_follow_intervals(cue):
                    if active_token_index is None:
                        safe_text = self._escape_ass_text(cue.text)
                        lines.append(
                            f"Dialogue: 0,{self._format_ass_time(start)},{self._format_ass_time(end)},{style_name},,0,0,0,,{safe_text}"
                        )
                        continue

                    base_text = self._format_subtitle_ass_box_base_text(cue, style_name, active_token_index)
                    background_text = self._format_subtitle_ass_box_background_text(
                        cue,
                        style_name,
                        box_background_style_name,
                        active_token_index,
                    )
                    active_text = self._format_subtitle_ass_box_text_text(
                        cue,
                        style_name,
                        box_text_style_name,
                        active_token_index,
                    )
                    lines.append(
                        f"Dialogue: 0,{self._format_ass_time(start)},{self._format_ass_time(end)},{style_name},,0,0,0,,{base_text}"
                    )
                    lines.append(
                        f"Dialogue: 1,{self._format_ass_time(start)},{self._format_ass_time(end)},{style_name},,0,0,0,,{background_text}"
                    )
                    lines.append(
                        f"Dialogue: 2,{self._format_ass_time(start)},{self._format_ass_time(end)},{style_name},,0,0,0,,{active_text}"
                    )
                continue

            if preset == "highlight-follow":
                for start, end, active_token_index in self._subtitle_follow_intervals(cue):
                    safe_text = self._format_subtitle_ass_text(cue, style_name, active_token_index)
                    lines.append(
                        f"Dialogue: 0,{self._format_ass_time(start)},{self._format_ass_time(end)},{style_name},,0,0,0,,{safe_text}"
                    )
                continue

            safe_text = self._format_subtitle_ass_text(cue, style_name)
            lines.append(
                f"Dialogue: 0,{self._format_ass_time(cue.start)},{self._format_ass_time(cue.end)},{style_name},,0,0,0,,{safe_text}"
            )

        self._write_text(ass_path, "\n".join(lines) + "\n")

    def _intro_bookend_seconds(self) -> float:
        if not self.config.include_intro:
            return 0.0
        return max(0.0, float(self.config.intro_seconds))

    def _outro_bookend_seconds(self) -> float:
        if not self.config.include_outro:
            return 0.0
        spoken_duration = self._outro_spoken_audio_duration()
        lead_silence = self._outro_lead_silence_seconds()
        return max(0.0, float(self.config.outro_seconds), lead_silence + spoken_duration + 0.4)

    def _shift_captions(
        self,
        captions: list[CaptionCue],
        offset_seconds: float,
    ) -> list[CaptionCue]:
        if offset_seconds <= 0.0:
            return captions
        shifted: list[CaptionCue] = []
        for cue in captions:
            shifted_words = tuple(
                replace(word, start=word.start + offset_seconds, end=word.end + offset_seconds)
                for word in cue.words
            )
            shifted.append(
                replace(
                    cue,
                    start=cue.start + offset_seconds,
                    end=cue.end + offset_seconds,
                    words=shifted_words,
                )
            )
        return shifted

    def _build_timeline(self, plan: ScriptPlan) -> list[TimelineClip]:
        shot_plan = self._shot_plan or self._load_shot_plan()
        if shot_plan is not None and shot_plan.shots:
            return self._build_timeline_from_shots(shot_plan)

        clips: list[TimelineClip] = []
        visual_cursor = 0.0
        narration_cursor = 0.0

        intro_seconds = self._intro_bookend_seconds()
        if intro_seconds > 0.0:
            start = visual_cursor
            end = visual_cursor + intro_seconds
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
            visual_cursor = end

        for scene in plan.scenes:
            scene_seconds = max(0.3, float(scene.seconds))
            visual_seconds = self._timeline_visual_seconds(
                scene_id=scene.scene_id,
                source_path=scene.asset_path,
                scheduled_seconds=scene_seconds,
            )
            start = visual_cursor
            end = visual_cursor + visual_seconds
            clips.append(
                TimelineClip(
                    scene_id=scene.scene_id,
                    clip_name=scene.clip_name,
                    start=start,
                    end=end,
                    seconds=visual_seconds,
                    source_path=scene.asset_path,
                    heading=scene.heading,
                    narration_start=narration_cursor,
                    narration_end=narration_cursor + scene_seconds,
                    visual_strategy=normalize_news_visual_strategy(scene.visual_strategy, "stock"),
                    editorial_source_id=scene.source_refs[0] if scene.source_refs else None,
                )
            )
            visual_cursor = end
            narration_cursor += scene_seconds

        outro_seconds = self._outro_bookend_seconds()
        visual_body_seconds = max(0.0, visual_cursor - intro_seconds)
        tail_hold_seconds = max(0.0, narration_cursor - visual_body_seconds)
        if outro_seconds > 0.0:
            rendered_outro_seconds = outro_seconds + tail_hold_seconds
            if tail_hold_seconds > 0.04:
                self._warn(
                    f"Extended outro by {tail_hold_seconds:.1f}s so short source clips can advance early without freezing on their last frame."
                )
            start = visual_cursor
            end = visual_cursor + rendered_outro_seconds
            clips.append(
                TimelineClip(
                    scene_id="__outro",
                    clip_name="outro-card",
                    start=start,
                    end=end,
                    seconds=rendered_outro_seconds,
                    source_path=None,
                    heading=self.config.outro_text,
                )
            )
            visual_cursor = end
        elif tail_hold_seconds > 0.04:
            self._warn(
                f"Added a {tail_hold_seconds:.1f}s tail hold after the final scene so short source clips can advance early without freezing per scene."
            )
            start = visual_cursor
            end = visual_cursor + tail_hold_seconds
            clips.append(
                TimelineClip(
                    scene_id="__tail",
                    clip_name="tail-hold",
                    start=start,
                    end=end,
                    seconds=tail_hold_seconds,
                    source_path=None,
                    heading="",
                )
            )

        return clips

    def _build_timeline_from_shots(self, shot_plan: ShotPlan) -> list[TimelineClip]:
        clips: list[TimelineClip] = []
        visual_cursor = 0.0

        intro_seconds = self._intro_bookend_seconds()
        if intro_seconds > 0.0:
            clips.append(
                TimelineClip(
                    scene_id="__intro",
                    clip_name="intro-card",
                    start=0.0,
                    end=intro_seconds,
                    seconds=intro_seconds,
                    source_path=None,
                    heading=shot_plan.title,
                )
            )
            visual_cursor = intro_seconds

        for shot in shot_plan.shots:
            scheduled_seconds = max(0.3, float(shot.seconds))
            visual_seconds = self._timeline_visual_seconds(
                scene_id=shot.shot_id,
                source_path=shot.asset_path,
                scheduled_seconds=scheduled_seconds,
            )
            start = visual_cursor
            end = visual_cursor + visual_seconds
            clips.append(
                TimelineClip(
                    scene_id=shot.shot_id,
                    clip_name=f"{shot.clip_name}-shot-{shot.shot_index:02d}",
                    start=start,
                    end=end,
                    seconds=visual_seconds,
                    source_path=shot.asset_path,
                    heading=shot.heading,
                    narration_start=shot.narration_start,
                    narration_end=shot.narration_end,
                    visual_strategy=normalize_news_visual_strategy(shot.visual_strategy, "stock"),
                    editorial_source_id=shot.source_refs[0] if shot.source_refs else None,
                    shot_id=shot.shot_id,
                    parent_scene_id=shot.scene_id,
                    match_confidence=normalize_shot_confidence(shot.match_confidence, "medium"),
                    fallback_level=shot.fallback_level,
                )
            )
            visual_cursor = end

        outro_seconds = self._outro_bookend_seconds()
        narration_total = max((float(shot.narration_end) for shot in shot_plan.shots), default=0.0)
        visual_body_seconds = max(0.0, visual_cursor - intro_seconds)
        tail_hold_seconds = max(0.0, narration_total - visual_body_seconds)
        if outro_seconds > 0.0:
            rendered_outro_seconds = outro_seconds + tail_hold_seconds
            clips.append(
                TimelineClip(
                    scene_id="__outro",
                    clip_name="outro-card",
                    start=visual_cursor,
                    end=visual_cursor + rendered_outro_seconds,
                    seconds=rendered_outro_seconds,
                    source_path=None,
                    heading=self.config.outro_text,
                )
            )
        elif tail_hold_seconds > 0.04:
            clips.append(
                TimelineClip(
                    scene_id="__tail",
                    clip_name="tail-hold",
                    start=visual_cursor,
                    end=visual_cursor + tail_hold_seconds,
                    seconds=tail_hold_seconds,
                    source_path=None,
                    heading="",
                )
            )
        return clips

    def _timeline_visual_seconds(
        self,
        *,
        scene_id: str,
        source_path: str | None,
        scheduled_seconds: float,
    ) -> float:
        duration = max(0.3, float(scheduled_seconds))
        if not source_path:
            return duration

        source = Path(source_path).expanduser()
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        else:
            source = source.resolve()
        if not source.exists():
            return duration

        if source.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
            return duration

        try:
            source_duration = self._media_duration(source)
        except Exception as exc:
            self._log(f"Could not probe source duration for timeline clip {scene_id}: {exc}")
            return duration

        if source_duration <= 0.0:
            return duration

        visual_seconds = max(0.3, min(duration, source_duration))
        if visual_seconds + 0.04 < duration:
            self._warn(
                f"Source clip {source.name} is only {source_duration:.1f}s for scene {scene_id} "
                f"({duration:.1f}s scheduled). Advancing the next clip early instead of freezing the last frame."
            )
        return visual_seconds

    def _render_video(
        self,
        timeline: list[TimelineClip],
        narration_wav: Path,
        final_mp4: Path,
        *,
        captions_ass_path: Path | None = None,
        intro_seconds: float | None = None,
        outro_seconds: float | None = None,
        render_subdir: str = "render",
        metrics_section: str = "render",
    ) -> None:
        render_dir = self.paths["tmp"] / render_subdir
        render_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = captions_ass_path or self.paths["captions_ass"]
        intro_pad = self._intro_bookend_seconds() if intro_seconds is None else max(0.0, float(intro_seconds))
        outro_pad = self._outro_bookend_seconds() if outro_seconds is None else max(0.0, float(outro_seconds))
        self._set_optimization_value(metrics_section, "requested_video_encoder", self._preferred_video_encoder())

        render_audio = render_dir / "narration_with_bookends.wav"
        with self._timed_optimization_block(metrics_section, "build_audio_seconds"):
            self._build_render_audio_track(
                narration_wav=narration_wav,
                output_audio=render_audio,
                intro_seconds=intro_pad,
                outro_seconds=outro_pad,
            )

        visuals_mp4 = render_dir / "visuals.mp4"
        single_clip_fast_path = len(timeline) == 1 and intro_pad <= 0.0 and outro_pad <= 0.0
        self._set_optimization_value(metrics_section, "single_clip_fast_path", bool(single_clip_fast_path))

        if single_clip_fast_path:
            self._set_optimization_value(metrics_section, "clip_render_workers", 1)
            with self._timed_optimization_block(metrics_section, "clip_render_seconds"):
                self._render_single_clip(timeline[0], visuals_mp4, 0, metrics_section=metrics_section)
            self._increment_optimization_counter(metrics_section, "rendered_clip_count", amount=1)
        else:
            clip_files: list[Path] = [render_dir / f"clip_{idx:04d}.mp4" for idx in range(len(timeline))]
            clip_workers = min(4, max(1, (os.cpu_count() or 2) // 2), len(timeline))
            self._set_optimization_value(metrics_section, "clip_render_workers", clip_workers)

            def render_clip_task(index: int, clip: TimelineClip, clip_path: Path) -> tuple[int, float]:
                started_at = time.perf_counter()
                self._render_single_clip(clip, clip_path, index, metrics_section=metrics_section)
                return index, time.perf_counter() - started_at

            if clip_workers == 1:
                results = [render_clip_task(idx, clip, clip_files[idx]) for idx, clip in enumerate(timeline)]
            else:
                with ThreadPoolExecutor(max_workers=clip_workers) as executor:
                    futures = [
                        executor.submit(render_clip_task, idx, clip, clip_files[idx])
                        for idx, clip in enumerate(timeline)
                    ]
                results = [future.result() for future in futures]

            for _, elapsed_seconds in results:
                self._record_optimization_time(metrics_section, "clip_render_seconds", elapsed_seconds)
            self._increment_optimization_counter(metrics_section, "rendered_clip_count", amount=len(clip_files))

            concat_list = render_dir / "concat.txt"
            concat_lines = [f"file '{path.resolve()}'" for path in clip_files]
            self._write_text(concat_list, "\n".join(concat_lines) + "\n")

            with self._timed_optimization_block(metrics_section, "concat_seconds"):
                concat = self._run_ffmpeg_video_command(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_list),
                        *self._video_encode_args(preset="slow", crf=18),
                        "-an",
                        str(visuals_mp4),
                    ],
                    timeout=3600,
                    preset="slow",
                    crf=18,
                    metrics_section=metrics_section,
                )
            if concat.returncode != 0:
                raise RuntimeError(f"Failed to concat clips: {concat.stderr.strip()}")

        if self.config.burn_subtitles and subtitle_path.exists():
            self._burn_subtitles(
                visuals_mp4,
                subtitle_path,
                final_mp4,
                audio_input=render_audio,
                metrics_section=metrics_section,
            )
            return

        if self.config.burn_subtitles and not subtitle_path.exists():
            self._warn("Subtitle burn-in skipped because captions.ass is missing or empty.")

        with self._timed_optimization_block(metrics_section, "mux_seconds"):
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
                    str(final_mp4),
                ],
                timeout=1200,
                check=False,
            )
        if mux.returncode != 0:
            raise RuntimeError(f"Failed to mux narration and video: {mux.stderr.strip()}")

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
        editorial_sources = (
            (self._approved_editorial_sources or self._load_approved_editorial_sources())
            if self._news_mode_enabled()
            else []
        )
        editorial_by_id = {source.source_id: source for source in editorial_sources}
        existing_catalog = self._load_json_state(self.paths["clip_catalog"]) or {}
        existing_clips = existing_catalog.get("clips") if isinstance(existing_catalog, dict) else None
        existing_by_scene: dict[str, dict[str, Any]] = {}
        if isinstance(existing_clips, list):
            for item in existing_clips:
                if not isinstance(item, dict):
                    continue
                scene_id = str(item.get("scene_id") or "").strip()
                if scene_id:
                    existing_by_scene[scene_id] = item

        clips: list[dict[str, Any]] = []
        for scene in plan.scenes:
            scene_context = self._scene_query_context(scene)
            right = rights_by_scene.get(scene.scene_id)
            editorial_source_id = scene.source_refs[0] if scene.source_refs else None
            editorial_source = editorial_by_id.get(editorial_source_id) if editorial_source_id else None
            selected_candidate = self._selected_assets_by_scene.get(scene.scene_id)
            existing_item = existing_by_scene.get(scene.scene_id, {})
            raw_candidates = existing_item.get("candidates") if isinstance(existing_item, dict) else None
            shortlist = self._scene_asset_shortlists.get(scene.scene_id)
            montage_assets = (
                [dict(item) for item in right.scene_components if isinstance(item, dict)]
                if right is not None
                else [dict(item) for item in existing_item.get("montage_assets") or [] if isinstance(item, dict)]
                if isinstance(existing_item.get("montage_assets"), list)
                else []
            )

            candidate_payloads: list[dict[str, Any]] = []
            if shortlist:
                selected_key = self._asset_uniqueness_key(selected_candidate) if selected_candidate is not None else None
                for candidate in shortlist:
                    payload = candidate.to_dict()
                    payload["selected"] = bool(selected_key and self._asset_uniqueness_key(candidate) == selected_key)
                    candidate_payloads.append(payload)
            elif isinstance(raw_candidates, list):
                candidate_payloads = [dict(item) for item in raw_candidates if isinstance(item, dict)]

            scene_path_media_type = self._media_type_from_path(Path(scene.asset_path)) if scene.asset_path else None
            asset_media_type = (
                (selected_candidate.media_type if selected_candidate is not None else None)
                or (right.media_type if right is not None else None)
                or self._coerce_str_or_none(existing_item.get("asset_media_type"))
                or scene_path_media_type
            )
            asset_width = (
                (selected_candidate.width if selected_candidate is not None else None)
                or (right.width if right is not None else None)
                or self._coerce_optional_int(existing_item.get("asset_width"))
            )
            asset_height = (
                (selected_candidate.height if selected_candidate is not None else None)
                or (right.height if right is not None else None)
                or self._coerce_optional_int(existing_item.get("asset_height"))
            )
            asset_duration = (
                (selected_candidate.duration_seconds if selected_candidate is not None else None)
                or (right.duration_seconds if right is not None else None)
                or self._coerce_optional_float(existing_item.get("asset_duration_seconds"))
            )
            license_name = (
                (selected_candidate.license_name if selected_candidate is not None else None)
                or (right.license_name if right is not None else None)
                or self._coerce_str_or_none(existing_item.get("license_name"))
            )
            license_url = (
                (selected_candidate.license_url if selected_candidate is not None else None)
                or (right.license_url if right is not None else None)
                or self._coerce_str_or_none(existing_item.get("license_url"))
            )
            attribution_required = (
                bool(selected_candidate.attribution_required)
                if selected_candidate is not None
                else bool(right.attribution_required)
                if right is not None
                else bool(existing_item.get("attribution_required"))
            )
            attribution_text = (
                (selected_candidate.attribution_text if selected_candidate is not None else None)
                or (right.attribution_text if right is not None else None)
                or self._coerce_str_or_none(existing_item.get("attribution_text"))
            )
            restriction_flags = (
                list(selected_candidate.restriction_flags)
                if selected_candidate is not None
                else list(right.restriction_flags)
                if right is not None
                else [
                    str(flag).strip()
                    for flag in existing_item.get("restriction_flags") or []
                    if str(flag).strip()
                ]
                if isinstance(existing_item.get("restriction_flags"), list)
                else []
            )
            clips.append(
                {
                    "scene_id": scene.scene_id,
                    "clip_name": scene.clip_name,
                    "heading": scene.heading,
                    "seconds": round(scene.seconds, 3),
                    "channel_vocabulary_key": self._channel_profile_key(),
                    "matched_channel_terms": list(scene_context.get("matched_terms") or []),
                    "search_terms": list(scene.search_terms),
                    "effective_search_queries": list(scene_context.get("effective_queries") or []),
                    "visual_strategy": normalize_news_visual_strategy(scene.visual_strategy, "stock"),
                    "editorial_source_id": editorial_source_id,
                    "editorial_source_title": editorial_source.title if editorial_source is not None else None,
                    "editorial_source_publisher": editorial_source.publisher if editorial_source is not None else None,
                    "editorial_source_domain": editorial_source.domain if editorial_source is not None else None,
                    "asset_provider": scene.asset_provider,
                    "asset_path": scene.asset_path,
                    "source_asset_id": right.source_asset_id if right else None,
                    "source_url": right.source_url if right else None,
                    "creator_name": right.creator_name if right else None,
                    "asset_media_type": asset_media_type,
                    "asset_width": asset_width,
                    "asset_height": asset_height,
                    "asset_duration_seconds": round(asset_duration, 3) if asset_duration is not None else None,
                    "license_name": license_name,
                    "license_url": license_url,
                    "attribution_required": attribution_required,
                    "attribution_text": attribution_text,
                    "restriction_flags": restriction_flags,
                    "montage_asset_count": len(montage_assets),
                    "montage_assets": montage_assets,
                    "candidate_count": len(candidate_payloads),
                    "candidates": candidate_payloads,
                }
            )

        payload = {
            "title": plan.title,
            "summary": plan.summary,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "asset_keywords": list(self.config.asset_keywords),
            "channel_vocabulary_key": self._channel_profile_key(),
            "clips": clips,
        }
        self._write_json(self.paths["clip_catalog"], payload)

    def _news_overlay_vf(self, *, clip: TimelineClip, output_clip: Path, base_vf: str) -> str:
        if normalize_news_visual_strategy(clip.visual_strategy, "stock") not in {"news-source-screenshot", "source-card"}:
            return base_vf
        source_id = self._coerce_str_or_none(clip.editorial_source_id)
        if not source_id:
            return base_vf

        source = self._approved_editorial_sources_by_id.get(source_id)
        if source is None:
            approved = self._approved_editorial_sources or self._load_approved_editorial_sources()
            self._approved_editorial_sources_by_id = {item.source_id: item for item in approved}
            source = self._approved_editorial_sources_by_id.get(source_id)
        if source is None:
            return base_vf

        label_file = output_clip.with_suffix(".source_label.txt")
        meta_file = output_clip.with_suffix(".source_meta.txt")
        mode_file = output_clip.with_suffix(".source_mode.txt")
        label_text = source.publisher or source.domain or "Editorial source"
        meta_bits = [source.published_at[:10]] if source.published_at else []
        if source.domain:
            meta_bits.append(source.domain)
        mode_text = "Approved article screenshot" if clip.visual_strategy == "news-source-screenshot" else "Approved source card"
        self._write_text(label_file, label_text + "\n")
        self._write_text(meta_file, " | ".join(bit for bit in meta_bits if bit) + "\n")
        self._write_text(mode_file, mode_text + "\n")
        label_path = self._escape_drawtext_path(label_file)
        meta_path = self._escape_drawtext_path(meta_file)
        mode_path = self._escape_drawtext_path(mode_file)

        overlay = (
            "drawbox=x=iw*0.05:y=ih*0.80:w=iw*0.58:h=ih*0.14:color=black@0.52:t=fill,"
            "drawbox=x=iw*0.05:y=ih*0.80:w=iw*0.58:h=3:color=0xeab308:t=fill,"
            f"drawtext=textfile='{label_path}':fontcolor=white:fontsize=28:x=w*0.07:y=h*0.83,"
            f"drawtext=textfile='{meta_path}':fontcolor=0xe5e7eb:fontsize=22:x=w*0.07:y=h*0.875,"
            f"drawtext=textfile='{mode_path}':fontcolor=0xfde68a:fontsize=18:x=w*0.07:y=h*0.912"
        )
        return f"{base_vf},{overlay}"

    def _render_single_clip(
        self,
        clip: TimelineClip,
        output_clip: Path,
        index: int,
        *,
        metrics_section: str = "render",
    ) -> None:
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
            result = self._run_ffmpeg_video_command(
                command,
                timeout=900,
                preset="medium",
                crf=20,
                metrics_section=metrics_section,
            )
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
            result = self._run_ffmpeg_video_command(
                command,
                timeout=900,
                preset="medium",
                crf=20,
                metrics_section=metrics_section,
            )
            if result.returncode != 0 and self._is_drawtext_missing_error(result.stderr):
                self._ffmpeg_drawtext_available = False
                raise RuntimeError(
                    "ffmpeg drawtext filter is required for intro/outro text rendering. "
                    "Install an ffmpeg build with drawtext support or disable intro/outro bookends."
                )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to render outro clip: {result.stderr.strip()}")
            return

        if clip.scene_id == "__tail":
            command = self._tail_hold_clip_command(output_clip=output_clip, duration=duration)
            result = self._run_ffmpeg_video_command(
                command,
                timeout=900,
                preset="medium",
                crf=20,
                metrics_section=metrics_section,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to render tail hold clip: {result.stderr.strip()}")
            return

        source = Path(clip.source_path) if clip.source_path else None
        vf_fallback = self._base_clip_vf()

        if source and source.exists():
            ext = source.suffix.lower()
            if ext in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
                source_duration: float | None = None
                try:
                    source_duration = self._media_duration(source)
                except Exception as exc:
                    self._log(f"Could not probe source duration for {clip.scene_id}: {exc}")

                still_fallback: Path | None = None
                if (
                    source_duration is not None
                    and source_duration < max(0.2, duration - 0.05)
                    and self._should_use_still_fallback_for_short_video(source_duration, duration)
                ):
                    still_fallback = self._resolve_bookend_background(source, f"scene-{index:04d}")
                    if still_fallback is not None:
                        self._warn(
                            f"Source clip {source.name} is only {source_duration:.1f}s for scene {clip.scene_id} "
                            f"({duration:.1f}s target). Using a still-frame motion fallback to avoid replay."
                        )
                        source = still_fallback
                        ext = source.suffix.lower()

                if ext in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
                    vf_primary = self._clip_vf(
                        source_kind="video",
                        index=index,
                        duration=duration,
                        scene_id=clip.scene_id,
                    )
                    vf_retry = vf_fallback
                    vf_primary = self._news_overlay_vf(clip=clip, output_clip=output_clip, base_vf=vf_primary)
                    vf_retry = self._news_overlay_vf(clip=clip, output_clip=output_clip, base_vf=vf_retry)

                    input_args = ["-i", str(source)]
                    if source_duration is not None and source_duration > duration + 0.08:
                        max_seek = max(0.0, source_duration - duration)
                        if max_seek > 0:
                            bucket = self._stable_pivot(f"{clip.scene_id}:{source.name}", 1000)
                            seek_ratio = float(bucket) / 999.0
                            seek_seconds = max_seek * seek_ratio
                            input_args = ["-ss", f"{seek_seconds:.3f}", "-i", str(source)]
                    elif source_duration is not None and source_duration < max(0.2, duration - 0.05):
                        timing_vf_primary = self._timed_short_video_vf(
                            base_vf=vf_primary,
                            source_duration=source_duration,
                            target_duration=duration,
                        )
                        timing_vf_retry = self._timed_short_video_vf(
                            base_vf=vf_fallback,
                            source_duration=source_duration,
                            target_duration=duration,
                        )
                        if timing_vf_primary != vf_primary:
                            vf_primary = timing_vf_primary
                        if timing_vf_retry != vf_fallback:
                            vf_retry = timing_vf_retry

                    command = [
                        "ffmpeg",
                        "-y",
                        *input_args,
                        "-t",
                        f"{duration:.3f}",
                        "-vf",
                        vf_primary,
                        "-an",
                        *self._video_encode_args(preset="medium", crf=20),
                        str(output_clip),
                    ]
                    fallback = [*command]
                    fallback[fallback.index("-vf") + 1] = vf_retry
                elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
                    vf_primary = self._clip_vf(
                        source_kind="image",
                        index=index,
                        duration=duration,
                        scene_id=clip.scene_id,
                    )
                    vf_primary = self._news_overlay_vf(clip=clip, output_clip=output_clip, base_vf=vf_primary)
                    vf_retry = self._news_overlay_vf(clip=clip, output_clip=output_clip, base_vf=vf_fallback)
                    fallback = [
                        "ffmpeg",
                        "-y",
                        *self._looped_image_input_args(source),
                        "-t",
                        f"{duration:.3f}",
                        "-vf",
                        vf_retry,
                        "-an",
                        *self._video_encode_args(preset="medium", crf=20),
                        str(output_clip),
                    ]
                    montage_paths = self._scene_montage_image_paths(clip.scene_id, source)
                    if self._can_render_image_montage(clip, montage_paths):
                        command = self._image_montage_clip_command(
                            clip=clip,
                            output_clip=output_clip,
                            index=index,
                            image_paths=montage_paths,
                        )
                    else:
                        command = [
                            "ffmpeg",
                            "-y",
                            *self._looped_image_input_args(source),
                            "-t",
                            f"{duration:.3f}",
                            "-vf",
                            vf_primary,
                            "-an",
                            *self._video_encode_args(preset="medium", crf=20),
                            str(output_clip),
                        ]
                else:
                    vf_primary = self._clip_vf(
                        source_kind="placeholder",
                        index=index,
                        duration=duration,
                        scene_id=clip.scene_id,
                    )
                    command = self._placeholder_clip_command(output_clip, duration, index, vf_primary)
                    fallback = self._placeholder_clip_command(output_clip, duration, index, vf_fallback)
            elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
                vf_primary = self._clip_vf(
                    source_kind="image",
                    index=index,
                    duration=duration,
                    scene_id=clip.scene_id,
                )
                vf_primary = self._news_overlay_vf(clip=clip, output_clip=output_clip, base_vf=vf_primary)
                vf_retry = self._news_overlay_vf(clip=clip, output_clip=output_clip, base_vf=vf_fallback)
                fallback = [
                    "ffmpeg",
                    "-y",
                    *self._looped_image_input_args(source),
                    "-t",
                    f"{duration:.3f}",
                    "-vf",
                    vf_retry,
                    "-an",
                    *self._video_encode_args(preset="medium", crf=20),
                    str(output_clip),
                ]
                montage_paths = self._scene_montage_image_paths(clip.scene_id, source)
                if self._can_render_image_montage(clip, montage_paths):
                    command = self._image_montage_clip_command(
                        clip=clip,
                        output_clip=output_clip,
                        index=index,
                        image_paths=montage_paths,
                    )
                else:
                    command = [
                        "ffmpeg",
                        "-y",
                        *self._looped_image_input_args(source),
                        "-t",
                        f"{duration:.3f}",
                        "-vf",
                        vf_primary,
                        "-an",
                        *self._video_encode_args(preset="medium", crf=20),
                        str(output_clip),
                    ]
            else:
                vf_primary = self._clip_vf(
                    source_kind="placeholder",
                    index=index,
                    duration=duration,
                    scene_id=clip.scene_id,
                )
                command = self._placeholder_clip_command(output_clip, duration, index, vf_primary)
                fallback = self._placeholder_clip_command(output_clip, duration, index, vf_fallback)
        else:
            vf_primary = self._clip_vf(
                source_kind="placeholder",
                index=index,
                duration=duration,
                scene_id=clip.scene_id,
            )
            command = self._placeholder_clip_command(output_clip, duration, index, vf_primary)
            fallback = self._placeholder_clip_command(output_clip, duration, index, vf_fallback)

        result = self._run_ffmpeg_video_command(
            command,
            timeout=900,
            preset="medium",
            crf=20,
            metrics_section=metrics_section,
        )
        if result.returncode != 0:
            self._warn(
                f"Primary visual filter failed for {clip.scene_id}; retrying simplified filter. "
                f"Details: {result.stderr.strip()}"
            )
            result = self._run_ffmpeg_video_command(
                fallback,
                timeout=900,
                preset="medium",
                crf=20,
                metrics_section=metrics_section,
            )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to render clip {clip.scene_id}: {result.stderr.strip()}")

    def _tail_hold_clip_command(self, output_clip: Path, duration: float) -> list[str]:
        palette = self._bookend_palette(style=self._normalized_bookend_style(), is_intro=False)
        fade = min(0.45, max(0.2, duration * 0.2))
        fade_out_start = max(0.0, duration - fade)
        return [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
            "-t",
            f"{duration:.3f}",
            "-vf",
            (
                f"drawbox=x=0:y=0:w=iw:h=ih:color={palette['overlay']}:t=fill,"
                f"drawbox=x=iw*0.10:y=ih*0.26:w=iw*0.80:h=ih*0.50:color={palette['panel']}:t=fill,"
                f"drawbox=x=iw*0.16:y=ih*0.735:w=iw*0.68:h=2:color={palette['accent']}:t=fill,"
                f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"
            ),
            "-an",
            *self._video_encode_args(preset="medium", crf=20),
            str(output_clip),
        ]

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

        if style == "corner-fade":
            title_font = max(32, min(72, int(title_font * 0.98)))
            title_lines_text = [line.strip() for line in wrapped_title.splitlines() if line.strip()]
            longest_line = max((len(line) for line in title_lines_text), default=12)
            title_lines = max(1, len(title_lines_text))
            line_spacing = max(12, int(self.config.height * 0.014))
            margin_x = int(self.config.width * 0.08)
            margin_y = int(self.config.height * 0.12)
            underline_gap = int(self.config.height * 0.028)
            underline_width = max(
                int(self.config.width * 0.14),
                min(int(self.config.width * 0.32), int(longest_line * title_font * 0.42)),
            )
            underline_height = max(3, int(self.config.height * 0.006))
            title_block_height = title_lines * title_font + max(0, title_lines - 1) * line_spacing
            underline_y = margin_y + title_block_height + underline_gap
            fade_in_duration = min(0.55, max(0.28, duration * 0.18))
            fade = min(0.42, max(0.24, duration * 0.16))
            fade_out_start = max(fade_in_duration + 0.9, duration - fade)
            fade_alpha = (
                f"'if(lt(t,{fade_in_duration:.3f}),t/{fade_in_duration:.3f},"
                f"if(lt(t,{fade_out_start:.3f}),1,"
                f"if(lt(t,{duration:.3f}),({duration:.3f}-t)/{fade:.3f},0)))'"
            )
            title_parts: list[str] = []
            for line_index, line in enumerate(title_lines_text, start=1):
                line_file = output_clip.with_suffix(f".intro_line_{line_index}.txt")
                self._write_text(line_file, line + "\n")
                line_textfile = self._escape_drawtext_path(line_file)
                line_y = margin_y + (line_index - 1) * (title_font + line_spacing)
                title_parts.append(
                    f"drawtext=textfile='{line_textfile}':fontcolor={palette['title_color']}:fontsize={title_font}:"
                    f"x={margin_x}:y={line_y}:alpha={fade_alpha}:"
                    "shadowcolor=black@0.94:shadowx=3:shadowy=3"
                )
            input_args: list[str]
            background_filter: str
            if background_image and background_image.exists():
                input_args = self._looped_image_input_args(background_image)
                background_filter = (
                    f"scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.config.width}:{self.config.height},"
                    "zoompan=z='min(zoom+0.00045,1.04)':"
                    "x='iw/2-(iw/zoom/2)':"
                    "y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={self.config.width}x{self.config.height}:fps={self.config.fps},"
                    "eq=contrast=1.02:saturation=0.98:brightness=-0.015,"
                    "unsharp=5:5:0.2:5:5:0.0,"
                    f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.12:t=fill"
                )
            else:
                input_args = [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
                ]
                background_filter = "format=yuv420p"

            input_args.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={palette['accent']}:s={underline_width}x{underline_height}:r={self.config.fps}",
                ]
            )
            filter_parts = [
                f"[0:v]{background_filter},{','.join(title_parts)}[corner_text]",
                f"[1:v]format=rgba,fade=t=in:st=0:d={fade_in_duration:.3f}:alpha=1,"
                f"fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}:alpha=1[corner_line]",
                f"[corner_text][corner_line]overlay=x={margin_x}:y={underline_y}:format=auto[v]",
            ]

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
                *self._video_encode_args(preset="medium", crf=20),
                str(output_clip),
            ]

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
                input_args.extend(self._looped_image_input_args(background_image))
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
                input_args.extend(self._looped_image_input_args(logo_image))

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
                *self._video_encode_args(preset="medium", crf=20),
                str(output_clip),
            ]

        input_args: list[str]
        visual_prefix = ""
        if background_image and background_image.exists():
            input_args = self._looped_image_input_args(background_image)
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
            *self._video_encode_args(preset="medium", crf=20),
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

        if style == "corner-fade":
            cta_text = self._normalized_bookend_tagline(self.config.outro_tagline)
            if not cta_text or cta_text.lower() == "watch next":
                cta_text = "Remember to like the video and subscribe"

            wrapped_cta = self._wrap_bookend_text(cta_text)
            first_lines_text = [line.strip() for line in wrapped_title.splitlines() if line.strip()]
            second_lines_text = [line.strip() for line in wrapped_cta.splitlines() if line.strip()]
            line_spacing = max(12, int(self.config.height * 0.014))
            primary_font = max(30, min(68, int(title_font * 0.92)))
            secondary_font = max(26, min(56, int(primary_font * 0.82)))
            underline_gap = int(self.config.height * 0.028)
            block_center_y = int(self.config.height * 0.61)
            segment_gap = max(0.10, duration * 0.025)
            first_end = max(duration * 0.45, (duration * 0.5) - (segment_gap * 0.5))
            second_start = min(duration - 0.55, (duration * 0.5) + (segment_gap * 0.5))

            def _segment_alpha(start: float, end: float) -> str:
                span = max(0.45, end - start)
                fade_window = min(0.42, max(0.22, span * 0.24))
                hold_end = max(start + fade_window, end - fade_window)
                return (
                    f"'if(lt(t,{start:.3f}),0,"
                    f"if(lt(t,{start + fade_window:.3f}),(t-{start:.3f})/{fade_window:.3f},"
                    f"if(lt(t,{hold_end:.3f}),1,"
                    f"if(lt(t,{end:.3f}),({end:.3f}-t)/{fade_window:.3f},0))))'"
                )

            first_alpha = _segment_alpha(0.0, first_end)
            second_alpha = _segment_alpha(second_start, duration)

            def _build_center_text_parts(lines: list[str], font_size: int, alpha_expr: str, prefix: str) -> tuple[list[str], int]:
                block_height = len(lines) * font_size + max(0, len(lines) - 1) * line_spacing
                block_top = block_center_y - int(block_height / 2)
                parts: list[str] = []
                for line_index, line in enumerate(lines, start=1):
                    line_file = output_clip.with_suffix(f".{prefix}_line_{line_index}.txt")
                    self._write_text(line_file, line + "\n")
                    line_textfile = self._escape_drawtext_path(line_file)
                    line_y = block_top + (line_index - 1) * (font_size + line_spacing)
                    parts.append(
                        f"drawtext=textfile='{line_textfile}':fontcolor={palette['title_color']}:fontsize={font_size}:"
                        f"x=(w-text_w)/2:y={line_y}:alpha={alpha_expr}:"
                        "shadowcolor=black@0.94:shadowx=3:shadowy=3"
                    )
                underline_y = block_top + block_height + underline_gap
                return parts, underline_y

            first_parts, first_underline_y = _build_center_text_parts(
                first_lines_text,
                primary_font,
                first_alpha,
                "outro_first",
            )
            second_parts, second_underline_y = _build_center_text_parts(
                second_lines_text,
                secondary_font,
                second_alpha,
                "outro_second",
            )

            first_longest = max((len(line) for line in first_lines_text), default=16)
            second_longest = max((len(line) for line in second_lines_text), default=20)
            first_underline_width = max(
                int(self.config.width * 0.14),
                min(int(self.config.width * 0.34), int(first_longest * primary_font * 0.42)),
            )
            second_underline_width = max(
                int(self.config.width * 0.18),
                min(int(self.config.width * 0.48), int(second_longest * secondary_font * 0.42)),
            )
            underline_height = max(3, int(self.config.height * 0.006))

            input_args: list[str]
            background_filter: str
            if background_image and background_image.exists():
                input_args = self._looped_image_input_args(background_image)
                background_filter = (
                    f"scale={self.config.width}:{self.config.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.config.width}:{self.config.height},"
                    "zoompan=z='min(zoom+0.0004,1.035)':"
                    "x='iw/2-(iw/zoom/2)':"
                    "y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={self.config.width}x{self.config.height}:fps={self.config.fps},"
                    "eq=contrast=1.02:saturation=0.97:brightness=-0.02,"
                    "unsharp=5:5:0.2:5:5:0.0,"
                    "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.18:t=fill"
                )
            else:
                input_args = [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={palette['base']}:s={self.config.width}x{self.config.height}:r={self.config.fps}",
                ]
                background_filter = "format=yuv420p"

            input_args.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={palette['accent']}:s={first_underline_width}x{underline_height}:r={self.config.fps}",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={palette['accent']}:s={second_underline_width}x{underline_height}:r={self.config.fps}",
                ]
            )
            filter_parts = [
                f"[0:v]{background_filter},{','.join(first_parts + second_parts)}[corner_outro_text]",
                f"[1:v]format=rgba,fade=t=in:st=0:d={min(0.42, max(0.22, first_end * 0.24)):.3f}:alpha=1,"
                f"fade=t=out:st={max(0.0, first_end - min(0.42, max(0.22, first_end * 0.24))):.3f}:"
                f"d={min(0.42, max(0.22, first_end * 0.24)):.3f}:alpha=1[corner_outro_line_1]",
                f"[2:v]format=rgba,fade=t=in:st={second_start:.3f}:"
                f"d={min(0.42, max(0.22, (duration - second_start) * 0.24)):.3f}:alpha=1,"
                f"fade=t=out:st={max(second_start, duration - min(0.42, max(0.22, (duration - second_start) * 0.24))):.3f}:"
                f"d={min(0.42, max(0.22, (duration - second_start) * 0.24)):.3f}:alpha=1[corner_outro_line_2]",
                f"[corner_outro_text][corner_outro_line_1]overlay=x=(W-w)/2:y={first_underline_y}:format=auto[corner_outro_stage_1]",
                f"[corner_outro_stage_1][corner_outro_line_2]overlay=x=(W-w)/2:y={second_underline_y}:format=auto[v]",
            ]

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
                *self._video_encode_args(preset="medium", crf=20),
                str(output_clip),
            ]

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
                input_args.extend(self._looped_image_input_args(background_image))
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
                input_args.extend(self._looped_image_input_args(logo_image))

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
                *self._video_encode_args(preset="medium", crf=20),
                str(output_clip),
            ]

        input_args: list[str]
        visual_prefix = ""
        if background_image and background_image.exists():
            input_args = self._looped_image_input_args(background_image)
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
            *self._video_encode_args(preset="medium", crf=20),
            str(output_clip),
        ]

    def _normalized_bookend_style(self) -> str:
        style = str(self.config.bookend_style or "minimal-clean").strip().lower()
        if style not in {"minimal-clean", "cinematic-subtle", "brand-image-motion", "corner-fade"}:
            return "minimal-clean"
        return style

    def _preferred_video_encoder(self) -> str:
        if self._hw_encoder_checked:
            return self._hw_encoder or "libx264"

        self._hw_encoder_checked = True
        self._hw_encoder = None
        if sys.platform != "darwin":
            return "libx264"

        result = self._run_command(["ffmpeg", "-hide_banner", "-encoders"], timeout=30, check=False)
        catalog = f"{result.stdout}\n{result.stderr}".lower()
        if re.search(r"\bh264_videotoolbox\b", catalog):
            self._hw_encoder = "h264_videotoolbox"
        return self._hw_encoder or "libx264"

    def _target_video_bitrate_kbps(self, *, crf: int) -> int:
        pixels = int(self.config.width) * int(self.config.height)
        if pixels <= (640 * 360):
            bitrate = 2500
        elif pixels <= (1280 * 720):
            bitrate = 5000
        elif pixels <= (1920 * 1080):
            bitrate = 8000
        else:
            bitrate = 12000

        if crf <= 18:
            bitrate = int(bitrate * 1.2)
        elif crf >= 22:
            bitrate = int(bitrate * 0.85)
        return max(1500, bitrate)

    def _libx264_encode_args(self, *, preset: str, crf: int) -> list[str]:
        return [
            "-c:v",
            "libx264",
            "-preset",
            str(preset),
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
        ]

    def _video_encode_args(
        self,
        *,
        preset: str,
        crf: int,
        encoder: str | None = None,
    ) -> list[str]:
        selected_encoder = str(encoder or self._preferred_video_encoder() or "libx264").strip().lower()
        if selected_encoder == "h264_videotoolbox":
            bitrate_kbps = self._target_video_bitrate_kbps(crf=crf)
            return [
                "-c:v",
                "h264_videotoolbox",
                "-allow_sw",
                "1",
                "-b:v",
                f"{bitrate_kbps}k",
                "-pix_fmt",
                "yuv420p",
            ]
        return self._libx264_encode_args(preset=preset, crf=crf)

    def _replace_video_encode_args(
        self,
        command: list[str],
        *,
        preset: str,
        crf: int,
        encoder: str,
    ) -> list[str]:
        if "-c:v" not in command or "-pix_fmt" not in command:
            return list(command)

        c_index = command.index("-c:v")
        pix_index = command.index("-pix_fmt", c_index)
        end_index = min(len(command), pix_index + 2)
        return [
            *command[:c_index],
            *self._video_encode_args(preset=preset, crf=crf, encoder=encoder),
            *command[end_index:],
        ]

    def _run_ffmpeg_video_command(
        self,
        command: list[str],
        *,
        timeout: int,
        preset: str,
        crf: int,
        metrics_section: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = self._run_command(command, timeout=timeout, check=False)
        if result.returncode == 0:
            return result

        preferred_encoder = self._preferred_video_encoder()
        if preferred_encoder == "libx264":
            return result
        if "-c:v" not in command:
            return result

        encoder_index = command.index("-c:v") + 1
        current_encoder = str(command[encoder_index]).strip().lower() if encoder_index < len(command) else ""
        if current_encoder != preferred_encoder:
            return result

        fallback_command = self._replace_video_encode_args(
            command,
            preset=preset,
            crf=crf,
            encoder="libx264",
        )
        fallback_result = self._run_command(fallback_command, timeout=timeout, check=False)
        if fallback_result.returncode == 0:
            if metrics_section:
                self._increment_optimization_counter(metrics_section, "hw_encoder_fallbacks")
                self._set_optimization_value(metrics_section, "video_encoder_fallback", "libx264")
            self._log(
                f"Video encode fallback triggered for {metrics_section or 'render'}; "
                f"retrying {preferred_encoder} output with libx264."
            )
            return fallback_result
        return fallback_result

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
        if style == "corner-fade":
            if is_intro:
                return {
                    "base": "#16181c",
                    "overlay": "#05070b@0.00",
                    "panel": "#ffffff@0.00",
                    "accent": "#d62828@0.96",
                    "title_color": "white",
                    "subtitle_color": "white@0.82",
                }
            return {
                "base": "#16181c",
                "overlay": "#05070b@0.00",
                "panel": "#ffffff@0.00",
                "accent": "#d62828@0.94",
                "title_color": "white",
                "subtitle_color": "white@0.82",
            }

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
            *self._video_encode_args(preset="medium", crf=20),
            str(output_clip),
        ]

    def _burn_subtitles(
        self,
        input_mp4: Path,
        subtitles_ass: Path,
        output_mp4: Path,
        *,
        audio_input: Path | None = None,
        metrics_section: str = "render",
    ) -> None:
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
        command = ["ffmpeg", "-y", "-i", str(input_mp4)]
        if audio_input is not None:
            command.extend(["-i", str(audio_input), "-map", "0:v:0", "-map", "1:a:0"])
        command.extend(
            [
                "-vf",
                filter_value,
                *self._video_encode_args(preset="medium", crf=18),
                "-c:a",
                "aac" if audio_input is not None else "copy",
            ]
        )
        if audio_input is not None:
            command.extend(["-b:a", "192k", "-shortest"])
        command.append(str(output_mp4))
        with self._timed_optimization_block(metrics_section, "subtitle_burn_seconds"):
            result = self._run_ffmpeg_video_command(
                command,
                timeout=2400,
                preset="medium",
                crf=18,
                metrics_section=metrics_section,
            )
        if result.returncode != 0:
            lowered = (result.stderr or "").lower()
            if "no such filter" in lowered and "subtitles" in lowered:
                self._ffmpeg_subtitles_available = False
                raise RuntimeError(
                    "ffmpeg subtitles filter is required for burned subtitles. "
                    "Install an ffmpeg build with libass/subtitles support or disable burned subtitles."
                )
            self._warn(f"Subtitle burn-in failed; shipping video without burned subtitles. Details: {result.stderr.strip()}")
            if audio_input is None:
                shutil.copy2(input_mp4, output_mp4)
                return

            mux = self._run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_mp4),
                    "-i",
                    str(audio_input),
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
                    str(output_mp4),
                ],
                timeout=1200,
                check=False,
            )
            if mux.returncode != 0:
                raise RuntimeError(f"Failed to mux narration and video after subtitle fallback: {mux.stderr.strip()}")

    def _ffmpeg_subtitles_filter(self, subtitles_ass: Path) -> str:
        value = str(subtitles_ass.resolve())
        value = value.replace("\\", "\\\\")
        value = value.replace(":", "\\:")
        value = value.replace("'", "\\'")
        value = value.replace(",", "\\,")
        value = value.replace(";", "\\;")
        return f"subtitles=filename='{value}'"

    def _write_manifest_and_publish_artifacts(self, plan: ScriptPlan, rights: list[AssetRight]) -> dict[str, Any]:
        credits_payload = self._build_youtube_description_credits_payload(rights)
        youtube_description_text = str(credits_payload.get("youtube_description_text") or "").rstrip()
        self._write_text(self.paths["youtube_credits"], youtube_description_text + "\n")
        manifest = self._build_manifest(plan, rights, credits_payload=credits_payload)
        self._write_json(self.paths["manifest"], manifest)
        return manifest

    def _build_youtube_description_credits_payload(self, rights: list[AssetRight]) -> dict[str, Any]:
        required_lines: list[str] = []
        optional_lines: list[str] = []
        editorial_lines: list[str] = []
        provider_warnings: list[str] = []
        seen_asset_keys: set[str] = set()
        ordered_rights = sorted(
            self._iter_right_credit_records(rights),
            key=lambda item: (
                item.scene_id,
                item.source_platform,
                item.source_asset_id or "",
                item.source_url,
            ),
        )

        for right in ordered_rights:
            asset_key = self._asset_uniqueness_key_from_right(right)
            if asset_key in seen_asset_keys:
                continue
            seen_asset_keys.add(asset_key)

            provider_name = self._provider_display_name(right.source_platform)
            creator_name = (right.creator_name or "").strip() or "Unknown creator"
            license_name = (right.license_name or "").strip() or "Unknown license"
            source_url = str(right.source_url or "").strip()
            attribution_text = (right.attribution_text or "").strip()
            restriction_flags = sorted(
                {
                    str(flag).strip().lower()
                    for flag in right.restriction_flags
                    if str(flag).strip()
                }
            )

            required_parts = [provider_name]
            if attribution_text:
                required_parts.append(f"Credit: {attribution_text}")
            else:
                required_parts.append(f"Creator: {creator_name}")
            if source_url:
                required_parts.append(f"Source: {source_url}")
            required_parts.append(f"License: {license_name}")

            optional_parts = [provider_name, f"Creator: {creator_name}"]
            if source_url:
                optional_parts.append(f"Source: {source_url}")
            optional_parts.append(f"License: {license_name}")

            optional_lines.append("- " + " | ".join(optional_parts))
            if right.attribution_required:
                required_lines.append("- " + " | ".join(required_parts))
                if not attribution_text:
                    provider_warnings.append(
                        f"{provider_name}: attribution is required, but no provider-specific credit text was recorded."
                    )

            if restriction_flags:
                provider_warnings.append(
                    f"{provider_name}: restriction flags = {', '.join(restriction_flags)}"
                )

        editorial_sources = (
            (self._approved_editorial_sources or self._load_approved_editorial_sources())
            if self._news_mode_enabled()
            else []
        )
        seen_editorial_ids: set[str] = set()
        for source in editorial_sources:
            if source.source_id in seen_editorial_ids:
                continue
            seen_editorial_ids.add(source.source_id)
            editorial_lines.append(
                "- "
                + " | ".join(
                    part
                    for part in (
                        source.publisher or source.domain,
                        source.title,
                        f"Source: {source.article_url}",
                    )
                    if part
                )
            )

        required_description_block = ""
        if required_lines:
            required_description_block = "Visual credits:\n" + "\n".join(required_lines)

        optional_source_block = "Visual sources:\n"
        if optional_lines:
            optional_source_block += "\n".join(optional_lines)
        else:
            optional_source_block += "- No external stock assets were resolved for this run."

        editorial_sources_block = ""
        if editorial_lines:
            editorial_sources_block = "Editorial sources consulted:\n" + "\n".join(editorial_lines)

        unique_warnings = sorted({warning for warning in provider_warnings if warning.strip()})
        text_lines = [
            "Visual Credits (YouTube Description)",
            "",
            "Required credits:",
        ]
        if required_lines:
            text_lines.extend(required_lines)
        else:
            text_lines.append("- No mandatory attribution entries were detected for this run.")

        text_lines.extend(["", "Optional source provenance:"])
        if optional_lines:
            text_lines.extend(optional_lines)
        else:
            text_lines.append("- No external stock assets were resolved for this run.")

        if editorial_lines:
            text_lines.extend(["", "Editorial sources consulted:"])
            text_lines.extend(editorial_lines)

        if unique_warnings:
            text_lines.extend(["", "Provider warnings:"])
            text_lines.extend(f"- {warning}" for warning in unique_warnings)

        return {
            "youtube_description_path": str(self.paths["youtube_credits"].resolve()),
            "required_entry_count": len(required_lines),
            "editorial_entry_count": len(editorial_lines),
            "required_description_block": required_description_block,
            "optional_source_block": optional_source_block,
            "editorial_sources_block": editorial_sources_block,
            "provider_warnings": unique_warnings,
            "youtube_description_text": "\n".join(text_lines).strip(),
        }

    def _build_manifest(
        self,
        plan: ScriptPlan,
        rights: list[AssetRight],
        *,
        credits_payload: dict[str, Any],
    ) -> dict[str, Any]:
        del plan
        out_files = [
            self.paths["final_mp4"],
            self.paths["final_srt"],
            self.paths["captions_ass"],
            self.paths["script"],
            self.paths["timeline"],
            self.paths["clip_catalog"],
            self.paths["youtube_credits"],
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
        tts_policy = describe_tts_config_policy(self.config)
        editorial_sources = (
            (self._approved_editorial_sources or self._load_approved_editorial_sources())
            if self._news_mode_enabled()
            else []
        )
        distinct_editorial_domains = sorted({source.domain for source in editorial_sources if source.domain})

        return {
            "manifest_version": 1,
            "project_id": self.config.project_dir.name,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "app_version": "0.1.0",
            "pipeline_version": "v1-local-720p",
            "config": {
                "content_mode": self._content_mode(),
                "minutes": self.config.minutes,
                "asset_keywords": list(self.config.asset_keywords),
                "news_feed_urls": list(self._news_feed_urls()),
                "news_max_age_hours": int(self.config.news_max_age_hours),
                "news_max_candidates": int(self.config.news_max_candidates),
                "news_min_approved_sources": int(self.config.news_min_approved_sources),
                "news_jurisdiction": self._news_jurisdiction(),
                "news_require_manual_source_approval": bool(self.config.news_require_manual_source_approval),
                "enable_pexels_provider": self.config.enable_pexels_provider,
                "enable_pixabay_provider": self.config.enable_pixabay_provider,
                "enable_coverr_provider": self.config.enable_coverr_provider,
                "enable_vecteezy_provider": self.config.enable_vecteezy_provider,
                "allow_image_assets": self._image_assets_enabled(),
                "allow_attribution_required_assets": self.config.allow_attribution_required_assets,
                "asset_mode": self._normalized_asset_mode(),
                "asset_shortlist_size": self.config.asset_shortlist_size,
                "resolution": f"{self.config.width}x{self.config.height}",
                "fps": self.config.fps,
                "video_effects": self.config.video_effects,
                "image_motion_style": self._normalized_image_motion_style(),
                "include_intro": self.config.include_intro,
                "include_outro": self.config.include_outro,
                "intro_seconds": self.config.intro_seconds,
                "outro_seconds": self.config.outro_seconds,
                "outro_text": self.config.outro_text,
                "outro_spoken_text": self.config.outro_spoken_text,
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
                "subtitle_preset": normalize_subtitle_preset(self.config.subtitle_preset, "regular"),
                "subtitle_position": normalize_subtitle_position(self.config.subtitle_position, "bottom"),
                "subtitle_accent_color": normalize_subtitle_accent_color(
                    self.config.subtitle_accent_color,
                    "sunflower",
                ),
                "subtitle_box_color": normalize_subtitle_box_color(
                    self.config.subtitle_box_color,
                    normalize_subtitle_accent_color(self.config.subtitle_accent_color, "sunflower"),
                ),
                "subtitle_bold": bool(self.config.subtitle_bold),
                "subtitle_outline": bool(self.config.subtitle_outline),
                "caption_font_scale": self.config.caption_font_scale,
                "caption_bottom_ratio": self.config.caption_bottom_ratio,
                "duration_tolerance_ratio": self.config.duration_tolerance_ratio,
                "target_speech_wpm": self.config.target_speech_wpm,
                "voice_profile": self.config.voice_profile,
                "voice_speed": self.config.voice_speed,
                "melo_language": self.config.melo_language,
                "melo_speaker": self.config.melo_speaker,
                "kokoro_lang_code": normalize_kokoro_lang_code(self.config.kokoro_lang_code),
                "kokoro_voice": self._resolve_kokoro_voice() if self.config.tts_engine == "kokoro" else self.config.kokoro_voice,
                "piper_voice_id": self.config.piper_voice_id,
                "piper_speaker_id": self.config.piper_speaker_id,
                "ollama_model": self.config.ollama_model,
            },
            "inputs": {
                "prompt_file": str(self.paths["prompt"].resolve()),
                "prompt_sha256": self._file_sha256(self.paths["prompt"]),
            },
            "assets": [record.to_dict() for record in rights],
            "credits": {
                "youtube_description_path": str(credits_payload.get("youtube_description_path") or ""),
                "required_entry_count": int(credits_payload.get("required_entry_count") or 0),
                "editorial_entry_count": int(credits_payload.get("editorial_entry_count") or 0),
                "required_description_block": str(credits_payload.get("required_description_block") or ""),
                "optional_source_block": str(credits_payload.get("optional_source_block") or ""),
                "editorial_sources_block": str(credits_payload.get("editorial_sources_block") or ""),
                "provider_warnings": list(credits_payload.get("provider_warnings") or []),
            },
            "editorial_sources": [source.to_dict() for source in editorial_sources],
            "provider_usage": dict(self.asset_stats.get("provider_usage") or {}),
            "models": [
                {
                    "model_id": self.config.ollama_model if self.config.script_engine == "ollama" else "template-script",
                    "provider": "ollama" if self.config.script_engine == "ollama" else "local-template",
                    "model_license": "Verify model-specific license",
                },
                {
                    "model_id": str(tts_policy.get("model_id") or self.config.tts_engine),
                    "provider": str(tts_policy.get("provider") or "local"),
                    "model_license": str(tts_policy.get("license_name") or "Verify voice/model license"),
                    "model_source": str(tts_policy.get("source") or "local"),
                    "model_source_url": tts_policy.get("source_url"),
                    "voice_selection": str(tts_policy.get("voice_display") or self.config.tts_engine),
                },
            ],
            "tools": tools,
            "outputs": outputs,
            "policy_decisions": [
                {
                    "rule_id": "strict-commercial-safe",
                    "result": "allow" if self.config.strict_commercial_safe else "warn",
                    "reason": "Strict mode enabled" if self.config.strict_commercial_safe else "Strict mode disabled",
                },
                {
                    "rule_id": "news-mode",
                    "result": "allow" if self._news_mode_enabled() else "allow",
                    "reason": (
                        "News mode enabled with editorial-source review artifacts."
                        if self._news_mode_enabled()
                        else "Standard explainer mode enabled."
                    ),
                },
                {
                    "rule_id": "news-jurisdiction",
                    "result": "allow",
                    "reason": f"News workflow jurisdiction is {self._news_jurisdiction()}.",
                },
                {
                    "rule_id": "news-source-review",
                    "result": (
                        "allow"
                        if (not self._news_mode_enabled() or (len(editorial_sources) >= max(1, int(self.config.news_min_approved_sources)) and len(distinct_editorial_domains) >= max(1, int(self.config.news_min_approved_sources))))
                        else "deny"
                    ),
                    "reason": (
                        f"{len(editorial_sources)} approved editorial sources across {len(distinct_editorial_domains)} domains."
                        if self._news_mode_enabled()
                        else "Not applicable outside news mode."
                    ),
                },
                {
                    "rule_id": "news-screenshot-use",
                    "result": "allow" if self._news_mode_enabled() else "allow",
                    "reason": (
                        "Third-party reuse is limited to approved article screenshots and internal source cards."
                        if self._news_mode_enabled()
                        else "Not applicable outside news mode."
                    ),
                },
                {
                    "rule_id": "tts-selection",
                    "result": str(tts_policy.get("policy_result") or "warn"),
                    "reason": str(tts_policy.get("reason") or "TTS policy could not be resolved"),
                    "engine": str(tts_policy.get("engine") or self.config.tts_engine),
                    "voice_selection": str(tts_policy.get("voice_display") or self.config.tts_engine),
                    "license_name": str(tts_policy.get("license_name") or ""),
                },
                {
                    "rule_id": "allow-attribution-required-assets",
                    "result": "allow" if self.config.allow_attribution_required_assets else "deny",
                    "reason": (
                        "Attribution-required sources remain eligible and should be credited in description exports."
                        if self.config.allow_attribution_required_assets
                        else "Attribution-required sources are excluded from asset resolution."
                    ),
                },
                {
                    "rule_id": "asset-mode",
                    "result": "allow",
                    "reason": f"Asset mode is set to {self._normalized_asset_mode()}.",
                },
                {
                    "rule_id": "image-motion-style",
                    "result": "allow",
                    "reason": f"Still-image motion style is set to {self._normalized_image_motion_style()}.",
                },
                {
                    "rule_id": "provider-pexels",
                    "result": "allow" if self.config.enable_pexels_provider else "deny",
                    "reason": "Pexels provider enabled" if self.config.enable_pexels_provider else "Pexels provider disabled",
                },
                {
                    "rule_id": "provider-pixabay",
                    "result": "allow" if self.config.enable_pixabay_provider else "deny",
                    "reason": "Pixabay provider enabled" if self.config.enable_pixabay_provider else "Pixabay provider disabled",
                },
                {
                    "rule_id": "provider-coverr",
                    "result": "allow" if self.config.enable_coverr_provider else "deny",
                    "reason": (
                        "Coverr experimental fallback provider enabled with local hourly request tracking."
                        if self.config.enable_coverr_provider
                        else "Coverr provider disabled"
                    ),
                },
                {
                    "rule_id": "provider-vecteezy",
                    "result": "allow" if self.config.enable_vecteezy_provider else "deny",
                    "reason": (
                        "Vecteezy experimental fallback provider enabled with quota tracking."
                        if self.config.enable_vecteezy_provider
                        else "Vecteezy provider disabled"
                    ),
                },
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

    def _optimization_section(self, key: str) -> dict[str, Any]:
        with self._stats_lock:
            section = self.optimization_stats.get(key)
            if isinstance(section, dict):
                return section
            section = {}
            self.optimization_stats[key] = section
            return section

    def _increment_optimization_counter(self, section: str, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            bucket = self.optimization_stats.get(section)
            if not isinstance(bucket, dict):
                bucket = {}
                self.optimization_stats[section] = bucket
            current = bucket.get(key)
            try:
                base = int(current)
            except (TypeError, ValueError):
                base = 0
            bucket[key] = base + int(amount)

    def _record_optimization_time(self, section: str, key: str, elapsed: float) -> None:
        with self._stats_lock:
            bucket = self.optimization_stats.get(section)
            if not isinstance(bucket, dict):
                bucket = {}
                self.optimization_stats[section] = bucket
            current = bucket.get(key)
            try:
                base = float(current)
            except (TypeError, ValueError):
                base = 0.0
            bucket[key] = round(base + max(0.0, float(elapsed)), 3)

    def _set_optimization_value(self, section: str, key: str, value: Any) -> None:
        with self._stats_lock:
            bucket = self.optimization_stats.get(section)
            if not isinstance(bucket, dict):
                bucket = {}
                self.optimization_stats[section] = bucket
            bucket[key] = value

    @contextmanager
    def _timed_optimization_block(self, section: str, key: str) -> Any:
        started = time.perf_counter()
        try:
            yield
        finally:
            self._record_optimization_time(section, key, time.perf_counter() - started)

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

    def _prepare_tts_narration_text(self, text: str) -> str:
        cleaned = self._clean_narration_text(text)
        if not cleaned:
            return cleaned
        script_language = str(self.config.script_language or "").strip().lower()
        if not script_language.startswith("pt"):
            return cleaned
        return self._expand_bible_references_for_tts(cleaned)

    def _normalize_bible_book_key(self, raw_book: str) -> str:
        lowered = str(raw_book or "").strip().lower()
        lowered = re.sub(r"\s+", " ", lowered)
        lowered = lowered.replace(".", "")
        normalized = unicodedata.normalize("NFD", lowered)
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        return normalized

    def _spoken_bible_book_name(self, raw_book: str) -> str | None:
        normalized = self._normalize_bible_book_key(raw_book)
        if not normalized:
            return None
        return BIBLE_BOOK_SPOKEN_ALIASES.get(normalized)

    def _join_spoken_reference_parts(self, parts: list[str]) -> str:
        cleaned = [part.strip() for part in parts if part.strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} e {cleaned[1]}"
        return ", ".join(cleaned[:-1]) + f" e {cleaned[-1]}"

    def _spoken_bible_verses(self, raw_verses: str) -> str:
        normalized = re.sub(r"\s+", "", str(raw_verses or ""))
        if not normalized:
            return ""

        parts: list[str] = []
        for token in [part for part in normalized.split(".") if part]:
            if "-" in token:
                start, end = token.split("-", 1)
                if start.isdigit() and end.isdigit():
                    parts.append(f"{int(start)} ao {int(end)}")
                    continue
            if token.isdigit():
                parts.append(str(int(token)))
                continue
            return ""

        if not parts:
            return ""
        label = "versiculo" if len(parts) == 1 and " ao " not in parts[0] else "versiculos"
        return f"{label} {self._join_spoken_reference_parts(parts)}"

    def _expand_bible_references_for_tts(self, text: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            spoken_book = self._spoken_bible_book_name(match.group("book"))
            if not spoken_book:
                return match.group(0)

            chapter = str(match.group("chapter") or "").strip()
            verses = self._spoken_bible_verses(match.group("verses"))
            if not chapter.isdigit() or not verses:
                return match.group(0)

            chapter_text = str(int(chapter))
            if spoken_book == "Salmo":
                return f"{spoken_book} {chapter_text}, {verses}"
            return f"{spoken_book} capitulo {chapter_text}, {verses}"

        return BIBLE_REFERENCE_RE.sub(_replace, text)

    def _clean_narration_text(self, text: str) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        cleaned_parts: list[str] = []

        for paragraph in paragraphs:
            cleaned = re.sub(r"\s+", " ", paragraph).strip()
            if cleaned:
                cleaned_parts.append(cleaned)

        return "\n\n".join(cleaned_parts)

    def _base_clip_vf(self) -> str:
        w = str(self.config.width)
        h = str(self.config.height)
        fps = str(self.config.fps)
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"

    def _clip_vf(
        self,
        source_kind: str,
        index: int,
        *,
        duration: float | None = None,
        scene_id: str | None = None,
    ) -> str:
        preset = str(self.config.video_effects or "clean").strip().lower()
        if preset not in {"clean", "subtle-motion", "dynamic"}:
            preset = "clean"

        if preset == "clean" and source_kind != "image":
            return self._base_clip_vf()

        if source_kind == "image":
            return self._image_effect_vf(index=index, duration=duration, scene_id=scene_id)
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

    def _looped_image_input_args(self, image_path: Path, *, duration: float | None = None) -> list[str]:
        args = ["-loop", "1", "-framerate", str(max(1, self.config.fps))]
        if duration is not None:
            args.extend(["-t", f"{max(0.1, float(duration)):.3f}"])
        args.extend(["-i", str(image_path)])
        return args

    def _scene_montage_asset_payloads(self, scene_id: str) -> list[dict[str, Any]]:
        cached = self._scene_montage_assets.get(scene_id)
        if cached is not None:
            return [dict(item) for item in cached if isinstance(item, dict)]

        payload = self._load_json_state(self.paths["clip_catalog"]) or {}
        clips = payload.get("clips") if isinstance(payload, dict) else None
        if isinstance(clips, list):
            for item in clips:
                if not isinstance(item, dict):
                    continue
                candidate_scene_id = str(item.get("scene_id") or "").strip()
                if candidate_scene_id != scene_id:
                    continue
                montage_assets = item.get("montage_assets")
                if isinstance(montage_assets, list):
                    resolved = [dict(asset) for asset in montage_assets if isinstance(asset, dict)]
                    self._scene_montage_assets[scene_id] = resolved
                    return resolved
                break

        self._scene_montage_assets[scene_id] = []
        return []

    def _scene_montage_image_paths(self, scene_id: str, primary_source: Path | None) -> list[Path]:
        seen_paths: set[str] = set()
        montage_paths: list[Path] = []

        if primary_source is not None and primary_source.exists() and self._media_type_from_path(primary_source) == "image":
            resolved_primary = primary_source.resolve()
            montage_paths.append(resolved_primary)
            seen_paths.add(str(resolved_primary))

        for payload in self._scene_montage_asset_payloads(scene_id):
            local_path = str(payload.get("local_path") or "").strip()
            if not local_path:
                continue
            path = Path(local_path).expanduser().resolve()
            if not path.exists():
                continue
            if self._media_type_from_path(path) != "image":
                continue
            key = str(path)
            if key in seen_paths:
                continue
            montage_paths.append(path)
            seen_paths.add(key)

        return montage_paths

    def _can_render_image_montage(self, clip: TimelineClip, image_paths: list[Path]) -> bool:
        motion_style = self._normalized_image_motion_style()
        usable_count = min(
            len(image_paths),
            self._scene_montage_target_count(clip.seconds, style=motion_style),
        )
        if usable_count <= 1:
            return False

        montage_profile = self._montage_motion_profile(motion_style)
        crossfade = min(
            float(montage_profile["crossfade_max"]),
            max(float(montage_profile["crossfade_min"]), clip.seconds * float(montage_profile["crossfade_ratio"])),
        )
        min_visible_seconds = float(montage_profile["min_visible_seconds"])
        while usable_count > 1:
            segment_duration = (clip.seconds + crossfade * (usable_count - 1)) / usable_count
            if (segment_duration - crossfade) >= min_visible_seconds:
                return True
            usable_count -= 1
        return False

    def _image_montage_clip_command(
        self,
        *,
        clip: TimelineClip,
        output_clip: Path,
        index: int,
        image_paths: list[Path],
    ) -> list[str]:
        motion_style = self._normalized_image_motion_style()
        montage_profile = self._montage_motion_profile(motion_style)
        usable_count = min(
            len(image_paths),
            self._scene_montage_target_count(clip.seconds, style=motion_style),
        )
        if usable_count <= 1:
            raise ValueError("Image montage requires at least two image assets")

        selected_paths = list(image_paths[:usable_count])
        crossfade = min(
            float(montage_profile["crossfade_max"]),
            max(float(montage_profile["crossfade_min"]), clip.seconds * float(montage_profile["crossfade_ratio"])),
        )
        min_visible_seconds = float(montage_profile["min_visible_seconds"])
        segment_motion_style = str(montage_profile["segment_motion_style"])
        while len(selected_paths) > 1:
            segment_duration = (clip.seconds + crossfade * (len(selected_paths) - 1)) / len(selected_paths)
            if (segment_duration - crossfade) >= min_visible_seconds:
                break
            selected_paths.pop()
        if len(selected_paths) <= 1:
            raise ValueError("Scene duration is too short for a stable image montage")

        segment_duration = (clip.seconds + crossfade * (len(selected_paths) - 1)) / len(selected_paths)
        transition_step = segment_duration - crossfade
        command = ["ffmpeg", "-y"]
        filter_parts: list[str] = []

        for asset_index, path in enumerate(selected_paths):
            command.extend(self._looped_image_input_args(path, duration=segment_duration))
            motion_vf = self._image_effect_vf(
                index=(index * 17) + asset_index,
                duration=segment_duration,
                scene_id=f"{clip.scene_id}:montage:{asset_index}",
                style=segment_motion_style,
            )
            filter_parts.append(f"[{asset_index}:v]{motion_vf},format=yuv444p,settb=AVTB[m{asset_index}]")

        current_label = "m0"
        for asset_index in range(1, len(selected_paths)):
            offset = transition_step * asset_index
            next_label = f"m{asset_index}"
            output_label = "v" if asset_index == (len(selected_paths) - 1) else f"mx{asset_index}"
            filter_parts.append(
                f"[{current_label}][{next_label}]xfade=transition=fade:duration={crossfade:.3f}:offset={offset:.3f}[{output_label}]"
            )
            current_label = output_label

        return [
            *command,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            f"[{current_label}]",
            "-an",
            *self._video_encode_args(preset="medium", crf=20),
            str(output_clip),
        ]

    def _image_motion_variant(
        self,
        *,
        index: int,
        duration: float | None,
        style: str,
        scene_id: str | None = None,
    ) -> tuple[dict[str, float | str], float]:
        def _blend(start: float, end: float, ratio: float) -> float:
            clamped_ratio = max(0.0, min(1.0, float(ratio)))
            return float(start + ((end - start) * clamped_ratio))

        def _clamp(value: float, lower: float, upper: float) -> float:
            return float(max(lower, min(upper, value)))

        duration_value = max(0.8, float(duration or 6.0))
        total_frames = max(12, int(round(duration_value * max(1, self.config.fps))))
        if style == "static":
            return (
                {
                    "family": "static",
                    "start_x": 0.5,
                    "end_x": 0.5,
                    "start_y": 0.5,
                    "end_y": 0.5,
                    "start_zoom": 1.0,
                    "end_zoom": 1.0,
                },
                float(total_frames),
            )

        style_settings = {
            "slow": {
                "push_start_zoom": (1.08, 1.12),
                "push_end_zoom": (1.14, 1.18),
                "pan_start_zoom": (1.18, 1.24),
                "pan_end_zoom": (1.2, 1.26),
                "pullback_start_zoom": (1.22, 1.28),
                "pullback_end_zoom": (1.08, 1.12),
                "travel": (0.22, 0.32),
                "vertical_travel": (0.05, 0.1),
                "center_bias": 0.05,
            },
            "balanced": {
                "push_start_zoom": (1.1, 1.15),
                "push_end_zoom": (1.18, 1.24),
                "pan_start_zoom": (1.22, 1.3),
                "pan_end_zoom": (1.24, 1.32),
                "pullback_start_zoom": (1.28, 1.36),
                "pullback_end_zoom": (1.1, 1.16),
                "travel": (0.3, 0.46),
                "vertical_travel": (0.08, 0.14),
                "center_bias": 0.075,
            },
            "fast": {
                "push_start_zoom": (1.14, 1.2),
                "push_end_zoom": (1.24, 1.34),
                "pan_start_zoom": (1.28, 1.38),
                "pan_end_zoom": (1.3, 1.42),
                "pullback_start_zoom": (1.36, 1.5),
                "pullback_end_zoom": (1.12, 1.2),
                "travel": (0.4, 0.62),
                "vertical_travel": (0.12, 0.2),
                "center_bias": 0.1,
            },
        }
        settings = style_settings.get(style, style_settings["slow"])

        if duration_value < 4.5:
            duration_bucket = "short"
            duration_travel_scale = 0.82
        elif duration_value < 8.0:
            duration_bucket = "medium"
            duration_travel_scale = 1.0
        else:
            duration_bucket = "long"
            duration_travel_scale = 1.08

        seed_base = f"{self.config.project_dir.name}:{scene_id or f'index-{index}'}:{index}:{duration_bucket}:{style}"
        families: list[str] = ["push-center", "push-left-to-center", "push-right-to-center"]
        if duration_value >= 4.0:
            families.extend(["pan-left", "pan-right"])
        if duration_value >= 4.5:
            families.extend(["drift-diagonal"])
            if style != "slow":
                families.extend(["pan-left", "pan-right"])
        if duration_value >= 8.0 and style != "slow":
            families.append("pull-back")

        family = families[self._stable_pivot(f"{seed_base}:family", len(families))]
        travel_ratio = self._stable_pivot(f"{seed_base}:travel", 1000) / 999.0
        push_start_zoom_ratio = self._stable_pivot(f"{seed_base}:push-start-zoom", 1000) / 999.0
        push_end_zoom_ratio = self._stable_pivot(f"{seed_base}:push-end-zoom", 1000) / 999.0
        pan_start_zoom_ratio = self._stable_pivot(f"{seed_base}:pan-start-zoom", 1000) / 999.0
        pan_end_zoom_ratio = self._stable_pivot(f"{seed_base}:pan-end-zoom", 1000) / 999.0
        pullback_start_zoom_ratio = self._stable_pivot(f"{seed_base}:pullback-start-zoom", 1000) / 999.0
        pullback_end_zoom_ratio = self._stable_pivot(f"{seed_base}:pullback-end-zoom", 1000) / 999.0
        vertical_ratio = self._stable_pivot(f"{seed_base}:vertical", 1000) / 999.0
        center_y_ratio = self._stable_pivot(f"{seed_base}:center-y", 1000) / 999.0
        diagonal_direction = -1.0 if self._stable_pivot(f"{seed_base}:mirror", 2) else 1.0

        travel = _blend(*settings["travel"], travel_ratio) * duration_travel_scale
        vertical_travel = _blend(*settings["vertical_travel"], vertical_ratio) * duration_travel_scale
        center_y_offset = _blend(-settings["center_bias"], settings["center_bias"], center_y_ratio)
        push_start_zoom = _blend(*settings["push_start_zoom"], push_start_zoom_ratio)
        push_end_zoom = _blend(*settings["push_end_zoom"], push_end_zoom_ratio)
        pan_start_zoom = _blend(*settings["pan_start_zoom"], pan_start_zoom_ratio)
        pan_end_zoom = _blend(*settings["pan_end_zoom"], pan_end_zoom_ratio)
        pullback_start_zoom = _blend(*settings["pullback_start_zoom"], pullback_start_zoom_ratio)
        pullback_end_zoom = _blend(*settings["pullback_end_zoom"], pullback_end_zoom_ratio)

        center_x = 0.5
        center_y = _clamp(0.5 + center_y_offset, 0.28, 0.72)
        start_vertical_anchor = _clamp(center_y - (vertical_travel * 0.5 * diagonal_direction), 0.12, 0.88)
        end_vertical_anchor = _clamp(center_y + (vertical_travel * 0.5 * diagonal_direction), 0.12, 0.88)

        if family == "push-center":
            variant = {
                "family": family,
                "start_x": center_x,
                "end_x": center_x,
                "start_y": start_vertical_anchor,
                "end_y": end_vertical_anchor,
                "start_zoom": push_start_zoom,
                "end_zoom": push_end_zoom,
            }
        elif family == "push-left-to-center":
            variant = {
                "family": family,
                "start_x": _clamp(0.5 - travel, 0.06, 0.34),
                "end_x": center_x,
                "start_y": start_vertical_anchor,
                "end_y": center_y,
                "start_zoom": push_start_zoom,
                "end_zoom": push_end_zoom,
            }
        elif family == "push-right-to-center":
            variant = {
                "family": family,
                "start_x": _clamp(0.5 + travel, 0.66, 0.94),
                "end_x": center_x,
                "start_y": start_vertical_anchor,
                "end_y": center_y,
                "start_zoom": push_start_zoom,
                "end_zoom": push_end_zoom,
            }
        elif family == "pan-left":
            variant = {
                "family": family,
                "start_x": _clamp(0.5 + travel, 0.72, 0.96),
                "end_x": _clamp(0.5 - travel, 0.04, 0.28),
                "start_y": start_vertical_anchor,
                "end_y": end_vertical_anchor,
                "start_zoom": pan_start_zoom,
                "end_zoom": pan_end_zoom,
            }
        elif family == "pan-right":
            variant = {
                "family": family,
                "start_x": _clamp(0.5 - travel, 0.04, 0.28),
                "end_x": _clamp(0.5 + travel, 0.72, 0.96),
                "start_y": start_vertical_anchor,
                "end_y": end_vertical_anchor,
                "start_zoom": pan_start_zoom,
                "end_zoom": pan_end_zoom,
            }
        elif family == "pull-back":
            variant = {
                "family": family,
                "start_x": _clamp(0.5 + (travel * 0.45 * diagonal_direction), 0.18, 0.82),
                "end_x": center_x,
                "start_y": start_vertical_anchor,
                "end_y": center_y,
                "start_zoom": pullback_start_zoom,
                "end_zoom": pullback_end_zoom,
            }
        else:
            variant = {
                "family": "drift-diagonal",
                "start_x": _clamp(0.5 - (travel * diagonal_direction), 0.08, 0.92),
                "end_x": _clamp(0.5 + (travel * diagonal_direction), 0.08, 0.92),
                "start_y": start_vertical_anchor,
                "end_y": end_vertical_anchor,
                "start_zoom": pan_start_zoom,
                "end_zoom": pan_end_zoom,
            }

        return variant, float(total_frames)

    def _image_effect_vf(
        self,
        *,
        index: int,
        duration: float | None = None,
        scene_id: str | None = None,
        style: str | None = None,
    ) -> str:
        w = self.config.width
        h = self.config.height
        fps = self.config.fps
        style = self._normalized_image_motion_style(style)
        variant, total_frames = self._image_motion_variant(
            index=index,
            duration=duration,
            style=style,
            scene_id=scene_id,
        )

        oversample_factor = 3 if style in {"balanced", "fast"} else 2
        oversampled_w = max(w, w * oversample_factor)
        oversampled_h = max(h, h * oversample_factor)
        frame_denominator = max(1.0, total_frames - 1.0)
        raw_progress_expr = f"clip(n/{frame_denominator:.1f},0,1)"
        ease_window = 0.14 if style == "fast" else 0.16
        linear_divisor = 1.0 - ease_window
        ease_denominator = max(0.0001, 2.0 * ease_window * linear_divisor)
        progress_expr = (
            f"(if(lt({raw_progress_expr},{ease_window:.4f}),"
            f"(({raw_progress_expr})*({raw_progress_expr}))/{ease_denominator:.6f},"
            f"if(lt({raw_progress_expr},{1.0 - ease_window:.4f}),"
            f"(({raw_progress_expr})-{ease_window / 2.0:.6f})/{linear_divisor:.6f},"
            f"1-((1-({raw_progress_expr}))*(1-({raw_progress_expr})))/{ease_denominator:.6f})))"
        )
        start_zoom = float(variant["start_zoom"])
        end_zoom = float(variant["end_zoom"])
        start_x = float(variant["start_x"])
        end_x = float(variant["end_x"])
        start_y = float(variant["start_y"])
        end_y = float(variant["end_y"])
        zoom_delta = end_zoom - start_zoom
        x_delta = end_x - start_x
        y_delta = end_y - start_y
        zoom_expr = f"({start_zoom:.5f}+({zoom_delta:.5f})*{progress_expr})"
        x_anchor_expr = f"({start_x:.5f}+({x_delta:.5f})*{progress_expr})"
        y_anchor_expr = f"({start_y:.5f}+({y_delta:.5f})*{progress_expr})"

        filter_parts = [
            f"scale={oversampled_w}:{oversampled_h}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd",
            "format=gbrp",
            (
                "scale="
                f"w='iw*{zoom_expr}':"
                f"h='ih*{zoom_expr}':"
                "eval=frame:flags=lanczos+accurate_rnd"
            ),
            (
                "crop="
                f"w={oversampled_w}:h={oversampled_h}:"
                f"x='(iw-ow)*{x_anchor_expr}':"
                f"y='(ih-oh)*{y_anchor_expr}':"
                "exact=1"
            ),
            f"scale={w}:{h}:flags=lanczos+accurate_rnd",
            "setsar=1",
            f"fps={fps}",
        ]

        if style == "slow":
            filter_parts.append("eq=contrast=1.02:saturation=1.04:brightness=0.008")
        elif style == "balanced":
            filter_parts.append("eq=contrast=1.03:saturation=1.02:brightness=0.006")
            filter_parts.append("unsharp=5:5:0.22:5:5:0.0")
        elif style == "fast":
            filter_parts.append("eq=contrast=1.06:saturation=1.11:brightness=0.014")
            filter_parts.append("unsharp=5:5:0.42:5:5:0.0")

        return ",".join(filter_parts)

    def _timed_short_video_vf(self, base_vf: str, source_duration: float, target_duration: float) -> str:
        if source_duration >= target_duration - 0.05:
            return base_vf

        pad_duration = max(0.0, target_duration - max(0.05, source_duration))

        filters: list[str] = []
        filters.append(base_vf)
        if pad_duration > 0.04:
            filters.append(f"tpad=stop_mode=clone:stop_duration={pad_duration:.3f}")
        return ",".join(filters)

    def _should_use_still_fallback_for_short_video(self, source_duration: float, target_duration: float) -> bool:
        if source_duration <= 0.0:
            return False
        shortfall = target_duration - source_duration
        if shortfall <= 0.45:
            return False
        return source_duration < max(1.0, target_duration * 0.55)

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
            "news_stats": self.news_stats,
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
        cache_key: tuple[str, int, int] | None = None
        try:
            resolved_path = media_path.expanduser().resolve()
            stat = resolved_path.stat()
            cache_key = (str(resolved_path), int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            resolved_path = media_path

        if cache_key is not None and cache_key in self._duration_cache:
            self._increment_optimization_counter("media_probe", "cache_hits")
            return self._duration_cache[cache_key]

        self._increment_optimization_counter("media_probe", "cache_misses")
        with self._timed_optimization_block("media_probe", "ffprobe_seconds"):
            result = self._run_command(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(resolved_path),
                ],
                timeout=60,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

        try:
            duration = float((result.stdout or "0").strip())
        except ValueError as exc:
            raise RuntimeError("Could not parse media duration") from exc
        if cache_key is not None:
            self._duration_cache[cache_key] = duration
        return duration

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
        with self._warnings_lock:
            self.warnings.append(message)
        self._log_with_level(message, level="WARN")

    def _log_with_level(self, message: str, level: str) -> None:
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        line = f"{timestamp} [{level}] {message}"
        try:
            with self._log_lock:
                self.paths["run_log"].parent.mkdir(parents=True, exist_ok=True)
                with self.paths["run_log"].open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            pass

        if self.config.verbose or level in {"WARN", "ERROR"}:
            with self._log_lock:
                print(f"[local-video-mvp] {message}")
