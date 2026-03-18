from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import http.server
import json
import mimetypes
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

YOUTUBE_VISIBILITY_CHOICES = ("private", "unlisted", "public")
YOUTUBE_CATEGORY_DEFAULT = "Education"
YOUTUBE_DRAFT_SCHEMA_VERSION = 2
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_DEFAULT_SCOPES = (YOUTUBE_UPLOAD_SCOPE,)
YOUTUBE_OAUTH_TIMEOUT_SECONDS = 300
YOUTUBE_RESUMABLE_CHUNK_BYTES = 8 * 1024 * 1024
YOUTUBE_THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
YOUTUBE_DEFAULT_THUMBNAIL_PATH = Path("output") / "thumbnail_yt.jpg"
YOUTUBE_THUMBNAIL_FONT_COLOR_CHOICES = (
    "white",
    "sunflower",
    "mint",
    "coral",
    "sky",
    "tangerine",
    "black",
)
YOUTUBE_THUMBNAIL_OUTLINE_COLOR_CHOICES = (
    "charcoal",
    "black",
    "midnight",
    "navy",
    "crimson",
)
YOUTUBE_THUMBNAIL_FONT_SIZE_CHOICES = ("small", "medium", "large", "extra-large")
YOUTUBE_THUMBNAIL_ANCHOR_CHOICES = (
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
YOUTUBE_THUMBNAIL_TEXT_ALIGN_CHOICES = ("left", "center", "right")
YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR = "white"
YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR = "charcoal"
YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE = "medium"
YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR = "bottom-left"
YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN = "left"
YOUTUBE_THUMBNAIL_MAX_OFFSET = 240
YOUTUBE_THUMBNAIL_VARIANT_FRACTIONS = (0.16, 0.32, 0.52, 0.68, 0.82)
YOUTUBE_ALLOWED_THUMBNAIL_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
    }
)
YOUTUBE_THUMBNAIL_SCALE_STEPS = (
    (1280, 720),
    (1024, 576),
    (854, 480),
    (640, 360),
)
YOUTUBE_THUMBNAIL_JPEG_QUALITIES = (4, 6, 8, 10, 12, 16, 20, 24, 28)
YOUTUBE_CATEGORY_IDS = {
    "Education": "27",
}
YOUTUBE_THUMBNAIL_COLOR_VALUES = {
    "white": "0xFFFFFF",
    "sunflower": "0xFFD54A",
    "mint": "0xB8F2C8",
    "coral": "0xFF7663",
    "sky": "0x7FD7FF",
    "tangerine": "0xFF9A3C",
    "black": "0x090909",
    "charcoal": "0x111111",
    "midnight": "0x152238",
    "navy": "0x16324F",
    "crimson": "0x7A1428",
}
YOUTUBE_THUMBNAIL_FONT_SIZE_MULTIPLIERS = {
    "small": 0.82,
    "medium": 1.0,
    "large": 1.24,
    "extra-large": 1.48,
}

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
}


@dataclass
class YouTubeAuthState:
    linked: bool
    client_secrets_path: str | None
    token_path: str | None
    reason: str


