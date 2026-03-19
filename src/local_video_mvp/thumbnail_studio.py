from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont

THUMBNAIL_STUDIO_SCHEMA_VERSION = 1
THUMBNAIL_STUDIO_ROOT = Path.home() / ".imagine" / "thumbnail-studio"
THUMBNAIL_STUDIO_SESSION_FILE = "session.json"
THUMBNAIL_STUDIO_CANVAS_SIZE = (1280, 720)
THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR = Path.home() / "Downloads"
THUMBNAIL_STUDIO_VARIANTS_PER_BATCH = 4
THUMBNAIL_STUDIO_PRESET_CHOICES = (
    "big-hook",
    "top-banner",
    "badge-hero",
    "minimal-product",
    "face-object-left",
)
THUMBNAIL_STUDIO_PALETTE_CHOICES = (
    "alert-red",
    "electric-cyan",
    "money-lime",
    "midnight-gold",
    "clean-white",
)
THUMBNAIL_STUDIO_DEFAULT_PRESET = "big-hook"
THUMBNAIL_STUDIO_DEFAULT_PALETTE = "alert-red"
THUMBNAIL_STUDIO_NEGATIVE_PROMPT = (
    "text, letters, watermark, logo, subtitles, caption, border, collage, split screen, "
    "blurry, low quality, duplicate subject, extra limbs, multiple faces, cluttered background"
)
THUMBNAIL_STUDIO_FONTFILE_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Trebuchet MS Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "what",
    "why",
    "with",
    "ao",
    "aos",
    "as",
    "com",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "pela",
    "pelas",
    "pelo",
    "pelos",
    "por",
    "que",
    "se",
    "sem",
    "sobre",
    "um",
    "uma",
}

THUMBNAIL_STUDIO_PRESET_SPECS: dict[str, dict[str, Any]] = {
    "big-hook": {
        "label": "Big Hook",
        "prompt_hint": "single dramatic hero subject on the right, clean negative space on the left, high contrast",
        "text_box": (0.05, 0.07, 0.53, 0.52),
        "text_align": "left",
        "font_scale": 1.18,
        "plate_strength": 0.82,
        "shade_side": "left",
        "badge_position": "top-right",
        "badge_enabled": True,
    },
    "top-banner": {
        "label": "Top Banner",
        "prompt_hint": "hero object or face centered lower frame, clean top area, studio lighting",
        "text_box": (0.08, 0.05, 0.84, 0.24),
        "text_align": "center",
        "font_scale": 1.14,
        "plate_strength": 0.9,
        "shade_side": "top",
        "badge_position": "bottom-right",
        "badge_enabled": True,
    },
    "badge-hero": {
        "label": "Badge + Hero",
        "prompt_hint": "dominant center hero subject, dynamic background depth, commercial thumbnail look",
        "text_box": (0.05, 0.60, 0.56, 0.28),
        "text_align": "left",
        "font_scale": 0.98,
        "plate_strength": 0.72,
        "shade_side": "bottom",
        "badge_position": "top-right",
        "badge_enabled": True,
    },
    "minimal-product": {
        "label": "Minimal Product",
        "prompt_hint": "clean isolated product render, simple background, modern studio advertisement, symmetry",
        "text_box": (0.09, 0.05, 0.82, 0.18),
        "text_align": "center",
        "font_scale": 1.26,
        "plate_strength": 0.22,
        "shade_side": "top",
        "badge_position": "bottom-right",
        "badge_enabled": False,
    },
    "face-object-left": {
        "label": "Face/Object Left",
        "prompt_hint": "main subject on the left side, empty space on the right, punchy commercial lighting",
        "text_box": (0.48, 0.08, 0.45, 0.54),
        "text_align": "right",
        "font_scale": 1.02,
        "plate_strength": 0.78,
        "shade_side": "right",
        "badge_position": "top-left",
        "badge_enabled": True,
    },
}

THUMBNAIL_STUDIO_PALETTES: dict[str, dict[str, str]] = {
    "alert-red": {
        "label": "Alert Red",
        "font": "#FFFFFF",
        "outline": "#111111",
        "badge_fill": "#E53935",
        "badge_text": "#FFFFFF",
        "accent": "#FF5A5A",
    },
    "electric-cyan": {
        "label": "Electric Cyan",
        "font": "#FFFFFF",
        "outline": "#0B1320",
        "badge_fill": "#06B6D4",
        "badge_text": "#FFFFFF",
        "accent": "#22D3EE",
    },
    "money-lime": {
        "label": "Money Lime",
        "font": "#FFFFFF",
        "outline": "#121212",
        "badge_fill": "#6CCB3D",
        "badge_text": "#091109",
        "accent": "#B8FF62",
    },
    "midnight-gold": {
        "label": "Midnight Gold",
        "font": "#FFFFFF",
        "outline": "#090909",
        "badge_fill": "#D9A441",
        "badge_text": "#1A1207",
        "accent": "#FFD36B",
    },
    "clean-white": {
        "label": "Clean White",
        "font": "#101010",
        "outline": "#F5F5F5",
        "badge_fill": "#111111",
        "badge_text": "#FFFFFF",
        "accent": "#FFFFFF",
    },
}


