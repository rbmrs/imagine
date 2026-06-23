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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, cast

import requests
try:
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont
except Exception:  # noqa: BLE001
    Image = None
    ImageColor = None
    ImageDraw = None
    ImageFilter = None
    ImageFont = None

YOUTUBE_VISIBILITY_CHOICES = ("private", "unlisted", "public")
YOUTUBE_CATEGORY_DEFAULT = "Education"
YOUTUBE_DRAFT_SCHEMA_VERSION = 5
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_DEFAULT_SCOPES = (YOUTUBE_UPLOAD_SCOPE,)
YOUTUBE_OAUTH_TIMEOUT_SECONDS = 300
YOUTUBE_RESUMABLE_CHUNK_BYTES = 8 * 1024 * 1024
YOUTUBE_CATEGORY_IDS = {
    "Education": "27",
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
    thumbnail_path: str | None = None
    title: str = ""
    title_suggestions: list[str] = field(default_factory=list)
    description: str = ""
    visibility: str = "private"
    schedule_at: str | None = None
    tags: list[str] = field(default_factory=list)
    category: str = YOUTUBE_CATEGORY_DEFAULT
    upload_captions: bool = False
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "YouTubePublishDraft":
        return cls(
            schema_version=int(payload.get("schema_version") or YOUTUBE_DRAFT_SCHEMA_VERSION),
            platform=str(payload.get("platform") or "youtube").strip() or "youtube",
            project_dir=str(payload.get("project_dir") or "").strip(),
            video_path=str(payload.get("video_path") or "").strip(),
            captions_path=_optional_str(payload.get("captions_path")),
            thumbnail_path=_optional_str(payload.get("thumbnail_path")),
            title=str(payload.get("title") or "").strip(),
            title_suggestions=_string_list(payload.get("title_suggestions")),
            description=str(payload.get("description") or "").strip(),
            visibility=_normalize_visibility(payload.get("visibility")),
            schedule_at=_optional_str(payload.get("schedule_at")),
            tags=_string_list(payload.get("tags")),
            category=str(payload.get("category") or YOUTUBE_CATEGORY_DEFAULT).strip() or YOUTUBE_CATEGORY_DEFAULT,
            upload_captions=bool(payload.get("upload_captions")),
            updated_at=str(payload.get("updated_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["visibility"] = _normalize_visibility(self.visibility)
        payload["title_suggestions"] = _dedupe_strings(self.title_suggestions)
        payload["tags"] = _dedupe_strings(self.tags)
        return payload


@dataclass(frozen=True)
class VideoPackagePaths:
    package_dir: Path
    metadata_dir: Path
    video_path: Path
    captions_path: Path
    prompt_path: Path
    script_path: Path
    timeline_path: Path
    rights_manifest_path: Path
    run_report_path: Path
    run_log_path: Path
    review_dir: Path
    publish_dir: Path
    output_dir: Path


def resolve_video_package(path: Path) -> VideoPackagePaths:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() == ".mp4":
        package_dir = resolved.parent
        video_path = resolved
    elif resolved.name == "metadata":
        package_dir = resolved.parent
        video_path = package_dir / f"{package_dir.name}.mp4"
    elif (resolved / "metadata").exists():
        package_dir = resolved
        video_path = package_dir / f"{package_dir.name}.mp4"
    else:
        package_dir = resolved.parent
        video_path = package_dir / f"{package_dir.name}.mp4"

    metadata_dir = package_dir / "metadata"
    return VideoPackagePaths(
        package_dir=package_dir,
        metadata_dir=metadata_dir,
        video_path=video_path,
        captions_path=metadata_dir / "final.srt",
        prompt_path=metadata_dir / "prompt.txt",
        script_path=metadata_dir / "script.json",
        timeline_path=metadata_dir / "timeline.json",
        rights_manifest_path=metadata_dir / "rights_manifest.json",
        run_report_path=metadata_dir / "run_report.json",
        run_log_path=metadata_dir / "run.log",
        review_dir=metadata_dir / "review",
        publish_dir=metadata_dir / "publish",
        output_dir=metadata_dir / "output",
    )


def youtube_publish_dir(project_dir: Path) -> Path:
    return resolve_video_package(project_dir).publish_dir


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


def build_youtube_publish_draft(
    project_dir: Path,
    *,
    fallback_prompt: str = "",
) -> YouTubePublishDraft:
    package = resolve_video_package(project_dir)
    resolved_project_dir = package.metadata_dir
    script_payload = _load_json_object(package.script_path)
    rights_payload = _load_json_object(package.rights_manifest_path)

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
        _humanize_project_name(package.package_dir.name),
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

    video_path = package.video_path
    captions_path = package.captions_path
    thumbnail_path = _default_thumbnail_path(package)
    return YouTubePublishDraft(
        project_dir=str(resolved_project_dir),
        video_path=str(video_path),
        captions_path=str(captions_path) if captions_path.exists() else None,
        thumbnail_path=str(thumbnail_path) if thumbnail_path is not None else None,
        title=title,
        title_suggestions=title_suggestions,
        description=description,
        visibility="private",
        schedule_at=None,
        tags=tags,
        category=YOUTUBE_CATEGORY_DEFAULT,
        upload_captions=not burn_subtitles,
        updated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


def ensure_youtube_publish_draft(
    project_dir: Path,
    *,
    fallback_prompt: str = "",
) -> YouTubePublishDraft:
    package = resolve_video_package(project_dir)
    draft = load_youtube_publish_draft(package.metadata_dir)
    if draft is not None:
        return draft

    draft = build_youtube_publish_draft(
        package.metadata_dir,
        fallback_prompt=fallback_prompt,
    )
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
    package = resolve_video_package(project_dir)
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
        "selfDeclaredMadeForKids": False,
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
    captions_result: dict[str, Any] | None = None
    thumbnail_result: dict[str, Any] | None = None

    thumbnail_path_value = str(draft.thumbnail_path or "").strip()
    if video_id and thumbnail_path_value:
        thumbnail_path = Path(thumbnail_path_value).expanduser().resolve()
        if not thumbnail_path.exists():
            warning = f"Thumbnail file not found: {thumbnail_path}"
            warnings.append(warning)
            if notify is not None:
                notify(f"WARN: {warning}")
        else:
            try:
                if notify is not None:
                    notify(f"Uploading thumbnail {thumbnail_path.name}.")
                thumbnail_result = set_youtube_thumbnail(
                    access_token=access_token,
                    video_id=video_id,
                    thumbnail_path=thumbnail_path,
                )
            except Exception as exc:  # noqa: BLE001
                warning = f"Thumbnail upload skipped/failed: {exc}"
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
        "package_dir": str(package.package_dir),
        "video_path": str(video_path),
        "draft_path": str(youtube_draft_path(project_dir)),
        "video_id": video_id or None,
        "video_url": video_url,
        "visibility": status["privacyStatus"],
        "self_declared_made_for_kids": bool(status.get("selfDeclaredMadeForKids")),
        "publish_at": status.get("publishAt"),
        "thumbnail_path": thumbnail_path_value or None,
        "thumbnail_uploaded": bool(thumbnail_result),
        "upload_captions_requested": bool(draft.upload_captions),
        "captions_uploaded": bool(captions_result),
        "warnings": warnings,
        "thumbnail_response": thumbnail_result,
        "captions_response": captions_result,
        "response": resource,
    }
    report_path = save_youtube_publish_report(project_dir, report_payload)
    report_payload["report_path"] = str(report_path)
    return report_payload


def draft_review_text(draft: YouTubePublishDraft) -> str:
    tag_text = ", ".join(draft.tags) if draft.tags else "(none)"
    schedule_text = draft.schedule_at or "publish immediately"
    captions_text = draft.captions_path or "(no captions file found)"
    thumbnail_text = draft.thumbnail_path or "(none selected)"
    return "\n".join(
        [
            f"Project: {draft.project_dir}",
            f"Video: {draft.video_path}",
            f"Captions: {captions_text}",
            f"Thumbnail: {thumbnail_text}",
            f"Visibility: {draft.visibility}",
            f"Schedule: {schedule_text}",
            f"Category: {draft.category}",
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


def _load_prompt_text(project_dir: Path, *, fallback_prompt: str = "") -> str:
    package = resolve_video_package(project_dir)
    prompt_path = package.prompt_path
    if prompt_path.exists():
        text = prompt_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return str(fallback_prompt or "").strip()


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
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(thumbnail_path.name)[0] or "image/jpeg"
    response = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
        params={"videoId": video_id},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": mime_type,
        },
        data=thumbnail_path.read_bytes(),
        timeout=120,
    )
    _raise_for_status(response, "Failed to upload YouTube thumbnail")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("YouTube thumbnail upload completed, but the response payload was invalid.")
    return payload


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


def _scene_headings(script_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(script_payload, dict):
        return []
    scenes = script_payload.get("scenes")
    if not isinstance(scenes, list):
        return []
    headings: list[str] = []
    seen: set[str] = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        heading = str(scene.get("heading") or "").strip()
        if not heading:
            continue
        key = heading.casefold()
        if key in seen:
            continue
        seen.add(key)
        headings.append(heading)
    return headings


def _asset_keywords(rights_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(rights_payload, dict):
        return []
    config_payload = rights_payload.get("config")
    if not isinstance(config_payload, dict):
        return []
    raw_keywords = config_payload.get("asset_keywords")
    if not isinstance(raw_keywords, list):
        return []
    return _dedupe_strings(str(item).strip() for item in raw_keywords if str(item).strip())


def _manifest_burn_subtitles(rights_payload: dict[str, Any] | None) -> bool:
    if not isinstance(rights_payload, dict):
        return False
    config_payload = rights_payload.get("config")
    if not isinstance(config_payload, dict):
        return False
    return bool(config_payload.get("burn_subtitles"))


def _manifest_required_credits_block(rights_payload: dict[str, Any] | None) -> str:
    if not isinstance(rights_payload, dict):
        return ""
    credits_payload = rights_payload.get("credits")
    if not isinstance(credits_payload, dict):
        return ""
    return str(credits_payload.get("required_description_block") or "").strip()


def _title_suggestions(
    *,
    title: str,
    prompt_value: str,
    scene_headings: list[str],
) -> list[str]:
    candidates = [title]
    if prompt_value:
        candidates.append(_title_case_sentence(prompt_value))
    if scene_headings:
        candidates.append(_title_case_sentence(f"{title}: {scene_headings[0]}"))
    if len(scene_headings) >= 2:
        candidates.append(_title_case_sentence(f"{scene_headings[0]} e {scene_headings[1]}"))
    return _dedupe_strings(candidate for candidate in candidates if str(candidate).strip())[:4]


def _description_text(
    *,
    title: str,
    summary: str,
    scene_headings: list[str],
    required_credits_block: str,
) -> str:
    lines: list[str] = []
    cleaned_summary = str(summary or "").strip()
    if cleaned_summary:
        lines.append(cleaned_summary)
    elif title.strip():
        lines.append(title.strip())

    if scene_headings:
        lines.append("")
        lines.append("Neste video:")
        for heading in scene_headings[:6]:
            lines.append(f"- {heading}")

    if required_credits_block:
        lines.append("")
        lines.append(required_credits_block.strip())

    return "\n".join(lines).strip()


def _tag_suggestions(
    *,
    title: str,
    prompt_value: str,
    scene_headings: list[str],
    asset_keywords: list[str],
) -> list[str]:
    candidates: list[str] = []
    candidates.extend(asset_keywords[:8])
    if title.strip():
        candidates.append(title.strip())
    if prompt_value.strip():
        candidates.append(prompt_value.strip())
    candidates.extend(scene_headings[:6])
    return _dedupe_strings(candidate for candidate in candidates if str(candidate).strip())[:12]


def _default_thumbnail_path(package: VideoPackagePaths) -> Path | None:
    candidates = [
        package.package_dir / "thumbnail_yt.png",
        package.package_dir / "thumbnail_yt.jpg",
        package.package_dir / "thumbnail_yt.jpeg",
        package.publish_dir / "thumbnail_yt.png",
        package.publish_dir / "thumbnail_yt.jpg",
        package.publish_dir / "thumbnail_yt.jpeg",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