@dataclass
class YouTubePublishDraft:
    schema_version: int = YOUTUBE_DRAFT_SCHEMA_VERSION
    platform: str = "youtube"
    project_dir: str = ""
    video_path: str = ""
    captions_path: str | None = None
    title: str = ""
    title_suggestions: list[str] = field(default_factory=list)
    description: str = ""
    visibility: str = "private"
    schedule_at: str | None = None
    tags: list[str] = field(default_factory=list)
    category: str = YOUTUBE_CATEGORY_DEFAULT
    upload_captions: bool = False
    thumbnail_path: str | None = None
    thumbnail_source: str = "none"
    thumbnail_prompt: str = ""
    thumbnail_text: str = ""
    thumbnail_font_color: str = YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR
    thumbnail_outline_color: str = YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR
    thumbnail_font_size_mode: str = YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE
    thumbnail_anchor: str = YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR
    thumbnail_offset_x: int = 0
    thumbnail_offset_y: int = 0
    thumbnail_text_align: str = YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN
    thumbnail_background_variant: int = 0
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "YouTubePublishDraft":
        return cls(
            schema_version=int(payload.get("schema_version") or YOUTUBE_DRAFT_SCHEMA_VERSION),
            platform=str(payload.get("platform") or "youtube").strip() or "youtube",
            project_dir=str(payload.get("project_dir") or "").strip(),
            video_path=str(payload.get("video_path") or "").strip(),
            captions_path=_optional_str(payload.get("captions_path")),
            title=str(payload.get("title") or "").strip(),
            title_suggestions=_string_list(payload.get("title_suggestions")),
            description=str(payload.get("description") or "").strip(),
            visibility=_normalize_visibility(payload.get("visibility")),
            schedule_at=_optional_str(payload.get("schedule_at")),
            tags=_string_list(payload.get("tags")),
            category=str(payload.get("category") or YOUTUBE_CATEGORY_DEFAULT).strip() or YOUTUBE_CATEGORY_DEFAULT,
            upload_captions=bool(payload.get("upload_captions")),
            thumbnail_path=_optional_str(payload.get("thumbnail_path")),
            thumbnail_source=str(payload.get("thumbnail_source") or "none").strip() or "none",
            thumbnail_prompt=str(payload.get("thumbnail_prompt") or "").strip(),
            thumbnail_text=str(payload.get("thumbnail_text") or payload.get("thumbnail_prompt") or "").strip(),
            thumbnail_font_color=_normalize_thumbnail_color(
                payload.get("thumbnail_font_color"),
                YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR,
                choices=YOUTUBE_THUMBNAIL_FONT_COLOR_CHOICES,
            ),
            thumbnail_outline_color=_normalize_thumbnail_color(
                payload.get("thumbnail_outline_color"),
                YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR,
                choices=YOUTUBE_THUMBNAIL_OUTLINE_COLOR_CHOICES,
            ),
            thumbnail_font_size_mode=_normalize_thumbnail_font_size_mode(payload.get("thumbnail_font_size_mode")),
            thumbnail_anchor=_normalize_thumbnail_anchor(payload.get("thumbnail_anchor")),
            thumbnail_offset_x=_normalize_thumbnail_offset(payload.get("thumbnail_offset_x")),
            thumbnail_offset_y=_normalize_thumbnail_offset(payload.get("thumbnail_offset_y")),
            thumbnail_text_align=_normalize_thumbnail_text_align(payload.get("thumbnail_text_align")),
            thumbnail_background_variant=max(0, _coerce_non_negative_int(payload.get("thumbnail_background_variant"))),
            updated_at=str(payload.get("updated_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["visibility"] = _normalize_visibility(self.visibility)
        payload["title_suggestions"] = _dedupe_strings(self.title_suggestions)
        payload["tags"] = _dedupe_strings(self.tags)
        payload["thumbnail_font_color"] = _normalize_thumbnail_color(
            self.thumbnail_font_color,
            YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR,
            choices=YOUTUBE_THUMBNAIL_FONT_COLOR_CHOICES,
        )
        payload["thumbnail_outline_color"] = _normalize_thumbnail_color(
            self.thumbnail_outline_color,
            YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR,
            choices=YOUTUBE_THUMBNAIL_OUTLINE_COLOR_CHOICES,
        )
        payload["thumbnail_font_size_mode"] = _normalize_thumbnail_font_size_mode(self.thumbnail_font_size_mode)
        payload["thumbnail_anchor"] = _normalize_thumbnail_anchor(self.thumbnail_anchor)
        payload["thumbnail_offset_x"] = _normalize_thumbnail_offset(self.thumbnail_offset_x)
        payload["thumbnail_offset_y"] = _normalize_thumbnail_offset(self.thumbnail_offset_y)
        payload["thumbnail_text_align"] = _normalize_thumbnail_text_align(self.thumbnail_text_align)
        payload["thumbnail_background_variant"] = max(0, _coerce_non_negative_int(self.thumbnail_background_variant))
        payload["thumbnail_text"] = str(self.thumbnail_text or self.thumbnail_prompt or "").strip()
        return payload


@dataclass
class PreparedYouTubeThumbnail:
    path: Path
    mime_type: str
    source_path: Path
    source_size_bytes: int
    upload_size_bytes: int
    optimized: bool
    optimization_reason: str | None = None
    cleanup_path: Path | None = None


def youtube_publish_dir(project_dir: Path) -> Path:
    return project_dir.expanduser().resolve() / "publish"


def youtube_draft_path(project_dir: Path) -> Path:
    return youtube_publish_dir(project_dir) / "youtube_draft.json"


def youtube_report_path(project_dir: Path) -> Path:
    return youtube_publish_dir(project_dir) / "youtube_report.json"


def youtube_auth_client_secrets_path() -> Path:
    return (Path.home() / ".imagine" / "youtube" / "client_secrets.json").resolve()


def youtube_auth_token_path() -> Path:
    return (Path.home() / ".imagine" / "youtube" / "token.json").resolve()


def detect_youtube_auth_state() -> YouTubeAuthState:
    client_path = youtube_auth_client_secrets_path()
    token_path = youtube_auth_token_path()

    has_client = client_path.exists()
    has_token = token_path.exists()
    linked = has_client and has_token

    if linked:
        reason = "OAuth client secrets and token files are present."
    elif has_client:
        reason = "OAuth client secrets are configured, but there is no saved token yet."
    elif has_token:
        reason = "A saved YouTube token exists, but the OAuth client secrets file is missing."
    else:
        reason = "No YouTube OAuth client secrets or token files were found yet."

    return YouTubeAuthState(
        linked=linked,
        client_secrets_path=str(client_path) if has_client else None,
        token_path=str(token_path) if has_token else None,
        reason=reason,
    )


def load_youtube_token() -> dict[str, Any] | None:
    token_path = youtube_auth_token_path()
    if not token_path.exists():
        return None
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid YouTube token payload at {token_path}")
    return payload


def save_youtube_token(payload: dict[str, Any]) -> Path:
    token_path = youtube_auth_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    token_path.write_text(serialized, encoding="utf-8")
    return token_path


def delete_youtube_token() -> bool:
    token_path = youtube_auth_token_path()
    if not token_path.exists():
        return False
    token_path.unlink()
    return True


def youtube_token_is_expired(payload: dict[str, Any], *, skew_seconds: int = 60) -> bool:
    expires_in = payload.get("expires_in")
    obtained_at = payload.get("obtained_at")
    if expires_in is None or obtained_at is None:
        return False

    try:
        expires_seconds = float(expires_in)
    except Exception:
        return False

    try:
        obtained_dt = dt.datetime.fromisoformat(str(obtained_at))
    except Exception:
        return False

    expires_at = obtained_dt.timestamp() + expires_seconds
    return (time.time() + max(0, skew_seconds)) >= expires_at


def youtube_token_has_scope(payload: dict[str, Any] | None, scope: str) -> bool:
    if not isinstance(payload, dict):
        return False
    scope_values = {
        item.strip()
        for item in str(payload.get("scope") or "").split()
        if item.strip()
    }
    return scope in scope_values


def refresh_youtube_token(*, notify: Callable[[str], None] | None = None) -> dict[str, Any]:
    token_payload = load_youtube_token()
    if token_payload is None:
        raise RuntimeError("No saved YouTube token exists yet.")

    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not refresh_token:
        raise RuntimeError("Saved YouTube token has no refresh token. Run the OAuth link flow again.")

    client_payload = _load_youtube_client_secrets_payload()
    token_request_payload = {
        "client_id": str(client_payload.get("client_id") or "").strip(),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    client_secret = str(client_payload.get("client_secret") or "").strip()
    if client_secret:
        token_request_payload["client_secret"] = client_secret

    if notify is not None:
        notify("Refreshing saved YouTube token.")

    refreshed = _post_form_json(
        str(client_payload.get("token_uri") or "https://oauth2.googleapis.com/token"),
        token_request_payload,
    )
    if "access_token" not in refreshed:
        raise RuntimeError("Google token refresh response did not include an access token.")

    merged = dict(token_payload)
    merged.update(refreshed)
    merged["refresh_token"] = refresh_token
    merged["scope"] = refreshed.get("scope") or token_payload.get("scope") or " ".join(YOUTUBE_DEFAULT_SCOPES)
    merged["obtained_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_youtube_token(merged)
    return merged


def ensure_youtube_token(
    *,
    scopes: tuple[str, ...] = YOUTUBE_DEFAULT_SCOPES,
    force_relink: bool = False,
    timeout_seconds: int = YOUTUBE_OAUTH_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    notify: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    existing = None if force_relink else load_youtube_token()
    if existing is not None:
        if not youtube_token_is_expired(existing):
            return existing
        if str(existing.get("refresh_token") or "").strip():
            try:
                return refresh_youtube_token(notify=notify)
            except Exception as exc:  # noqa: BLE001
                if notify is not None:
                    notify(f"WARN: Saved YouTube token refresh failed; starting browser auth again: {exc}")

    return run_local_youtube_oauth_flow(
        scopes=scopes,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
        notify=notify,
    )


def disconnect_youtube_auth(
    *,
    revoke_remote: bool = True,
    notify: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    token_payload = load_youtube_token()
    token_path = youtube_auth_token_path()
    revoked_remote = False
    warning: str | None = None

    if revoke_remote and isinstance(token_payload, dict):
        token_value = str(token_payload.get("refresh_token") or token_payload.get("access_token") or "").strip()
        if token_value:
            try:
                _revoke_google_oauth_token(token_value)
                revoked_remote = True
                if notify is not None:
                    notify("Revoked the saved YouTube OAuth token with Google.")
            except Exception as exc:  # noqa: BLE001
                warning = f"Google token revoke request failed, but the local token was still removed: {exc}"
                if notify is not None:
                    notify(f"WARN: {warning}")

    removed_local_token = delete_youtube_token()
    if removed_local_token and notify is not None:
        notify(f"Removed saved YouTube token: {token_path}")

    if not removed_local_token and warning is None:
        warning = "No saved YouTube token was present to disconnect."

    linked_after = detect_youtube_auth_state().linked
    return {
        "linked": linked_after,
        "revoked_remote_token": revoked_remote,
        "removed_local_token": removed_local_token,
        "client_secrets_path": str(youtube_auth_client_secrets_path()),
        "token_path": str(token_path),
        "warning": warning,
    }


def run_local_youtube_oauth_flow(
    *,
    scopes: tuple[str, ...] = YOUTUBE_DEFAULT_SCOPES,
    timeout_seconds: int = YOUTUBE_OAUTH_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    notify: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    client_payload = _load_youtube_client_secrets_payload()
    client_id = str(client_payload.get("client_id") or "").strip()
    if not client_id:
        raise RuntimeError("YouTube OAuth client secrets file is missing `client_id`.")

    token_uri = str(client_payload.get("token_uri") or "https://oauth2.googleapis.com/token").strip()
    auth_uri = str(client_payload.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth").strip()
    client_secret = str(client_payload.get("client_secret") or "").strip()
    redirect_host = _preferred_loopback_host(client_payload.get("redirect_uris"))

    callback_queue: queue.Queue[dict[str, str]] = queue.Queue()
    server = _oauth_callback_server(redirect_host, callback_queue)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    redirect_uri = f"http://{redirect_host}:{server.server_port}/"
    state = secrets.token_urlsafe(24)
    code_verifier = _generate_code_verifier()
    code_challenge = _code_challenge(code_verifier)
    scope_text = " ".join(_dedupe_strings(scopes))

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope_text,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent select_account",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{auth_uri}?{urllib.parse.urlencode(auth_params)}"

    try:
        if notify is not None:
            notify("Opening browser for YouTube OAuth sign-in.")
            notify(f"If the browser does not open automatically, visit: {auth_url}")
        _open_auth_url(auth_url)

        deadline = time.monotonic() + max(30, int(timeout_seconds))
        callback_params: dict[str, str] | None = None
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("YouTube OAuth was cancelled before completion.")
            try:
                callback_params = callback_queue.get(timeout=0.25)
                break
            except queue.Empty:
                continue

        if callback_params is None:
            raise RuntimeError("Timed out waiting for Google OAuth to return to the local callback server.")

        if callback_params.get("state") != state:
            raise RuntimeError("OAuth state check failed. Refusing to continue with the returned authorization code.")

        if callback_params.get("error"):
            error_value = callback_params.get("error") or "unknown_error"
            error_description = callback_params.get("error_description") or "Google returned an OAuth error."
            raise RuntimeError(f"Google OAuth error: {error_value} ({error_description})")

        code = str(callback_params.get("code") or "").strip()
        if not code:
            raise RuntimeError("Google OAuth callback returned without an authorization code.")

        token_request_payload = {
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if client_secret:
            token_request_payload["client_secret"] = client_secret

        token_payload = _post_form_json(token_uri, token_request_payload)
        if "access_token" not in token_payload:
            raise RuntimeError("Google OAuth token response did not include an access token.")

        token_payload["scope"] = token_payload.get("scope") or scope_text
        token_payload["obtained_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save_youtube_token(token_payload)
        return token_payload
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1.0)


def load_youtube_publish_draft(project_dir: Path) -> YouTubePublishDraft | None:
    draft_path = youtube_draft_path(project_dir)
    if not draft_path.exists():
        return None

    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid YouTube draft payload at {draft_path}")
    return YouTubePublishDraft.from_dict(payload)


def save_youtube_publish_draft(draft: YouTubePublishDraft) -> Path:
    project_dir = Path(draft.project_dir).expanduser().resolve()
    draft_path = youtube_draft_path(project_dir)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    draft_path.write_text(
        json.dumps(draft.to_dict(), indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return draft_path


def ensure_project_youtube_thumbnail(
    project_dir: Path,
    *,
    prompt_text: str = "",
    overwrite: bool = False,
    notify: Callable[[str], None] | None = None,
) -> Path | None:
    resolved_project_dir = project_dir.expanduser().resolve()
    existing_thumbnail = _detect_thumbnail_path(resolved_project_dir)
    if existing_thumbnail is not None and not overwrite:
        return existing_thumbnail

    final_mp4 = resolved_project_dir / "output" / "final.mp4"
    if not final_mp4.exists():
        return existing_thumbnail

    return render_project_youtube_thumbnail(
        resolved_project_dir,
        thumbnail_text=_default_thumbnail_prompt_text(resolved_project_dir, fallback_prompt=prompt_text),
        font_color=YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR,
        outline_color=YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR,
        font_size_mode=YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE,
        anchor=YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR,
        offset_x=0,
        offset_y=0,
        text_align=YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN,
        background_variant=0,
        notify=notify,
    ) or existing_thumbnail


def render_youtube_thumbnail_for_draft(
    draft: YouTubePublishDraft,
    *,
    notify: Callable[[str], None] | None = None,
) -> Path | None:
    project_dir = Path(draft.project_dir).expanduser().resolve()
    thumbnail_text = str(draft.thumbnail_text or draft.thumbnail_prompt or draft.title or "").strip()
    rendered_path = render_project_youtube_thumbnail(
        project_dir,
        thumbnail_text=thumbnail_text,
        font_color=draft.thumbnail_font_color,
        outline_color=draft.thumbnail_outline_color,
        font_size_mode=draft.thumbnail_font_size_mode,
        anchor=draft.thumbnail_anchor,
        offset_x=draft.thumbnail_offset_x,
        offset_y=draft.thumbnail_offset_y,
        text_align=draft.thumbnail_text_align,
        background_variant=draft.thumbnail_background_variant,
        notify=notify,
    )
    if rendered_path is None:
        return None
    draft.thumbnail_path = str(rendered_path)
    draft.thumbnail_source = "generated"
    draft.thumbnail_prompt = thumbnail_text
    draft.thumbnail_text = thumbnail_text
    return rendered_path


def render_project_youtube_thumbnail(
    project_dir: Path,
    *,
    thumbnail_text: str,
    font_color: str,
    outline_color: str,
    font_size_mode: str,
    anchor: str,
    offset_x: int,
    offset_y: int,
    text_align: str,
    background_variant: int,
    notify: Callable[[str], None] | None = None,
    output_path: Path | None = None,
) -> Path | None:
    resolved_project_dir = project_dir.expanduser().resolve()
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        if notify is not None:
            notify("WARN: ffmpeg not found; skipping YouTube thumbnail rendering.")
        return None

    resolved_output_path = (output_path or (resolved_project_dir / YOUTUBE_DEFAULT_THUMBNAIL_PATH)).resolve()
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)

    width = 1280
    height = 720
    wrapped_text = _wrap_youtube_thumbnail_text(thumbnail_text)
    title_lines = [line for line in wrapped_text.splitlines() if line.strip()] or ["WHY THIS MATTERS NOW"]
    base_font = _youtube_thumbnail_title_font_size(width=width, height=height, title_lines=title_lines)
    normalized_font_size_mode = _normalize_thumbnail_font_size_mode(font_size_mode)
    font_multiplier = YOUTUBE_THUMBNAIL_FONT_SIZE_MULTIPLIERS.get(normalized_font_size_mode, 1.0)
    title_font = max(34, int(base_font * font_multiplier))
    line_spacing = max(10, int(title_font * 0.16))

    normalized_anchor = _normalize_thumbnail_anchor(anchor)
    normalized_align = _normalize_thumbnail_text_align(text_align)
    normalized_offset_x = _normalize_thumbnail_offset(offset_x)
    normalized_offset_y = _normalize_thumbnail_offset(offset_y)
    font_color_value = _thumbnail_color_value(font_color)
    outline_color_value = _thumbnail_color_value(outline_color)
    x_expr, y_expr = _thumbnail_drawtext_position(
        width=width,
        height=height,
        anchor=normalized_anchor,
        text_align=normalized_align,
        offset_x=normalized_offset_x,
        offset_y=normalized_offset_y,
    )

    cleanup_paths: list[Path] = []
    input_args, background_cleanup_paths = _thumbnail_render_input(
        resolved_project_dir,
        background_variant=max(0, int(background_variant)),
        working_dir=resolved_output_path.parent,
        notify=notify,
    )
    cleanup_paths.extend(background_cleanup_paths)

    try:
        with tempfile.NamedTemporaryFile(
            prefix="youtube-thumbnail-text-",
            suffix=".txt",
            dir=str(resolved_output_path.parent),
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as handle:
            handle.write(wrapped_text)
            text_path = Path(handle.name)
        cleanup_paths.append(text_path)

        textfile_value = _escape_drawtext_path(text_path)
        filter_parts = [
            (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                "eq=contrast=1.06:saturation=1.04:brightness=-0.04,"
                "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.10:t=fill,"
                f"drawtext=textfile='{textfile_value}':reload=1:fontcolor={font_color_value}:"
                f"fontsize={title_font}:line_spacing={line_spacing}:"
                f"x={x_expr}:y={y_expr}:borderw=7:bordercolor={outline_color_value}@0.96:"
                f"shadowcolor={outline_color_value}@0.96:shadowx=3:shadowy=3[v]"
            )
        ]
        command = [
            ffmpeg_bin,
            "-y",
            *input_args,
            "-frames:v",
            "1",
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
            str(resolved_output_path),
        ]
        run = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if int(run.returncode or 0) != 0:
            stderr_text = str(run.stderr or "").strip()
            if notify is not None:
                notify(f"WARN: YouTube thumbnail rendering failed: {stderr_text or 'unknown ffmpeg error'}")
            return None
    finally:
        for cleanup_path in cleanup_paths:
            cleanup_path.unlink(missing_ok=True)

    if resolved_output_path.exists():
        if notify is not None:
            notify(f"Prepared YouTube thumbnail: {resolved_output_path.name}")
        return resolved_output_path
    return None


def build_youtube_publish_draft(project_dir: Path, *, fallback_prompt: str = "") -> YouTubePublishDraft:
    resolved_project_dir = project_dir.expanduser().resolve()
    script_payload = _load_json_object(resolved_project_dir / "script.json")
    rights_payload = _load_json_object(resolved_project_dir / "rights_manifest.json")

    prompt_value = _load_prompt_text(resolved_project_dir, fallback_prompt=fallback_prompt)
    script_title = _optional_str(script_payload.get("title")) if script_payload else None
    summary = _optional_str(script_payload.get("summary")) if script_payload else None
    scene_headings = _scene_headings(script_payload)
    asset_keywords = _asset_keywords(rights_payload)
    burn_subtitles = _manifest_burn_subtitles(rights_payload)
    required_credits_block = _manifest_required_credits_block(rights_payload)

    title = _first_non_empty(
        script_title,
        prompt_value,
        _humanize_project_name(resolved_project_dir.name),
        "Untitled video",
    )
    title_suggestions = _title_suggestions(title=title, prompt_value=prompt_value, scene_headings=scene_headings)
    description = _description_text(
        title=title,
        summary=summary or "",
        scene_headings=scene_headings,
        required_credits_block=required_credits_block,
    )
    tags = _tag_suggestions(title=title, prompt_value=prompt_value, scene_headings=scene_headings, asset_keywords=asset_keywords)

    video_path = resolved_project_dir / "output" / "final.mp4"
    captions_path = resolved_project_dir / "output" / "final.srt"
    thumbnail_path = ensure_project_youtube_thumbnail(
        resolved_project_dir,
        prompt_text=title,
        overwrite=False,
    )

    return YouTubePublishDraft(
        project_dir=str(resolved_project_dir),
        video_path=str(video_path),
        captions_path=str(captions_path) if captions_path.exists() else None,
        title=title,
        title_suggestions=title_suggestions,
        description=description,
        visibility="private",
        schedule_at=None,
        tags=tags,
        category=YOUTUBE_CATEGORY_DEFAULT,
        upload_captions=not burn_subtitles,
        thumbnail_path=str(thumbnail_path) if thumbnail_path is not None else None,
        thumbnail_source="auto" if thumbnail_path is not None else "none",
        thumbnail_prompt=title,
        thumbnail_text=title,
        thumbnail_font_color=YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR,
        thumbnail_outline_color=YOUTUBE_THUMBNAIL_DEFAULT_OUTLINE_COLOR,
        thumbnail_font_size_mode=YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE,
        thumbnail_anchor=YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR,
        thumbnail_offset_x=0,
        thumbnail_offset_y=0,
        thumbnail_text_align=YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN,
        thumbnail_background_variant=0,
        updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


def ensure_youtube_publish_draft(project_dir: Path, *, fallback_prompt: str = "") -> YouTubePublishDraft:
    draft = load_youtube_publish_draft(project_dir)
    if draft is not None:
        thumbnail_path = Path(draft.thumbnail_path).expanduser().resolve() if draft.thumbnail_path else None
        if thumbnail_path is None or not thumbnail_path.exists():
            generated_thumbnail = ensure_project_youtube_thumbnail(
                project_dir,
                prompt_text=draft.thumbnail_prompt or draft.title or fallback_prompt,
                overwrite=False,
            )
            if generated_thumbnail is not None:
                draft.thumbnail_path = str(generated_thumbnail)
                if draft.thumbnail_source in {"", "none"}:
                    draft.thumbnail_source = "auto"
                save_youtube_publish_draft(draft)
        return draft

    draft = build_youtube_publish_draft(project_dir, fallback_prompt=fallback_prompt)
    save_youtube_publish_draft(draft)
    return draft


def save_youtube_publish_report(project_dir: Path, payload: dict[str, Any]) -> Path:
    report_path = youtube_report_path(project_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    report_path.write_text(serialized, encoding="utf-8")
    return report_path


def publish_youtube_draft(
    draft: YouTubePublishDraft,
    *,
    relink: bool = False,
    notify: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    chunk_bytes: int = YOUTUBE_RESUMABLE_CHUNK_BYTES,
) -> dict[str, Any]:
    project_dir = Path(draft.project_dir).expanduser().resolve()
    video_path = Path(draft.video_path).expanduser().resolve()
    if not video_path.exists():
        raise RuntimeError(f"Video file not found for YouTube publish: {video_path}")

    token_payload = ensure_youtube_token(force_relink=relink, cancel_event=cancel_event, notify=notify)
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("YouTube auth succeeded, but no access token was available.")

    category_id = YOUTUBE_CATEGORY_IDS.get(draft.category, "27")
    snippet: dict[str, Any] = {
        "title": draft.title.strip() or video_path.stem,
        "description": draft.description.strip(),
        "categoryId": category_id,
    }
    if draft.tags:
        snippet["tags"] = list(_dedupe_strings(draft.tags))

    status: dict[str, Any] = {
        "privacyStatus": _normalize_visibility(draft.visibility),
    }
    if draft.schedule_at:
        scheduled_at = _normalize_publish_at(draft.schedule_at)
        status["privacyStatus"] = "private"
        status["publishAt"] = scheduled_at

    if notify is not None:
        notify(f"Starting YouTube upload for {video_path.name}.")

    upload_url = _start_resumable_video_upload(
        access_token=access_token,
        video_path=video_path,
        snippet=snippet,
        status=status,
    )
    resource = _upload_resumable_video_bytes(
        access_token=access_token,
        upload_url=upload_url,
        video_path=video_path,
        chunk_bytes=chunk_bytes,
        notify=notify,
        progress=progress,
        cancel_event=cancel_event,
    )

    video_id = str(resource.get("id") or "").strip()
    video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    warnings: list[str] = []
    thumbnail_result: dict[str, Any] | None = None
    captions_result: dict[str, Any] | None = None

    thumbnail_upload_details: dict[str, Any] | None = None

    if video_id and draft.thumbnail_path:
        thumbnail_path = Path(draft.thumbnail_path).expanduser().resolve()
        if thumbnail_path.exists():
            prepared_thumbnail: PreparedYouTubeThumbnail | None = None
            try:
                prepared_thumbnail = prepare_youtube_thumbnail_for_upload(
                    project_dir=project_dir,
                    thumbnail_path=thumbnail_path,
                    notify=notify,
                )
                thumbnail_upload_details = {
                    "source_path": str(prepared_thumbnail.source_path),
                    "source_size_bytes": prepared_thumbnail.source_size_bytes,
                    "upload_size_bytes": prepared_thumbnail.upload_size_bytes,
                    "upload_mime_type": prepared_thumbnail.mime_type,
                    "optimized": prepared_thumbnail.optimized,
                    "optimization_reason": prepared_thumbnail.optimization_reason,
                }
                if notify is not None:
                    notify(f"Uploading thumbnail {prepared_thumbnail.path.name}.")
                thumbnail_result = set_youtube_thumbnail(
                    access_token=access_token,
                    video_id=video_id,
                    thumbnail_path=prepared_thumbnail.path,
                    mime_type=prepared_thumbnail.mime_type,
                )
            except Exception as exc:  # noqa: BLE001
                warning = f"Thumbnail upload skipped/failed: {exc}"
                warnings.append(warning)
                if notify is not None:
                    notify(f"WARN: {warning}")
            finally:
                cleanup_path = prepared_thumbnail.cleanup_path if prepared_thumbnail is not None else None
                if cleanup_path is not None:
                    cleanup_path.unlink(missing_ok=True)
        else:
            warning = f"Thumbnail file not found: {thumbnail_path}"
            warnings.append(warning)
            if notify is not None:
                notify(f"WARN: {warning}")

    if video_id and draft.upload_captions and draft.captions_path:
        captions_path = Path(draft.captions_path).expanduser().resolve()
        if not captions_path.exists():
            warning = f"Captions file not found: {captions_path}"
            warnings.append(warning)
            if notify is not None:
                notify(f"WARN: {warning}")
        elif not youtube_token_has_scope(token_payload, YOUTUBE_FORCE_SSL_SCOPE):
            warning = (
                "Caption upload skipped: current token does not include youtube.force-ssl. "
                "Add that scope in Google Cloud and relink to enable caption upload."
            )
            warnings.append(warning)
            if notify is not None:
                notify(f"WARN: {warning}")
        else:
            try:
                if notify is not None:
                    notify(f"Uploading captions {captions_path.name}.")
                captions_result = insert_youtube_captions(
                    access_token=access_token,
                    video_id=video_id,
                    captions_path=captions_path,
                    language="en",
                    name="English",
                )
            except Exception as exc:  # noqa: BLE001
                warning = f"Caption upload skipped/failed: {exc}"
                warnings.append(warning)
                if notify is not None:
                    notify(f"WARN: {warning}")

    report_payload: dict[str, Any] = {
        "platform": "youtube",
        "status": "uploaded_with_warnings" if warnings else "uploaded",
        "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project_dir": str(project_dir),
        "video_path": str(video_path),
        "draft_path": str(youtube_draft_path(project_dir)),
        "video_id": video_id or None,
        "video_url": video_url,
        "visibility": status["privacyStatus"],
        "publish_at": status.get("publishAt"),
        "upload_captions_requested": bool(draft.upload_captions),
        "thumbnail_uploaded": bool(thumbnail_result),
        "captions_uploaded": bool(captions_result),
        "warnings": warnings,
        "thumbnail_response": thumbnail_result,
        "thumbnail_upload": thumbnail_upload_details,
        "captions_response": captions_result,
        "response": resource,
    }
    report_path = save_youtube_publish_report(project_dir, report_payload)
    report_payload["report_path"] = str(report_path)
    return report_payload


def draft_review_text(draft: YouTubePublishDraft) -> str:
    tag_text = ", ".join(draft.tags) if draft.tags else "(none)"
    schedule_text = draft.schedule_at or "publish immediately"
    thumbnail_text = draft.thumbnail_path or "(none selected)"
    captions_text = draft.captions_path or "(no captions file found)"
    return "\n".join(
        [
            f"Project: {draft.project_dir}",
            f"Video: {draft.video_path}",
            f"Captions: {captions_text}",
            f"Visibility: {draft.visibility}",
            f"Schedule: {schedule_text}",
            f"Category: {draft.category}",
            f"Thumbnail: {thumbnail_text}",
            f"Thumbnail source: {draft.thumbnail_source}",
            f"Upload captions: {'yes' if draft.upload_captions else 'no'}",
            "",
            f"Title: {draft.title}",
            "",
            "Description:",
            draft.description.strip() or "(empty)",
            "",
            f"Tags: {tag_text}",
        ]
    ).strip()


def auth_review_text(auth_state: YouTubeAuthState) -> str:
    lines = [
        f"Linked: {'yes' if auth_state.linked else 'no'}",
        auth_state.reason,
    ]
    if auth_state.client_secrets_path:
        lines.append(f"Client secrets: {auth_state.client_secrets_path}")
    else:
        lines.append(f"Client secrets expected at: {youtube_auth_client_secrets_path()}")
    if auth_state.token_path:
        lines.append(f"Saved token: {auth_state.token_path}")
    else:
        lines.append(f"Token expected at: {youtube_auth_token_path()}")
    if auth_state.linked:
        lines.append("Use the YouTube account menu to switch channels/accounts or disconnect this token.")
    return "\n".join(lines)


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _load_youtube_client_secrets_payload() -> dict[str, Any]:
    client_path = youtube_auth_client_secrets_path()
    if not client_path.exists():
        raise RuntimeError(f"YouTube OAuth client secrets file not found: {client_path}")

    payload = json.loads(client_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"YouTube OAuth client secrets file is invalid: {client_path}")

    installed_payload = payload.get("installed")
    if not isinstance(installed_payload, dict):
        raise RuntimeError("Expected Google desktop OAuth client JSON with a top-level `installed` object.")
    return installed_payload


def _preferred_loopback_host(redirect_uris: Any) -> str:
    if isinstance(redirect_uris, list):
        for raw_uri in redirect_uris:
            parsed = urllib.parse.urlparse(str(raw_uri))
            if parsed.hostname == "127.0.0.1":
                return "127.0.0.1"
            if parsed.hostname == "localhost":
                return "localhost"
    return "localhost"


def _generate_code_verifier() -> str:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii")
    return verifier.rstrip("=")


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _oauth_callback_server(
    host: str,
    callback_queue: queue.Queue[dict[str, str]],
) -> http.server.ThreadingHTTPServer:
    class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            params = {
                key: values[-1]
                for key, values in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()
                if values
            }
            callback_queue.put(params)

            if params.get("error"):
                message = (
                    "<h2>Imagine YouTube auth failed.</h2>"
                    "<p>You can close this window and return to the terminal.</p>"
                )
                self.send_response(400)
            else:
                message = (
                    "<h2>Imagine YouTube auth complete.</h2>"
                    "<p>You can close this window and return to the TUI.</p>"
                )
                self.send_response(200)

            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            body = (
                "<!doctype html><html><head><meta charset='utf-8'><title>Imagine YouTube Auth</title></head>"
                f"<body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:32px'>{message}</body></html>"
            )
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    return http.server.ThreadingHTTPServer((host, 0), OAuthCallbackHandler)


def _open_auth_url(url: str) -> None:
    opened = False
    try:
        opened = bool(webbrowser.open(url, new=1, autoraise=True))
    except Exception:
        opened = False

    if opened:
        return

    if subprocess.run(["open", url], capture_output=True, text=True, check=False).returncode == 0:
        return

    raise RuntimeError(
        "Could not open the browser automatically for Google OAuth. "
        f"Open this URL manually: {url}"
    )


def _post_form_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(
        {key: str(value) for key, value in payload.items() if str(value or "").strip()}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google OAuth request failed ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Google OAuth endpoint: {exc}") from exc

    payload_json = json.loads(raw)
    if not isinstance(payload_json, dict):
        raise RuntimeError("Google OAuth endpoint returned an invalid JSON payload.")
    return payload_json


def _revoke_google_oauth_token(token: str) -> None:
    encoded = urllib.parse.urlencode({"token": token}).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/revoke",
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google revoke endpoint failed ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Google revoke endpoint: {exc}") from exc


def _start_resumable_video_upload(
    *,
    access_token: str,
    video_path: Path,
    snippet: dict[str, Any],
    status: dict[str, Any],
) -> str:
    mime_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
    metadata = {
        "snippet": snippet,
        "status": status,
    }
    response = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={
            "part": "snippet,status",
            "uploadType": "resumable",
        },
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(video_path.stat().st_size),
            "X-Upload-Content-Type": mime_type,
        },
        data=json.dumps(metadata),
        timeout=60,
    )
    _raise_for_status(response, "Failed to initialize YouTube resumable upload")
    upload_url = str(response.headers.get("Location") or "").strip()
    if not upload_url:
        raise RuntimeError("YouTube resumable upload initialization did not return an upload URL.")
    return upload_url


def _upload_resumable_video_bytes(
    *,
    access_token: str,
    upload_url: str,
    video_path: Path,
    chunk_bytes: int,
    notify: Callable[[str], None] | None,
    progress: Callable[[int, int], None] | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    total_bytes = video_path.stat().st_size
    mime_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
    uploaded = 0
    last_logged_percent = -1

    with video_path.open("rb") as handle:
        while uploaded < total_bytes:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("YouTube upload was cancelled.")

            handle.seek(uploaded)
            chunk = handle.read(max(256 * 1024, int(chunk_bytes)))
            if not chunk:
                break

            chunk_end = uploaded + len(chunk) - 1
            response = requests.put(
                upload_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": str(len(chunk)),
                    "Content-Type": mime_type,
                    "Content-Range": f"bytes {uploaded}-{chunk_end}/{total_bytes}",
                },
                data=chunk,
                timeout=300,
            )

            if response.status_code in (200, 201):
                uploaded = total_bytes
                if progress is not None:
                    progress(uploaded, total_bytes)
                if notify is not None:
                    notify(f"YouTube upload finished for {video_path.name}.")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("YouTube upload completed, but the response payload was invalid.")
                return payload

            if response.status_code == 308:
                committed = _uploaded_range_end(response.headers.get("Range"))
                if committed is not None:
                    uploaded = committed + 1
                else:
                    uploaded = chunk_end + 1
                if progress is not None:
                    progress(uploaded, total_bytes)
                percent = int((uploaded / max(1, total_bytes)) * 100)
                if notify is not None and percent != last_logged_percent and percent % 10 == 0:
                    notify(f"YouTube upload progress: {percent}%")
                    last_logged_percent = percent
                continue

            _raise_for_status(response, "YouTube upload chunk failed")

    raise RuntimeError("YouTube upload stopped before completion.")


def _uploaded_range_end(range_header: str | None) -> int | None:
    match = re.search(r"bytes=0-(\d+)", str(range_header or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _normalize_publish_at(value: str) -> str:
    normalized = str(value or "").strip().replace(" ", "T")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raise_for_status(response: requests.Response, message: str) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
    except Exception:
        payload = response.text
    raise RuntimeError(f"{message} ({response.status_code}): {payload}")


def set_youtube_thumbnail(
    *,
    access_token: str,
    video_id: str,
    thumbnail_path: Path,
    mime_type: str | None = None,
) -> dict[str, Any]:
    resolved_mime_type = mime_type or mimetypes.guess_type(thumbnail_path.name)[0] or "application/octet-stream"
    with thumbnail_path.open("rb") as handle:
        response = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            params={"videoId": video_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": resolved_mime_type,
            },
            data=handle.read(),
            timeout=120,
        )
    _raise_for_status(response, "Failed to upload YouTube thumbnail")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("YouTube thumbnail upload returned an invalid response payload.")
    return payload


def prepare_youtube_thumbnail_for_upload(
    *,
    project_dir: Path,
    thumbnail_path: Path,
    notify: Callable[[str], None] | None = None,
) -> PreparedYouTubeThumbnail:
    resolved_thumbnail_path = thumbnail_path.expanduser().resolve()
    source_size_bytes = resolved_thumbnail_path.stat().st_size
    source_mime_type = mimetypes.guess_type(resolved_thumbnail_path.name)[0] or "application/octet-stream"
    direct_upload_supported = (
        source_mime_type in YOUTUBE_ALLOWED_THUMBNAIL_MIME_TYPES
        and source_size_bytes <= YOUTUBE_THUMBNAIL_MAX_BYTES
    )
    if direct_upload_supported:
        return PreparedYouTubeThumbnail(
            path=resolved_thumbnail_path,
            mime_type=source_mime_type,
            source_path=resolved_thumbnail_path,
            source_size_bytes=source_size_bytes,
            upload_size_bytes=source_size_bytes,
            optimized=False,
        )

    optimization_reasons: list[str] = []
    if source_mime_type not in YOUTUBE_ALLOWED_THUMBNAIL_MIME_TYPES:
        optimization_reasons.append(f"format {source_mime_type} is not directly supported by YouTube thumbnails")
    if source_size_bytes > YOUTUBE_THUMBNAIL_MAX_BYTES:
        optimization_reasons.append(
            f"file size {source_size_bytes} bytes exceeds YouTube's {YOUTUBE_THUMBNAIL_MAX_BYTES}-byte limit"
        )
    reason_text = "; ".join(optimization_reasons) or "thumbnail needs upload-safe normalization"

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise RuntimeError(
            "Thumbnail cannot be uploaded as-is because "
            f"{reason_text}, and ffmpeg was not found to prepare an upload-safe image."
        )

    if notify is not None:
        notify(f"Preparing thumbnail for YouTube upload because {reason_text}.")

    project_publish_dir = youtube_publish_dir(project_dir)
    project_publish_dir.mkdir(parents=True, exist_ok=True)
    last_size_bytes = source_size_bytes
    for width, height in YOUTUBE_THUMBNAIL_SCALE_STEPS:
        for quality in YOUTUBE_THUMBNAIL_JPEG_QUALITIES:
            with tempfile.NamedTemporaryFile(
                prefix="youtube-thumbnail-",
                suffix=".jpg",
                dir=str(project_publish_dir),
                delete=False,
            ) as handle:
                candidate_path = Path(handle.name)

            command = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(resolved_thumbnail_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                "-q:v",
                str(quality),
                str(candidate_path),
            ]
            run = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if int(run.returncode or 0) != 0:
                candidate_path.unlink(missing_ok=True)
                stderr_text = str(run.stderr or "").strip()
                raise RuntimeError(f"ffmpeg could not prepare a YouTube thumbnail: {stderr_text or 'unknown error'}")

            candidate_size_bytes = candidate_path.stat().st_size if candidate_path.exists() else 0
            if 0 < candidate_size_bytes <= YOUTUBE_THUMBNAIL_MAX_BYTES:
                return PreparedYouTubeThumbnail(
                    path=candidate_path,
                    mime_type="image/jpeg",
                    source_path=resolved_thumbnail_path,
                    source_size_bytes=source_size_bytes,
                    upload_size_bytes=candidate_size_bytes,
                    optimized=True,
                    optimization_reason=reason_text,
                    cleanup_path=candidate_path,
                )

            last_size_bytes = candidate_size_bytes
            candidate_path.unlink(missing_ok=True)

    raise RuntimeError(
        "Could not prepare a YouTube thumbnail under "
        f"{YOUTUBE_THUMBNAIL_MAX_BYTES} bytes; smallest attempt was {last_size_bytes} bytes."
    )


def insert_youtube_captions(
    *,
    access_token: str,
    video_id: str,
    captions_path: Path,
    language: str,
    name: str,
) -> dict[str, Any]:
    boundary = f"===============ImagineCaption{secrets.token_hex(12)}=="
    metadata = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": name,
            "isDraft": False,
        }
    }
    mime_type = mimetypes.guess_type(captions_path.name)[0] or "application/octet-stream"
    metadata_bytes = json.dumps(metadata, ensure_ascii=True).encode("utf-8")
    media_bytes = captions_path.read_bytes()
    body = (
        b"--" + boundary.encode("ascii") + b"\r\n"
        + b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        + metadata_bytes
        + b"\r\n--" + boundary.encode("ascii") + b"\r\n"
        + f"Content-Type: {mime_type}\r\n\r\n".encode("ascii")
        + media_bytes
        + b"\r\n--" + boundary.encode("ascii") + b"--\r\n"
    )

    response = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/captions",
        params={"part": "snippet", "uploadType": "multipart"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f'multipart/related; boundary="{boundary}"',
        },
        data=body,
        timeout=120,
    )
    _raise_for_status(response, "Failed to upload YouTube captions")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("YouTube captions upload returned an invalid response payload.")
    return payload


def _load_prompt_text(project_dir: Path, *, fallback_prompt: str) -> str:
    prompt_path = project_dir / "prompt.txt"
    if prompt_path.exists():
        value = prompt_path.read_text(encoding="utf-8").strip()
        if value:
            return value
    return str(fallback_prompt or "").strip()


def _default_thumbnail_prompt_text(project_dir: Path, *, fallback_prompt: str) -> str:
    script_payload = _load_json_object(project_dir / "script.json")
    if isinstance(script_payload, dict):
        title = _optional_str(script_payload.get("title"))
        if title:
            return title
    prompt_value = _load_prompt_text(project_dir, fallback_prompt=fallback_prompt)
    if prompt_value:
        return prompt_value
    return _humanize_project_name(project_dir.name) or "Why this matters now"


def _scene_headings(script_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(script_payload, dict):
        return []
    raw_scenes = script_payload.get("scenes")
    if not isinstance(raw_scenes, list):
        return []
    headings: list[str] = []
    for scene in raw_scenes:
        if not isinstance(scene, dict):
            continue
        heading = _optional_str(scene.get("heading"))
        if heading:
            headings.append(heading)
    return _dedupe_strings(headings)[:6]


def _asset_keywords(rights_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(rights_payload, dict):
        return []
    config = rights_payload.get("config")
    if not isinstance(config, dict):
        return []
    raw_keywords = config.get("asset_keywords")
    if not isinstance(raw_keywords, list):
        return []
    return _dedupe_strings(str(item).strip() for item in raw_keywords if str(item).strip())[:8]


def _manifest_burn_subtitles(rights_payload: dict[str, Any] | None) -> bool:
    if not isinstance(rights_payload, dict):
        return True
    config = rights_payload.get("config")
    if not isinstance(config, dict):
        return True
    value = config.get("burn_subtitles")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return True


def _title_suggestions(*, title: str, prompt_value: str, scene_headings: list[str]) -> list[str]:
    candidates = [
        title,
        prompt_value,
        scene_headings[0] if scene_headings else "",
    ]
    normalized: list[str] = []
    for item in candidates:
        cleaned = _title_case_sentence(item)
        if not cleaned:
            continue
        normalized.append(cleaned[:100].strip())
    return _dedupe_strings(normalized)[:3]


def _description_text(
    *,
    title: str,
    summary: str,
    scene_headings: list[str],
    required_credits_block: str = "",
) -> str:
    parts: list[str] = []
    cleaned_summary = str(summary or "").strip()
    if cleaned_summary:
        parts.append(cleaned_summary)
    else:
        parts.append(f"This video breaks down {title.lower()}.")

    if scene_headings:
        parts.append("In this video:")
        parts.extend(f"- {heading}" for heading in scene_headings[:5])

    parts.append("")
    parts.append("Built with the local Imagine workflow.")
    cleaned_credits = str(required_credits_block or "").strip()
    if cleaned_credits:
        parts.extend(["", cleaned_credits])
    return "\n".join(part.rstrip() for part in parts).strip()


def _manifest_required_credits_block(rights_payload: dict[str, Any] | None) -> str:
    if not isinstance(rights_payload, dict):
        return ""
    credits = rights_payload.get("credits")
    if isinstance(credits, dict):
        return str(credits.get("required_description_block") or "").strip()
    return ""


def _tag_suggestions(
    *,
    title: str,
    prompt_value: str,
    scene_headings: list[str],
    asset_keywords: list[str],
) -> list[str]:
    tokens: list[str] = []
    for source in [title, prompt_value, *scene_headings[:3], *asset_keywords]:
        for token in re.split(r"[^a-z0-9]+", str(source).lower()):
            cleaned = token.strip()
            if len(cleaned) < 3 or cleaned in _STOP_WORDS:
                continue
            tokens.append(cleaned)
    return _dedupe_strings(tokens)[:8]


def _coerce_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _normalize_thumbnail_color(value: Any, default: str, *, choices: tuple[str, ...]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in choices:
        return normalized
    return default


def _normalize_thumbnail_font_size_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in YOUTUBE_THUMBNAIL_FONT_SIZE_CHOICES:
        return normalized
    return YOUTUBE_THUMBNAIL_DEFAULT_FONT_SIZE


def _normalize_thumbnail_anchor(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in YOUTUBE_THUMBNAIL_ANCHOR_CHOICES:
        return normalized
    return YOUTUBE_THUMBNAIL_DEFAULT_ANCHOR


def _normalize_thumbnail_text_align(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in YOUTUBE_THUMBNAIL_TEXT_ALIGN_CHOICES:
        return normalized
    return YOUTUBE_THUMBNAIL_DEFAULT_TEXT_ALIGN


def _normalize_thumbnail_offset(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-YOUTUBE_THUMBNAIL_MAX_OFFSET, min(YOUTUBE_THUMBNAIL_MAX_OFFSET, parsed))


def _thumbnail_color_value(color_name: Any) -> str:
    normalized = str(color_name or "").strip().lower()
    return YOUTUBE_THUMBNAIL_COLOR_VALUES.get(normalized, YOUTUBE_THUMBNAIL_COLOR_VALUES[YOUTUBE_THUMBNAIL_DEFAULT_FONT_COLOR])


def _wrap_youtube_thumbnail_text(prompt_text: str) -> str:
    cleaned = " ".join(str(prompt_text or "").split()).strip()
    if not cleaned:
        return "Why this matters now"

    lines = textwrap.wrap(
        cleaned,
        width=18,
        replace_whitespace=False,
        drop_whitespace=True,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not lines:
        return "Why this matters now"
    limited = lines[:4]
    if len(lines) > 4:
        tail = limited[-1].rstrip(". ")
        limited[-1] = f"{tail}..."
    return "\n".join(line.upper() for line in limited if line)


def _youtube_thumbnail_title_font_size(
    *,
    width: int,
    height: int,
    title_lines: list[str],
) -> int:
    line_count = max(1, len([line for line in title_lines if line.strip()]))
    longest = max((len(line.strip()) for line in title_lines if line.strip()), default=12)

    base = int(height * 0.118)
    if line_count == 2:
        base = int(height * 0.104)
    elif line_count == 3:
        base = int(height * 0.088)
    elif line_count >= 4:
        base = int(height * 0.076)

    if longest >= 22:
        base = int(base * 0.88)
    elif longest >= 18:
        base = int(base * 0.94)

    max_by_width = int((width * 0.88) / max(6, longest) * 1.85)
    return max(34, min(base, max_by_width))


def _youtube_thumbnail_seek_seconds(media_path: Path, variant_index: int = 0) -> float:
    duration = _probe_media_duration_seconds(media_path)
    if duration <= 0.5:
        return 0.0
    fraction = YOUTUBE_THUMBNAIL_VARIANT_FRACTIONS[variant_index % len(YOUTUBE_THUMBNAIL_VARIANT_FRACTIONS)]
    return max(0.0, min(duration - 0.2, duration * fraction))


def _probe_media_duration_seconds(media_path: Path) -> float:
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None or not media_path.exists():
        return 0.0

    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    run = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if int(run.returncode or 0) != 0:
        return 0.0
    try:
        return max(0.0, float(str(run.stdout or "").strip()))
    except Exception:
        return 0.0


def _escape_drawtext_path(path: Path) -> str:
    value = str(path.expanduser().resolve()).replace("\\", r"\\")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    value = value.replace(",", r"\,")
    value = value.replace("%", r"\%")
    return value


def _thumbnail_drawtext_position(
    *,
    width: int,
    height: int,
    anchor: str,
    text_align: str,
    offset_x: int,
    offset_y: int,
) -> tuple[str, str]:
    horizontal_key, vertical_key = _thumbnail_anchor_parts(anchor)
    anchor_x_values = {
        "left": int(width * 0.06),
        "center": int(width * 0.50),
        "right": int(width * 0.94),
    }
    anchor_y_values = {
        "top": int(height * 0.08),
        "center": int(height * 0.50),
        "bottom": int(height * 0.88),
    }
    anchor_x = anchor_x_values[horizontal_key]
    anchor_y = anchor_y_values[vertical_key]

    if text_align == "center":
        x_expr = f"{anchor_x}-(text_w/2)+({offset_x})"
    elif text_align == "right":
        x_expr = f"{anchor_x}-text_w+({offset_x})"
    else:
        x_expr = f"{anchor_x}+({offset_x})"

    if vertical_key == "center":
        y_expr = f"{anchor_y}-(text_h/2)+({offset_y})"
    elif vertical_key == "bottom":
        y_expr = f"{anchor_y}-text_h+({offset_y})"
    else:
        y_expr = f"{anchor_y}+({offset_y})"
    return x_expr, y_expr


def _thumbnail_anchor_parts(anchor: str) -> tuple[str, str]:
    normalized = _normalize_thumbnail_anchor(anchor)
    if "-" not in normalized:
        return "center", "center"
    vertical_key, horizontal_key = normalized.split("-", 1)
    if vertical_key not in {"top", "center", "bottom"}:
        vertical_key = "center"
    if horizontal_key not in {"left", "center", "right"}:
        horizontal_key = "center"
    return horizontal_key, vertical_key


def _thumbnail_render_input(
    project_dir: Path,
    *,
    background_variant: int,
    working_dir: Path,
    notify: Callable[[str], None] | None,
) -> tuple[list[str], list[Path]]:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return ["-f", "lavfi", "-i", "color=c=0x152238:s=1280x720"], []

    source_candidates = _youtube_thumbnail_source_assets(project_dir)
    cleanup_paths: list[Path] = []
    candidate_media = source_candidates[background_variant % len(source_candidates)] if source_candidates else None

    def extract_frame(media_path: Path, *, variant_index: int) -> Path | None:
        frame_path = working_dir / f"thumbnail-background-{variant_index:02d}.jpg"
        timestamp = _youtube_thumbnail_seek_seconds(media_path, variant_index)
        command = [
            ffmpeg_bin,
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(frame_path),
        ]
        run = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if int(run.returncode or 0) == 0 and frame_path.exists():
            cleanup_paths.append(frame_path)
            return frame_path
        if notify is not None:
            notify(
                f"WARN: Could not extract thumbnail frame from {media_path.name}: "
                f"{str(run.stderr or '').strip() or 'unknown ffmpeg error'}"
            )
        frame_path.unlink(missing_ok=True)
        return None

    if candidate_media is not None and candidate_media.exists():
        if candidate_media.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            return ["-i", str(candidate_media)], cleanup_paths
        extracted = extract_frame(candidate_media, variant_index=background_variant)
        if extracted is not None:
            return ["-i", str(extracted)], cleanup_paths

    final_mp4 = project_dir / "output" / "final.mp4"
    if final_mp4.exists():
        extracted = extract_frame(final_mp4, variant_index=background_variant)
        if extracted is not None:
            return ["-i", str(extracted)], cleanup_paths

    brand_candidates = [
        project_dir / "publish" / "thumbnail_background.jpg",
        project_dir / "publish" / "thumbnail_background.png",
        project_dir.parent / "brand-kit" / "channel-bg-intro.jpg",
        project_dir.parent / "brand-kit" / "channel-bg-outro.jpg",
    ]
    for candidate in brand_candidates:
        if candidate.exists():
            return ["-i", str(candidate.resolve())], cleanup_paths

    return ["-f", "lavfi", "-i", "color=c=0x152238:s=1280x720"], cleanup_paths


def _youtube_thumbnail_source_assets(project_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(path_value: str | None) -> None:
        if not path_value:
            return
        resolved = Path(str(path_value)).expanduser().resolve()
        if not resolved.exists() or resolved in seen:
            return
        seen.add(resolved)
        candidates.append(resolved)

    timeline_path = project_dir / "timeline.json"
    if timeline_path.exists():
        try:
            payload = json.loads(timeline_path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        clips = payload.get("clips") if isinstance(payload, dict) else None
        if isinstance(clips, list):
            for clip in clips:
                if not isinstance(clip, dict):
                    continue
                scene_id = str(clip.get("scene_id") or "").strip()
                if scene_id in {"", "__intro", "__outro"}:
                    continue
                add(str(clip.get("source_path") or "").strip() or None)

    clip_catalog_path = project_dir / "review" / "clip_catalog.json"
    if clip_catalog_path.exists():
        try:
            payload = json.loads(clip_catalog_path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        clips = payload.get("clips") if isinstance(payload, dict) else None
        if isinstance(clips, list):
            for clip in clips:
                if not isinstance(clip, dict):
                    continue
                add(str(clip.get("asset_path") or "").strip() or None)

    return candidates


def _detect_thumbnail_path(project_dir: Path) -> Path | None:
    candidates = [
        project_dir / YOUTUBE_DEFAULT_THUMBNAIL_PATH,
        project_dir / "output" / "thumbnail_yt.png",
        project_dir / "thumbnail_yt.png",
        project_dir / "output" / "thumbnail_yt.jpg",
        project_dir / "thumbnail_yt.jpg",
        project_dir / "review" / "debug" / "thumbnail",
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
        if candidate.is_dir():
            matches = sorted(
                [path for path in candidate.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}],
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if matches:
                return matches[0].resolve()
    return None


def _humanize_project_name(value: str) -> str:
    words = re.split(r"[-_]+", str(value or "").strip())
    return " ".join(word for word in words if word).strip().title()


def _title_case_sentence(value: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        return ""
    if cleaned.islower():
        return cleaned.title()
    return cleaned


def _normalize_visibility(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    if lowered in YOUTUBE_VISIBILITY_CHOICES:
        return lowered
    return "private"


def _optional_str(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return _dedupe_strings(str(item).strip() for item in values if str(item).strip())


def _dedupe_strings(values: Any) -> list[str]:
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


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return ""