@dataclass
class ThumbnailStudioVariant:
    variant_id: str
    image_path: str
    composited_path: str
    seed: int
    preset: str
    headline_text: str
    badge_text: str
    score_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThumbnailStudioVariant":
        return cls(
            variant_id=str(payload.get("variant_id") or "").strip(),
            image_path=str(payload.get("image_path") or "").strip(),
            composited_path=str(payload.get("composited_path") or "").strip(),
            seed=int(payload.get("seed") or 0),
            preset=_normalize_preset(payload.get("preset")),
            headline_text=str(payload.get("headline_text") or "").strip(),
            badge_text=str(payload.get("badge_text") or "").strip(),
            score_notes=[
                str(item).strip()
                for item in payload.get("score_notes") or []
                if str(item).strip()
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "image_path": self.image_path,
            "composited_path": self.composited_path,
            "seed": int(self.seed),
            "preset": _normalize_preset(self.preset),
            "headline_text": self.headline_text,
            "badge_text": self.badge_text,
            "score_notes": list(self.score_notes),
        }


@dataclass
class ThumbnailStudioSession:
    schema_version: int = THUMBNAIL_STUDIO_SCHEMA_VERSION
    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    prompt: str = ""
    style_hint: str = ""
    enhanced_prompt: str = ""
    headline_text: str = ""
    badge_text: str = ""
    preset: str = THUMBNAIL_STUDIO_DEFAULT_PRESET
    palette: str = THUMBNAIL_STUDIO_DEFAULT_PALETTE
    seed: int = 0
    selected_variant_id: str | None = None
    variants: list[ThumbnailStudioVariant] = field(default_factory=list)
    exported_path: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThumbnailStudioSession":
        variants_raw = payload.get("variants")
        if not isinstance(variants_raw, list):
            variants_raw = []
        return cls(
            schema_version=int(payload.get("schema_version") or THUMBNAIL_STUDIO_SCHEMA_VERSION),
            session_id=str(payload.get("session_id") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
            updated_at=str(payload.get("updated_at") or "").strip(),
            prompt=str(payload.get("prompt") or "").strip(),
            style_hint=str(payload.get("style_hint") or "").strip(),
            enhanced_prompt=str(payload.get("enhanced_prompt") or "").strip(),
            headline_text=str(payload.get("headline_text") or "").strip(),
            badge_text=str(payload.get("badge_text") or "").strip(),
            preset=_normalize_preset(payload.get("preset")),
            palette=_normalize_palette(payload.get("palette")),
            seed=int(payload.get("seed") or 0),
            selected_variant_id=_optional_str(payload.get("selected_variant_id")),
            variants=[
                ThumbnailStudioVariant.from_dict(item)
                for item in variants_raw
                if isinstance(item, dict)
            ],
            exported_path=_optional_str(payload.get("exported_path")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": THUMBNAIL_STUDIO_SCHEMA_VERSION,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "prompt": self.prompt,
            "style_hint": self.style_hint,
            "enhanced_prompt": self.enhanced_prompt,
            "headline_text": self.headline_text,
            "badge_text": self.badge_text,
            "preset": _normalize_preset(self.preset),
            "palette": _normalize_palette(self.palette),
            "seed": int(self.seed),
            "selected_variant_id": self.selected_variant_id,
            "variants": [item.to_dict() for item in self.variants],
            "exported_path": self.exported_path,
        }


def thumbnail_studio_root() -> Path:
    THUMBNAIL_STUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    return THUMBNAIL_STUDIO_ROOT.resolve()


def thumbnail_studio_session_dir(session_id: str) -> Path:
    return (thumbnail_studio_root() / session_id).resolve()


def thumbnail_studio_session_path(session_dir: Path) -> Path:
    return session_dir.expanduser().resolve() / THUMBNAIL_STUDIO_SESSION_FILE


def list_thumbnail_studio_sessions(*, limit: int = 20) -> list[ThumbnailStudioSession]:
    root = thumbnail_studio_root()
    sessions: list[tuple[float, ThumbnailStudioSession]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        session_path = thumbnail_studio_session_path(entry)
        if not session_path.exists():
            continue
        try:
            session = load_thumbnail_studio_session(entry)
        except Exception:
            continue
        sessions.append((entry.stat().st_mtime, session))
    sessions.sort(key=lambda item: item[0], reverse=True)
    return [session for _, session in sessions[: max(1, int(limit))]]


def load_thumbnail_studio_session(session_dir: Path) -> ThumbnailStudioSession:
    resolved_dir = session_dir.expanduser().resolve()
    payload = json.loads(thumbnail_studio_session_path(resolved_dir).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Thumbnail studio session JSON is invalid.")
    session = ThumbnailStudioSession.from_dict(payload)
    if not session.session_id:
        session.session_id = resolved_dir.name
    return session


def save_thumbnail_studio_session(session: ThumbnailStudioSession) -> Path:
    if not session.session_id:
        raise RuntimeError("Thumbnail studio session is missing session_id.")
    session_dir = thumbnail_studio_session_dir(session.session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    if not session.created_at:
        session.created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    session.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = session.to_dict()
    serialized = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    session_path = thumbnail_studio_session_path(session_dir)
    session_path.write_text(serialized, encoding="utf-8")
    return session_path


def create_thumbnail_studio_session(
    *,
    prompt: str,
    style_hint: str = "",
    badge_text: str = "",
    use_ollama_hooks: bool = True,
    ollama_model: str = "qwen2.5:14b",
    notify: Callable[[str], None] | None = None,
) -> ThumbnailStudioSession:
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise ValueError("Thumbnail prompt cannot be empty.")
    session_id = _thumbnail_session_id(prompt_text)
    enhanced_prompt, headline_text, badge_suggestion = _thumbnail_prompt_package(
        prompt_text,
        style_hint=style_hint,
        use_ollama_hooks=use_ollama_hooks,
        ollama_model=ollama_model,
        notify=notify,
    )
    session = ThumbnailStudioSession(
        session_id=session_id,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        prompt=prompt_text,
        style_hint=str(style_hint or "").strip(),
        enhanced_prompt=enhanced_prompt,
        headline_text=headline_text,
        badge_text=str(badge_text or "").strip() or badge_suggestion,
        preset=THUMBNAIL_STUDIO_DEFAULT_PRESET,
        palette=THUMBNAIL_STUDIO_DEFAULT_PALETTE,
        seed=_stable_seed(prompt_text, salt="thumbnail-session"),
    )
    save_thumbnail_studio_session(session)
    return session


def selected_thumbnail_variant(session: ThumbnailStudioSession) -> ThumbnailStudioVariant | None:
    selected_id = str(session.selected_variant_id or "").strip()
    for variant in session.variants:
        if variant.variant_id == selected_id:
            return variant
    return session.variants[0] if session.variants else None


def generate_thumbnail_studio_variants(
    session: ThumbnailStudioSession,
    *,
    comfyui_url: str = THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL,
    variant_count: int = THUMBNAIL_STUDIO_VARIANTS_PER_BATCH,
    append: bool = True,
    notify: Callable[[str], None] | None = None,
) -> list[ThumbnailStudioVariant]:
    resolved_url = _normalize_comfyui_url(comfyui_url)
    _ensure_comfyui_available(resolved_url)

    session_dir = thumbnail_studio_session_dir(session.session_id)
    raw_dir = session_dir / "raw"
    composited_dir = session_dir / "composited"
    raw_dir.mkdir(parents=True, exist_ok=True)
    composited_dir.mkdir(parents=True, exist_ok=True)

    existing_count = len(session.variants)
    presets = list(THUMBNAIL_STUDIO_PRESET_CHOICES)
    generated: list[ThumbnailStudioVariant] = []
    prompt_text = str(session.enhanced_prompt or session.prompt).strip()
    if not prompt_text:
        raise RuntimeError("Thumbnail session has no prompt to generate from.")

    def _worker(offset: int) -> ThumbnailStudioVariant:
        variant_index = existing_count + offset + 1
        preset_key = presets[(variant_index - 1) % len(presets)]
        variant_id = f"variant-{variant_index:04d}"
        seed = _stable_seed(session.prompt, salt=f"{session.seed}:{variant_id}:{preset_key}")
        raw_path = raw_dir / f"{variant_id}.png"
        composited_path = composited_dir / f"{variant_id}.jpg"
        prompt_for_variant = _variant_generation_prompt(
            prompt_text,
            style_hint=session.style_hint,
            preset=preset_key,
        )
        _generate_comfyui_image(
            comfyui_url=resolved_url,
            prompt_text=prompt_for_variant,
            negative_prompt=THUMBNAIL_STUDIO_NEGATIVE_PROMPT,
            seed=seed,
            output_path=raw_path,
            filename_prefix=f"imagine-{session.session_id}-{variant_id}",
        )
        variant = ThumbnailStudioVariant(
            variant_id=variant_id,
            image_path=str(raw_path.relative_to(session_dir)),
            composited_path=str(composited_path.relative_to(session_dir)),
            seed=seed,
            preset=preset_key,
            headline_text=session.headline_text,
            badge_text=session.badge_text,
            score_notes=_variant_score_notes(
                headline_text=session.headline_text,
                badge_text=session.badge_text,
                preset=preset_key,
            ),
        )
        render_thumbnail_studio_variant(
            session=session,
            variant=variant,
            notify=notify,
        )
        if notify is not None:
            notify(f"Generated thumbnail variant {variant.variant_id} ({_preset_label(preset_key)}).")
        return variant

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(variant_count))) as executor:
        futures = [executor.submit(_worker, index) for index in range(max(1, int(variant_count)))]
        for future in concurrent.futures.as_completed(futures):
            generated.append(future.result())

    generated.sort(key=lambda item: item.variant_id)
    if append:
        session.variants.extend(generated)
    else:
        session.variants = list(generated)
    if generated and not session.selected_variant_id:
        session.selected_variant_id = generated[0].variant_id
        session.preset = generated[0].preset
    elif generated and session.selected_variant_id == generated[0].variant_id:
        session.preset = generated[0].preset
    save_thumbnail_studio_session(session)
    return generated


def render_thumbnail_studio_variant(
    *,
    session: ThumbnailStudioSession,
    variant: ThumbnailStudioVariant,
    notify: Callable[[str], None] | None = None,
) -> Path:
    session_dir = thumbnail_studio_session_dir(session.session_id)
    raw_path = (session_dir / variant.image_path).resolve()
    if not raw_path.exists():
        raise RuntimeError(f"Thumbnail source image was not found: {raw_path}")
    output_path = (session_dir / variant.composited_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _compose_thumbnail_image(
        source_path=raw_path,
        output_path=output_path,
        headline_text=str(session.headline_text or variant.headline_text or "").strip(),
        badge_text=str(session.badge_text or variant.badge_text or "").strip(),
        preset=_normalize_preset(variant.preset or session.preset),
        palette=_normalize_palette(session.palette),
    )
    variant.headline_text = str(session.headline_text or variant.headline_text or "").strip()
    variant.badge_text = str(session.badge_text or variant.badge_text or "").strip()
    variant.preset = _normalize_preset(variant.preset or session.preset)
    variant.score_notes = _variant_score_notes(
        headline_text=variant.headline_text,
        badge_text=variant.badge_text,
        preset=variant.preset,
    )
    if notify is not None:
        notify(f"Rendered thumbnail variant {variant.variant_id}.")
    return output_path


def rerender_thumbnail_studio_session(
    session: ThumbnailStudioSession,
    *,
    notify: Callable[[str], None] | None = None,
) -> list[Path]:
    outputs: list[Path] = []
    for variant in session.variants:
        variant.preset = _normalize_preset(variant.preset or session.preset)
        outputs.append(render_thumbnail_studio_variant(session=session, variant=variant, notify=notify))
    save_thumbnail_studio_session(session)
    return outputs


def export_thumbnail_studio_variant(
    session: ThumbnailStudioSession,
    *,
    export_dir: Path | None = None,
    notify: Callable[[str], None] | None = None,
) -> Path:
    variant = selected_thumbnail_variant(session)
    if variant is None:
        raise RuntimeError("No thumbnail variants are available to export yet.")
    session_dir = thumbnail_studio_session_dir(session.session_id)
    source_path = (session_dir / variant.composited_path).resolve()
    if not source_path.exists():
        render_thumbnail_studio_variant(session=session, variant=variant, notify=notify)
    target_dir = (export_dir or THUMBNAIL_STUDIO_DEFAULT_EXPORT_DIR).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    export_path = target_dir / f"{_slugify(session.prompt)[:64] or session.session_id}-thumbnail.jpg"
    shutil.copy2(source_path, export_path)
    session.exported_path = str(export_path)
    save_thumbnail_studio_session(session)
    if notify is not None:
        notify(f"Exported thumbnail to {export_path}.")
    return export_path


def thumbnail_studio_session_preview_path(session: ThumbnailStudioSession) -> Path | None:
    variant = selected_thumbnail_variant(session)
    if variant is None:
        return None
    path = (thumbnail_studio_session_dir(session.session_id) / variant.composited_path).resolve()
    return path if path.exists() else None


def comfyui_endpoint_reachable(comfyui_url: str) -> tuple[bool, str]:
    resolved_url = _normalize_comfyui_url(comfyui_url)
    try:
        response = requests.get(f"{resolved_url}/system_stats", timeout=5)
        if int(response.status_code) < 400:
            return True, "reachable"
        return False, f"HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def palette_label(palette: str) -> str:
    return THUMBNAIL_STUDIO_PALETTES.get(_normalize_palette(palette), {}).get("label", palette)


def preset_label(preset: str) -> str:
    return _preset_label(preset)


def ollama_thumbnail_prompt_enabled(value: Any) -> bool:
    return bool(value)


def _thumbnail_prompt_package(
    prompt_text: str,
    *,
    style_hint: str,
    use_ollama_hooks: bool,
    ollama_model: str,
    notify: Callable[[str], None] | None = None,
) -> tuple[str, str, str]:
    enhanced_prompt = _build_enhanced_thumbnail_prompt(prompt_text, style_hint=style_hint)
    headline = _default_headline_text(prompt_text)
    badge = ""
    if use_ollama_hooks and shutil.which("ollama") is not None:
        package = _ollama_thumbnail_prompt_package(
            prompt_text,
            style_hint=style_hint,
            ollama_model=ollama_model,
            notify=notify,
        )
        if package is not None:
            enhanced_prompt = package.get("enhanced_prompt") or enhanced_prompt
            headline = package.get("headline_text") or headline
            badge = package.get("badge_text") or badge
    return enhanced_prompt, headline, badge


def _ollama_thumbnail_prompt_package(
    prompt_text: str,
    *,
    style_hint: str,
    ollama_model: str,
    notify: Callable[[str], None] | None = None,
) -> dict[str, str] | None:
    instruction = textwrap.dedent(
        f"""
        Return compact JSON only with keys "enhanced_prompt", "headline_text", and "badge_text".
        The goal is a modern, professional, curiosity-driven YouTube thumbnail.
        Rules:
        - enhanced_prompt: one sentence for an image model, 16:9, single dominant subject, strong contrast, clean negative space for headline, no text baked into image.
        - headline_text: 2 to 4 words max.
        - badge_text: optional short badge like DAY 97, $599, NOW, or blank.
        Prompt: {prompt_text}
        Style hint: {style_hint or "(none)"}
        """
    ).strip()
    try:
        completed = subprocess.run(
            ["ollama", "run", str(ollama_model or "qwen2.5:14b").strip() or "qwen2.5:14b", instruction],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        if notify is not None:
            notify(f"WARN: Ollama thumbnail helper failed: {exc}")
        return None
    if int(completed.returncode or 0) != 0:
        if notify is not None:
            notify(f"WARN: Ollama thumbnail helper exited with code {completed.returncode}.")
        return None
    stdout = str(completed.stdout or "").strip()
    if not stdout:
        return None
    match = re.search(r"\{.*\}", stdout, flags=re.DOTALL)
    candidate_text = match.group(0) if match else stdout
    try:
        payload = json.loads(candidate_text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "enhanced_prompt": str(payload.get("enhanced_prompt") or "").strip(),
        "headline_text": str(payload.get("headline_text") or "").strip(),
        "badge_text": str(payload.get("badge_text") or "").strip(),
    }


def _build_enhanced_thumbnail_prompt(prompt_text: str, *, style_hint: str) -> str:
    style_text = str(style_hint or "").strip()
    details = [
        prompt_text.strip(),
        "single dominant hero subject",
        "modern YouTube thumbnail look",
        "high contrast cinematic lighting",
        "clean negative space for headline",
        "commercial polish",
        "16:9 composition",
        "no text",
    ]
    if style_text:
        details.insert(1, style_text)
    return ", ".join(item for item in details if item)


def _variant_generation_prompt(prompt_text: str, *, style_hint: str, preset: str) -> str:
    preset_hint = THUMBNAIL_STUDIO_PRESET_SPECS.get(_normalize_preset(preset), {}).get("prompt_hint", "")
    details = [prompt_text.strip(), preset_hint, str(style_hint or "").strip()]
    merged = ", ".join(item for item in details if item)
    merged = re.sub(r"\s+", " ", merged).strip(" ,")
    return merged


def _generate_comfyui_image(
    *,
    comfyui_url: str,
    prompt_text: str,
    negative_prompt: str,
    seed: int,
    output_path: Path,
    filename_prefix: str,
) -> None:
    checkpoint_name = _comfyui_checkpoint_name(comfyui_url)
    workflow = _comfyui_workflow(
        prompt_text=prompt_text,
        negative_prompt=negative_prompt,
        seed=seed,
        checkpoint_name=checkpoint_name,
        width=THUMBNAIL_STUDIO_CANVAS_SIZE[0],
        height=THUMBNAIL_STUDIO_CANVAS_SIZE[1],
        filename_prefix=filename_prefix,
    )
    response = requests.post(
        f"{comfyui_url}/prompt",
        json={"prompt": workflow},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    prompt_id = str(payload.get("prompt_id") or "").strip()
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return a prompt_id.")
    history = _wait_for_comfyui_history(comfyui_url, prompt_id)
    image_meta = _first_comfyui_image_meta(history)
    if image_meta is None:
        raise RuntimeError("ComfyUI completed, but no output image metadata was found.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_response = requests.get(
        f"{comfyui_url}/view",
        params={
            "filename": str(image_meta.get("filename") or ""),
            "subfolder": str(image_meta.get("subfolder") or ""),
            "type": str(image_meta.get("type") or "output"),
        },
        timeout=60,
    )
    image_response.raise_for_status()
    output_path.write_bytes(image_response.content)


def _wait_for_comfyui_history(comfyui_url: str, prompt_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 300.0
    last_error = "unknown"
    while time.monotonic() < deadline:
        response = requests.get(f"{comfyui_url}/history/{prompt_id}", timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            time.sleep(1.0)
            continue
        history = payload.get(prompt_id)
        if isinstance(history, dict):
            status = history.get("status")
            if isinstance(status, dict):
                status_text = str(status.get("status_str") or "").strip().lower()
                if status_text in {"error", "failed"}:
                    messages = status.get("messages")
                    if isinstance(messages, list) and messages:
                        last_error = str(messages[-1])
                    raise RuntimeError(f"ComfyUI prompt failed: {last_error}")
            outputs = history.get("outputs")
            if isinstance(outputs, dict) and outputs:
                return history
        time.sleep(1.0)
    raise RuntimeError("Timed out waiting for ComfyUI image generation.")


def _first_comfyui_image_meta(history: dict[str, Any]) -> dict[str, Any] | None:
    outputs = history.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        images = node_output.get("images")
        if not isinstance(images, list):
            continue
        for image_meta in images:
            if isinstance(image_meta, dict) and image_meta.get("filename"):
                return image_meta
    return None


def _comfyui_workflow(
    *,
    prompt_text: str,
    negative_prompt: str,
    seed: int,
    checkpoint_name: str,
    width: int,
    height: int,
    filename_prefix: str,
) -> dict[str, Any]:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(seed),
                "steps": 30,
                "cfg": 7.5,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": checkpoint_name,
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": int(width),
                "height": int(height),
                "batch_size": 1,
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt_text,
                "clip": ["4", 1],
            },
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt,
                "clip": ["4", 1],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2],
            },
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": filename_prefix,
                "images": ["8", 0],
            },
        },
    }


def _comfyui_checkpoint_name(comfyui_url: str) -> str:
    env_value = str(os.environ.get("IMAGINE_THUMBNAIL_COMFYUI_CHECKPOINT") or "").strip()
    if env_value:
        return env_value

    candidates = [
        f"{comfyui_url}/object_info/CheckpointLoaderSimple",
        f"{comfyui_url}/object_info",
    ]
    for url in candidates:
        response = requests.get(url, timeout=15)
        if int(response.status_code) >= 400:
            continue
        payload = response.json()
        values = _extract_checkpoint_names(payload)
        if values:
            return values[0]
    raise RuntimeError(
        "Could not resolve a ComfyUI checkpoint. Set IMAGINE_THUMBNAIL_COMFYUI_CHECKPOINT or install at least one checkpoint."
    )


def _extract_checkpoint_names(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    node_payload = payload.get("CheckpointLoaderSimple")
    if not isinstance(node_payload, dict):
        return []
    input_payload = node_payload.get("input")
    if not isinstance(input_payload, dict):
        return []
    required = input_payload.get("required")
    if not isinstance(required, dict):
        return []
    raw_values = required.get("ckpt_name")
    values: list[str] = []
    if isinstance(raw_values, list) and raw_values:
        first = raw_values[0]
        if isinstance(first, list):
            values.extend(str(item).strip() for item in first if str(item).strip())
        else:
            values.extend(str(item).strip() for item in raw_values if str(item).strip())
    return values


def _ensure_comfyui_available(comfyui_url: str) -> None:
    reachable, reason = comfyui_endpoint_reachable(comfyui_url)
    if not reachable:
        raise RuntimeError(
            f"ComfyUI is not reachable at {comfyui_url}. Start ComfyUI with API access enabled. Detail: {reason}"
        )


def _compose_thumbnail_image(
    *,
    source_path: Path,
    output_path: Path,
    headline_text: str,
    badge_text: str,
    preset: str,
    palette: str,
) -> None:
    normalized_preset = _normalize_preset(preset)
    preset_spec = THUMBNAIL_STUDIO_PRESET_SPECS[normalized_preset]
    palette_spec = THUMBNAIL_STUDIO_PALETTES[_normalize_palette(palette)]
    width, height = THUMBNAIL_STUDIO_CANVAS_SIZE

    base_image = Image.open(source_path).convert("RGB")
    canvas = _cover_image(base_image, THUMBNAIL_STUDIO_CANVAS_SIZE).convert("RGBA")
    _apply_base_grade(canvas)
    _apply_side_shade(
        canvas,
        side=str(preset_spec.get("shade_side") or "left"),
        accent=palette_spec.get("accent", "#FFFFFF"),
    )

    draw = ImageDraw.Draw(canvas)
    text_box = _text_box_pixels(preset_spec["text_box"], THUMBNAIL_STUDIO_CANVAS_SIZE)
    headline = _sanitize_headline_text(headline_text) or "WHY NOW"
    align = str(preset_spec.get("text_align") or "left")
    font, wrapped_text, line_spacing, bbox = _fit_headline_text(
        draw=draw,
        headline_text=headline,
        box=text_box,
        font_scale=float(preset_spec.get("font_scale") or 1.0),
        align=align,
    )
    _apply_text_plate(
        canvas,
        bbox=bbox,
        strength=float(preset_spec.get("plate_strength") or 0.7),
    )
    draw = ImageDraw.Draw(canvas)
    stroke_fill = _rgb(palette_spec["outline"])
    fill = _rgb(palette_spec["font"])
    draw.multiline_text(
        (bbox[0], bbox[1]),
        wrapped_text,
        font=font,
        fill=fill,
        align=align,
        spacing=line_spacing,
        stroke_width=max(4, int(font.size * 0.08)),
        stroke_fill=stroke_fill,
    )
    if bool(preset_spec.get("badge_enabled")) and str(badge_text or "").strip():
        _draw_badge(
            canvas,
            badge_text=str(badge_text or "").strip(),
            position=str(preset_spec.get("badge_position") or "top-right"),
            fill_color=palette_spec["badge_fill"],
            text_color=palette_spec["badge_text"],
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)


def _apply_base_grade(canvas: Image.Image) -> None:
    graded = ImageEnhance.Contrast(canvas).enhance(1.07)
    graded = ImageEnhance.Color(graded).enhance(1.08)
    graded = ImageEnhance.Brightness(graded).enhance(0.97)
    canvas.paste(graded)


def _apply_side_shade(canvas: Image.Image, *, side: str, accent: str) -> None:
    width, height = canvas.size
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    accent_rgb = _rgb(accent)
    draw = ImageDraw.Draw(overlay)
    if side == "left":
        for column in range(width):
            ratio = 1.0 - min(1.0, column / max(1, width * 0.65))
            alpha = int(170 * max(0.0, ratio))
            draw.line([(column, 0), (column, height)], fill=(0, 0, 0, alpha))
    elif side == "right":
        for column in range(width):
            ratio = min(1.0, column / max(1, width * 0.35))
            alpha = int(170 * max(0.0, ratio))
            draw.line([(column, 0), (column, height)], fill=(0, 0, 0, alpha))
    elif side == "top":
        for row in range(height):
            ratio = 1.0 - min(1.0, row / max(1, height * 0.35))
            alpha = int(180 * max(0.0, ratio))
            draw.line([(0, row), (width, row)], fill=(0, 0, 0, alpha))
    else:
        for row in range(height):
            ratio = min(1.0, row / max(1, height * 0.40))
            alpha = int(180 * max(0.0, ratio))
            draw.line([(0, height - row), (width, height - row)], fill=(0, 0, 0, alpha))
    glow = Image.new("RGBA", canvas.size, (*accent_rgb, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (
            int(width * 0.58),
            int(height * 0.10),
            int(width * 0.96),
            int(height * 0.72),
        ),
        fill=(*accent_rgb, 28),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=42))
    canvas.alpha_composite(overlay)
    canvas.alpha_composite(glow)


def _apply_text_plate(canvas: Image.Image, *, bbox: tuple[int, int, int, int], strength: float) -> None:
    if strength <= 0.0:
        return
    expanded = (
        max(0, bbox[0] - 30),
        max(0, bbox[1] - 20),
        min(canvas.size[0], bbox[2] + 30),
        min(canvas.size[1], bbox[3] + 20),
    )
    crop = canvas.crop(expanded)
    blurred = crop.filter(ImageFilter.GaussianBlur(radius=max(12, int(18 * strength))))
    shadow = Image.new("RGBA", crop.size, (0, 0, 0, int(150 * strength)))
    blurred.alpha_composite(shadow)
    mask = Image.new("L", crop.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, crop.size[0] - 1, crop.size[1] - 1), radius=28, fill=255)
    canvas.paste(blurred, expanded, mask)


def _draw_badge(
    canvas: Image.Image,
    *,
    badge_text: str,
    position: str,
    fill_color: str,
    text_color: str,
) -> None:
    text_value = str(badge_text or "").strip()
    if not text_value:
        return
    width, height = canvas.size
    font = _load_font(54)
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text_value, font=font)
    text_width = max(1, int(bbox[2] - bbox[0]))
    text_height = max(1, int(bbox[3] - bbox[1]))
    pad_x = 28
    pad_y = 16
    badge_width = text_width + pad_x * 2
    badge_height = text_height + pad_y * 2
    margin = 38
    if position == "top-left":
        left = margin
        top = margin
    elif position == "bottom-right":
        left = width - badge_width - margin
        top = height - badge_height - margin
    else:
        left = width - badge_width - margin
        top = margin
    badge_box = (left, top, left + badge_width, top + badge_height)
    draw.rounded_rectangle(badge_box, radius=26, fill=_rgb(fill_color))
    text_x = left + pad_x
    text_y = top + pad_y - bbox[1]
    draw.text((text_x, text_y), text_value, font=font, fill=_rgb(text_color))


def _fit_headline_text(
    *,
    draw: ImageDraw.ImageDraw,
    headline_text: str,
    box: tuple[int, int, int, int],
    font_scale: float,
    align: str,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str, int, tuple[int, int, int, int]]:
    width = max(120, int(box[2]))
    height = max(80, int(box[3]))
    cleaned = _sanitize_headline_text(headline_text)
    words = cleaned.split()
    wrap_width = max(8, min(18, len(words) + 2))
    wrapped = textwrap.fill(cleaned, width=wrap_width)
    font_size = int(min(width * 0.16, height * 0.42) * max(0.8, float(font_scale)))
    font_size = max(44, min(132, font_size))
    best_font = _load_font(font_size)
    best_text = wrapped
    best_spacing = max(8, int(font_size * 0.14))
    best_bbox = (box[0], box[1], box[0] + width, box[1] + height)

    for candidate_size in range(font_size, 35, -4):
        font = _load_font(candidate_size)
        spacing = max(8, int(candidate_size * 0.14))
        for candidate_wrap in range(max(6, wrap_width - 4), min(20, wrap_width + 5)):
            candidate_text = textwrap.fill(cleaned, width=candidate_wrap)
            measured = draw.multiline_textbbox(
                (0, 0),
                candidate_text,
                font=font,
                align=align,
                spacing=spacing,
                stroke_width=max(4, int(candidate_size * 0.08)),
            )
            measured_width = int(measured[2] - measured[0])
            measured_height = int(measured[3] - measured[1])
            if measured_width <= width and measured_height <= height:
                x = box[0]
                if align == "center":
                    x = box[0] + (width - measured_width) // 2
                elif align == "right":
                    x = box[0] + width - measured_width
                y = box[1] + max(0, (height - measured_height) // 2)
                return font, candidate_text, spacing, (x, y, x + measured_width, y + measured_height)
            best_font = font
            best_text = candidate_text
            best_spacing = spacing
            x = box[0]
            if align == "center":
                x = box[0] + max(0, (width - measured_width) // 2)
            elif align == "right":
                x = box[0] + max(0, width - measured_width)
            y = box[1] + max(0, (height - measured_height) // 2)
            best_bbox = (x, y, x + measured_width, y + measured_height)
    return best_font, best_text, best_spacing, best_bbox


def _cover_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    src_width, src_height = image.size
    if src_width <= 0 or src_height <= 0:
        raise RuntimeError("Thumbnail source image has invalid dimensions.")
    scale = max(width / float(src_width), height / float(src_height))
    resized = image.resize((max(1, int(src_width * scale)), max(1, int(src_height * scale))), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _text_box_pixels(box: tuple[float, float, float, float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (
        int(width * float(box[0])),
        int(height * float(box[1])),
        int(width * float(box[2])),
        int(height * float(box[3])),
    )


def _variant_score_notes(*, headline_text: str, badge_text: str, preset: str) -> list[str]:
    notes = [f"Preset: {_preset_label(preset)}"]
    word_count = len([part for part in str(headline_text or "").split() if part.strip()])
    if word_count <= 4:
        notes.append("Short hook")
    else:
        notes.append("Hook may be too long")
    if badge_text:
        notes.append("Has badge")
    else:
        notes.append("No badge")
    return notes


def _default_headline_text(prompt_text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zÀ-ÿ\s-]", " ", str(prompt_text or "")).strip()
    words = [word for word in re.split(r"\s+", cleaned) if word]
    filtered = [word for word in words if word.lower() not in _STOP_WORDS]
    selected = filtered[:4] or words[:4] or ["Why", "This", "Now"]
    return " ".join(selected[:4]).title()


def _sanitize_headline_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if not cleaned:
        return ""
    return cleaned[:72]


def _thumbnail_session_id(prompt_text: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _slugify(prompt_text)[:48] or "thumbnail"
    return f"{slug}-{stamp}"


def _slugify(value: str) -> str:
    candidate = str(value or "").strip().lower()
    candidate = re.sub(r"[^a-z0-9]+", "-", candidate)
    candidate = re.sub(r"-{2,}", "-", candidate).strip("-")
    return candidate or "thumbnail"


def _stable_seed(text: str, *, salt: str) -> int:
    digest = hashlib.sha1(f"{salt}:{text}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _normalize_preset(value: Any) -> str:
    candidate = str(value or THUMBNAIL_STUDIO_DEFAULT_PRESET).strip().lower()
    if candidate not in THUMBNAIL_STUDIO_PRESET_CHOICES:
        return THUMBNAIL_STUDIO_DEFAULT_PRESET
    return candidate


def _normalize_palette(value: Any) -> str:
    candidate = str(value or THUMBNAIL_STUDIO_DEFAULT_PALETTE).strip().lower()
    if candidate not in THUMBNAIL_STUDIO_PALETTE_CHOICES:
        return THUMBNAIL_STUDIO_DEFAULT_PALETTE
    return candidate


def _normalize_comfyui_url(value: Any) -> str:
    candidate = str(value or THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL).strip()
    return candidate.rstrip("/") or THUMBNAIL_STUDIO_DEFAULT_COMFYUI_URL


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _preset_label(value: str) -> str:
    return THUMBNAIL_STUDIO_PRESET_SPECS.get(_normalize_preset(value), {}).get("label", value)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in THUMBNAIL_STUDIO_FONTFILE_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(ImageColor.getrgb(value))
