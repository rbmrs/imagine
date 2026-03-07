from __future__ import annotations

import curses
import curses.textpad as textpad
import io
import datetime as dt
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
import urllib.request
import warnings
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Literal, TextIO, TypeVar, cast, overload

from .cli import _apply_default_brand_bookends
from .models import PipelineConfig
from .pipeline import VideoPipeline


@dataclass
class TuiConfig:
    prompt: str
    asset_keywords: list[str]
    project_dir: Path
    minutes: int
    fast_mode: bool
    tts_engine: str
    piper_voice_id: str
    piper_speaker_id: int | None
    voice_profile: str
    voice_speed: float
    melo_language: str
    melo_speaker: str


T = TypeVar("T")


class SpinnerCancelled(RuntimeError):
    pass


class LocalVideoMvpTui:
    SPINNER_FRAMES = ["-", "\\", "|", "/"]
    STOCK_ENV_KEYS = ("PEXELS_API_KEY", "PIXABAY_API_KEY")
    MELO_LANGUAGE_CHOICES = ("EN",)
    VOICE_PROFILE_CHOICES = ("calm-documentary", "balanced", "energetic-explainer")
    STAGE_LINE_RE = re.compile(r"\[local-video-mvp\]\s+Stage\s+(\d+(?:\.\d+)?)/(\d+):\s+(.+)")
    STAGE_COMPLETE_RE = re.compile(r"\[local-video-mvp\]\s+([A-Za-z0-9_]+)\s+completed in\s+([0-9]+(?:\.[0-9]+)?)s")
    ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    DEBUG_VOICE_TEST_PHRASE = (
        "Autonomous vehicles are reshaping mobility, safety, and city planning across the world."
    )
    DEBUG_PIPER_VOICES = (
        {
            "id": "en_US-libritts-high",
            "label": "[C] EN-US LibriTTS (high, speaker 000)",
            "speaker_id": 0,
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
            "license_note": "CC BY 4.0 (attribution required).",
        },
        {
            "id": "en_US-libritts-high",
            "label": "[C] EN-US LibriTTS (high, speaker 120)",
            "speaker_id": 120,
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
            "license_note": "CC BY 4.0 (attribution required).",
        },
        {
            "id": "en_US-libritts-high",
            "label": "[C] EN-US LibriTTS (high, speaker 360)",
            "speaker_id": 360,
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
            "license_note": "CC BY 4.0 (attribution required).",
        },
        {
            "id": "en_US-libritts-high",
            "label": "[C] EN-US LibriTTS (high, speaker 700)",
            "speaker_id": 700,
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/libritts/high/en_US-libritts-high.onnx.json?download=true",
            "license_note": "CC BY 4.0 (attribution required).",
        },
        {
            "id": "en_US-ljspeech-high",
            "label": "[C] EN-US LJSpeech (high)",
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high/en_US-ljspeech-high.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high/en_US-ljspeech-high.onnx.json?download=true",
            "license_note": "Public-domain lineage (verify model card).",
        },
        {
            "id": "en_US-joe-medium",
            "label": "[C] EN-US Joe (medium)",
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx.json?download=true",
            "license_note": "Commercial-friendly baseline (verify model card).",
        },
        {
            "id": "en_US-john-medium",
            "label": "[C] EN-US John (medium)",
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/john/medium/en_US-john-medium.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/john/medium/en_US-john-medium.onnx.json?download=true",
            "license_note": "Commercial-friendly baseline (verify model card).",
        },
        {
            "id": "en_US-norman-medium",
            "label": "[C] EN-US Norman (medium)",
            "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/norman/medium/en_US-norman-medium.onnx?download=true",
            "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/norman/medium/en_US-norman-medium.onnx.json?download=true",
            "license_note": "Commercial-friendly baseline (verify model card).",
        },
    )

    def __init__(self, config: TuiConfig) -> None:
        self.config = config
        self.config.asset_keywords = self._normalize_asset_keywords(self.config.asset_keywords)
        self.config.project_dir = self.config.project_dir.expanduser().resolve()
        self.config.project_dir.mkdir(parents=True, exist_ok=True)

        self._stdscr: Any = None
        self._repo_root = Path(__file__).resolve().parents[2]

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
        self._pending_scene_review_prompt: dict[str, Any] | None = None
        self._hitl_stage = "draft"
        self._hitl_enabled = True

        self._stock_api_keys: dict[str, str] = {}
        self._stock_key_sources: dict[str, str] = {}
        self._stock_key_warnings: list[str] = []

        self._worker: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None
        self._mpv_supported_vos: set[str] | None = None
        self._mpv_vo_probe_attempted = False
        self._mpv_input_conf_path: Path | None = None

        self._started_ollama = False
        self._ollama_process: subprocess.Popen[str] | None = None
        self._ollama_log_handle: TextIO | None = None

        self._session_log_handle: TextIO | None = None
        self._session_log_path, self._latest_log_path = self._init_session_log_paths()

        self._color_enabled = False
        self._color_pairs: dict[str, int] = {}

        self._open_session_log()
        self._load_persisted_settings()
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
                self._maybe_prompt_scene_review()
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
            self._color_pairs = {
                "title": 1,
                "ok": 2,
                "warn": 3,
                "error": 4,
                "accent": 5,
                "muted": 6,
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

        if char in {"s", "S"}:
            self._open_settings_menu()
            return

        if char in {"e", "E"}:
            self._edit_parameters()
            return

        if char in {"d", "D"}:
            self._open_debug_menu()
            return

        if char in {"c", "C"}:
            self._clean_projects()
            return

    def _start_run_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._refresh_stock_key_cache()
        if not self._passes_asset_hard_guard_precheck():
            return

        if not self._hitl_enabled:
            workspace = self._prepare_run_workspace()
            self._hitl_stage = "full"
            with self._lock:
                self._pending_unique_asset_prompt = None
                self._pending_clip_review_prompt = None
                self._pending_stage_transition_prompt = None
                self._pending_scene_review_prompt = None

            self._mark_command_start(workflow_kind="run")
            self._set_running(True)
            self._set_status(f"Starting full stage: {workspace.name}")
            self._worker = threading.Thread(target=self._run_workflow, daemon=True)
            self._worker.start()
            return

        if self._hitl_stage == "preview" and self._active_project_dir is not None:
            if self._has_pending_scene_reviews(self._active_project_dir):
                self._queue_scene_review_prompt(self._active_project_dir)
                self._set_status("Resuming scene-by-scene review.")
                return

        if self._active_project_dir is None or self._hitl_stage == "done":
            workspace = self._prepare_run_workspace()
            self._hitl_stage = "draft"
            with self._lock:
                self._pending_unique_asset_prompt = None
                self._pending_clip_review_prompt = None
                self._pending_stage_transition_prompt = None
                self._pending_scene_review_prompt = None
        else:
            workspace = self._active_project_dir

        if self._hitl_stage == "draft" and not self._ensure_ollama_available_with_modal():
            return

        self._mark_command_start(workflow_kind="run")
        self._set_running(True)
        self._set_status(f"Starting {self._hitl_stage} stage: {workspace.name}")
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

            if stage == "draft":
                self._ensure_ollama_running()

            run_code = self._run_and_stream(self._build_run_command(workflow_stage=stage), label="run")

            if run_code != 0:
                queued_unique_prompt = self._queue_unique_asset_prompt_from_run_report()
                if queued_unique_prompt:
                    self._set_status("Run paused: broaden asset keywords to unlock more unique clips.")
                else:
                    self._set_status(f"{stage.title()} stage failed with exit code {run_code}.")
                return

            if stage == "draft":
                if self._active_project_dir is not None:
                    self._hitl_stage = "preview"
                    self._queue_scene_review_prompt(self._active_project_dir)
                self._set_status("Draft complete. Scene review is ready.")
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
                if self._active_project_dir is not None and self._has_pending_scene_reviews(self._active_project_dir):
                    self._queue_scene_review_prompt(self._active_project_dir)
                    if inspect_code == 0:
                        self._set_status("Preview complete. Scene review still has pending approvals.")
                    else:
                        self._set_status(f"Preview complete, inspect failed with exit code {inspect_code}.")
                    return

                self._queue_stage_transition_prompt(
                    next_stage="finalize",
                    title="Preview Ready",
                    body="Preview is ready. Choose preview actions before finalizing.",
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
                exported_mp4 = self._export_final_mp4_to_downloads()
                self._hitl_stage = "done"

                if inspect_code == 0 and exported_mp4 is not None:
                    self._set_status(f"Finalize complete. MP4 exported to {exported_mp4.name}.")
                elif inspect_code == 0:
                    self._set_status("Finalize complete. MP4 export skipped.")
                else:
                    self._set_status(f"Finalize succeeded, inspect failed with exit code {inspect_code}.")
                return

            if stage == "full":
                self._set_status("Full run complete. Inspecting outputs...")
                inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
                exported_mp4 = self._export_final_mp4_to_downloads()
                self._hitl_stage = "done"

                if inspect_code == 0 and exported_mp4 is not None:
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
            "--workflow-stage",
            stage,
            "--project-dir",
            str(project_dir),
            "--minutes",
            str(self.config.minutes),
            "--tts-engine",
            self.config.tts_engine,
            "--voice-profile",
            self.config.voice_profile,
            "--voice-speed",
            f"{self.config.voice_speed:.2f}",
            "--caption-style",
            "engagement",
            "--strict-commercial-safe",
            "--verbose",
        ]

        if self.config.fast_mode:
            command.append("--fast-mode")
        else:
            command.extend(
                [
                    "--resolution",
                    "1280x720",
                    "--video-effects",
                    "subtle-motion",
                    "--include-intro",
                    "--include-outro",
                    "--bookend-style",
                    "minimal-clean",
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
            if stage == "draft":
                command.append("--prepare-scene-review")
        else:
            command.extend(["--script-engine", "template"])

        if self.config.asset_keywords:
            command.extend(["--asset-keywords", ", ".join(self.config.asset_keywords)])

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

    def _ensure_ollama_running(self) -> None:
        if shutil.which("ollama") is None:
            self._append_log("WARN: `ollama` not found in PATH; run may fail with --require-ollama.")
            return

        if shutil.which("pgrep") is not None:
            check = subprocess.run(["pgrep", "-f", "ollama serve"], capture_output=True, text=True, check=False)
            if check.returncode == 0:
                self._append_log("Ollama server already running.")
                return

        log_path = Path("/tmp/local-video-mvp-ollama.log")
        log_handle = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(["ollama", "serve"], stdout=log_handle, stderr=subprocess.STDOUT, text=True)

        self._ollama_log_handle = log_handle
        self._ollama_process = process
        self._started_ollama = True

        self._append_log(f"Started Ollama server (pid={process.pid}).")
        self._append_log(f"Ollama log: {log_path}")
        time.sleep(2.0)

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
        self._append_log("WARN: Ollama unavailable. Draft stage was blocked before run start.")
        self._set_status("Ollama unavailable. Start `ollama serve` and retry.")
        return False

    def _draw(self) -> None:
        if self._stdscr is None:
            return

        stdscr = self._stdscr
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        if width < 50 or height < 20:
            self._draw_compact(height, width)
            stdscr.refresh()
            return

        spinner = self._spinner_frame() if self._is_running() else "*"
        title = f" Imagine TUI {spinner} "
        self._safe_addstr(0, 0, title, width, attr=self._attr("title", bold=True))

        self._draw_hotkey_hint(row=1, width=width)
        self._safe_hline(2, width)

        self._draw_box(3, 0, 8, width, title=" Configuration ", attr=self._attr("accent"))
        self._safe_addstr(4, 2, f"Prompt : {self._trim_tail(self.config.prompt, width - 14)}", width)
        self._safe_addstr(5, 2, f"Minutes: {self.config.minutes}", width)
        self._safe_addstr(6, 2, f"Fast   : {'On' if self.config.fast_mode else 'Off'}", width)
        self._safe_addstr(
            7,
            2,
            f"Voice  : {self._voice_display_value()}  "
            f"profile={self.config.voice_profile} speed={self.config.voice_speed:.2f}",
            width,
        )
        self._safe_addstr(
            8,
            2,
            f"MP4 out: {self._trim_middle(str(self._mp4_output_preview_path()), width - 13)}",
            width,
            attr=self._attr("muted"),
        )
        keywords_text = ", ".join(self.config.asset_keywords) if self.config.asset_keywords else "(auto from script scenes)"
        self._safe_addstr(9, 2, f"Keywords: {self._trim_tail(keywords_text, width - 14)}", width, attr=self._attr("muted"))

        self._draw_box(11, 0, 6, width, title=" Runtime ", attr=self._attr("accent"))
        state_text, state_attr = self._state_display()
        self._safe_addstr(12, 2, f"State  : {state_text}", width, attr=state_attr)
        self._safe_addstr(13, 2, f"Phase  : {self._trim_tail(self._progress_phase_text(), width - 14)}", width)
        assets_text, assets_attr = self._asset_status_display()
        self._safe_addstr(14, 2, f"Assets : {self._trim_tail(assets_text, width - 14)}", width, attr=assets_attr)
        self._safe_addstr(15, 2, f"Status : {self._trim_tail(self._get_status(), width - 14)}", width)

        logs_top = 17
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
        state_text, state_attr = self._state_display()
        self._safe_addstr(9, 0, f"{state_text}", width, attr=state_attr)
        self._safe_addstr(10, 0, self._trim_tail(self._progress_phase_text(), width), width)
        assets_text, assets_attr = self._asset_status_display()
        self._safe_addstr(11, 0, self._trim_tail(assets_text, width), width, attr=assets_attr)
        self._safe_addstr(12, 0, self._trim_tail(self._get_status(), width), width)

        logs = self._get_logs()
        log_rows = max(1, height - 14)
        if self._is_modal_active():
            self._safe_addstr(14, 0, self._trim_tail("Modal focus active. Live logs hidden.", width), width, attr=self._attr("muted"))
        else:
            for idx, line in enumerate(logs[-log_rows:]):
                self._safe_addstr(14 + idx, 0, self._trim_tail(line, width), width, attr=self._log_line_attr(line))

    def _voice_display_value(self) -> str:
        engine = str(self.config.tts_engine or "melo").strip().lower()
        if engine == "piper":
            voice_meta = self._selected_piper_voice_meta()
            label = str(voice_meta.get("label") or voice_meta.get("id") or "piper").strip()
            return f"piper/{label}"
        return f"{self.config.melo_language}/{self.config.melo_speaker}"

    def _state_display(self) -> tuple[str, int]:
        running = self._is_running()
        elapsed = self._elapsed_seconds()

        if running:
            frame = self._spinner_frame()
            progress = self._estimate_progress_percent(elapsed)
            eta_seconds = self._estimate_eta_seconds(elapsed, progress)
            eta_text = self._format_eta(eta_seconds)
            return f"{frame} RUNNING  {elapsed:.1f}s  {progress:5.1f}% ETA {eta_text}", self._attr("ok", bold=True)

        if self._last_elapsed_seconds is not None:
            return f"IDLE  last run {self._last_elapsed_seconds:.1f}s", self._attr("muted")

        return "IDLE", self._attr("muted")

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
            return f"HITL ready: {self._hitl_stage}"

        if stage_index is None or stage_total is None or stage_total <= 0:
            return f"Starting {self._hitl_stage} stage..."

        if float(stage_index).is_integer():
            stage_idx_text = str(int(stage_index))
        else:
            stage_idx_text = f"{stage_index:.1f}"

        if stage_label:
            return f"Stage {stage_idx_text}/{stage_total}: {stage_label}"
        return f"Stage {stage_idx_text}/{stage_total}"

    def _asset_status_display(self) -> tuple[str, int]:
        with self._lock:
            has_pexels = bool(self._stock_api_keys.get("PEXELS_API_KEY"))
            has_pixabay = bool(self._stock_api_keys.get("PIXABAY_API_KEY"))

        if has_pexels and has_pixabay:
            return "stock ready (pexels + pixabay)", self._attr("ok", bold=True)
        if has_pexels:
            return "stock limited (pexels only)", self._attr("warn", bold=True)
        if has_pixabay:
            return "stock limited (pixabay only)", self._attr("warn", bold=True)
        return "placeholders only (no stock keys)", self._attr("warn", bold=True)

    def _asset_preflight_message(self) -> str:
        with self._lock:
            has_pexels = bool(self._stock_api_keys.get("PEXELS_API_KEY"))
            has_pixabay = bool(self._stock_api_keys.get("PIXABAY_API_KEY"))

        if has_pexels and has_pixabay:
            return "Preflight: stock keys detected (Pexels + Pixabay). Placeholders only if searches/downloads fail."
        if has_pexels:
            return "Preflight: only Pexels key detected. Some scenes may still use placeholders."
        if has_pixabay:
            return "Preflight: only Pixabay key detected. Some scenes may still use placeholders."
        return "Preflight: no stock API keys detected (PEXELS_API_KEY / PIXABAY_API_KEY). Hard guard blocks this run."

    def _passes_asset_hard_guard_precheck(self) -> bool:
        with self._lock:
            has_pexels = bool(self._stock_api_keys.get("PEXELS_API_KEY"))
            has_pixabay = bool(self._stock_api_keys.get("PIXABAY_API_KEY"))

        if has_pexels or has_pixabay:
            return True

        self._append_log("ERROR: Hard guard blocked run: no stock API keys were found.")
        self._append_log(
            "Set PEXELS_API_KEY and/or PIXABAY_API_KEY in env, repo .env, or ~/.config/imagine/stock_api_keys.json"
        )
        self._set_status("Hard guard: missing stock API keys. Run blocked.")
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
        return out[:8]

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

    def _latest_run_report_path(self) -> Path | None:
        for workspace in self._iter_project_workspaces():
            report_path = workspace / "run_report.json"
            if report_path.exists():
                return report_path
        return None

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

    def _mp4_output_preview_path(self) -> Path:
        if self._pending_export_path is not None:
            return self._pending_export_path

        base_slug = self._slugify(self.config.prompt)
        return (Path.home() / "Downloads" / f"{base_slug}-<timestamp>.mp4").resolve()

    def _export_final_mp4_to_downloads(self) -> Path | None:
        project_dir = self._active_project_dir
        if project_dir is None:
            self._append_log("WARN: No active workspace found for MP4 export.")
            return None

        source_mp4 = project_dir / "output" / "final.mp4"
        if not source_mp4.exists():
            self._append_log(f"WARN: final.mp4 not found at {source_mp4}")
            return None

        target_mp4 = self._downloads_export_mp4_path(project_dir)
        target_mp4.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_mp4, target_mp4)

        self._pending_export_path = target_mp4
        self._append_log(f"Exported final MP4 to {target_mp4}")
        return target_mp4

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

    def _queue_scene_review_prompt(self, project_dir: Path) -> None:
        with self._lock:
            self._pending_scene_review_prompt = {
                "project_dir": str(project_dir),
            }

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

            narration_start = max(0.0, preview_start - intro_shift)
            narration_end = max(narration_start + 0.1, preview_end - intro_shift)

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

        state = self._load_scene_review_state(project_dir)
        for entry in entries:
            scene_id = str(entry.get("scene_id") or "").strip()
            if scene_id:
                self._scene_review_record(state, scene_id)

        self._save_scene_review_state(project_dir, state)
        with self._modal_focus():
            outcome = self._run_scene_review_hub(project_dir, state)
        self._save_scene_review_state(project_dir, state)

        if outcome == "ready":
            self._queue_stage_transition_prompt(
                next_stage="preview",
                title="Scene Review Complete",
                body="All scenes are approved. Generate preview now?",
            )
            self._set_status("All scenes approved. Preview checkpoint pending.")
            return

        if outcome == "force_preview":
            self._hitl_stage = "preview"
            self._mark_command_start(workflow_kind="run")
            self._set_running(True)
            workspace_name = project_dir.name
            self._set_status(f"Starting preview stage: {workspace_name}")
            self._worker = threading.Thread(target=self._run_workflow, daemon=True)
            self._worker.start()
            return

        self._set_status("Scene review closed. Press R to resume.")

    def _run_scene_review_hub(self, project_dir: Path, state: dict[str, dict[str, Any]]) -> str:
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
                footer = "Up/Down move | Enter edit | G generate | Esc back"
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
                        title="Generate Preview",
                        body="Approve all pending scenes and generate preview now?",
                        default_yes=True,
                    )
                    if not confirmed:
                        break

                    approved_count = self._approve_all_pending_scene_reviews(state, entries)
                    self._save_scene_review_state(project_dir, state)
                    self._append_log(f"Auto-approved {approved_count} pending scene(s) from review hub.")
                    return "force_preview"
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

    def _update_scene_voiceover(self, project_dir: Path, *, scene_id: str, new_voiceover: str) -> None:
        approved_path = project_dir / "review" / "script_approved.json"
        script_path = project_dir / "script.json"
        source_path = approved_path if approved_path.exists() else script_path
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
        approved_path.parent.mkdir(parents=True, exist_ok=True)
        approved_path.write_text(serialized, encoding="utf-8")
        script_path.write_text(serialized, encoding="utf-8")

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
            help_text = "Ctrl-G save+approve | Esc back"
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
        config = PipelineConfig(
            prompt=self.config.prompt,
            project_dir=project_dir,
            asset_keywords=list(self.config.asset_keywords),
            minutes=max(1, int(self.config.minutes)),
            width=1280,
            height=720,
            fps=30,
            script_engine="template",
            ollama_model="qwen2.5:14b",
            require_ollama=False,
            tts_engine=self.config.tts_engine,
            piper_voice_id=(self.config.piper_voice_id or None),
            piper_speaker_id=self.config.piper_speaker_id,
            caption_engine="heuristic",
            caption_style="engagement",
            burn_subtitles=True,
            strict_commercial_safe=True,
            pexels_api_key=self._stock_api_keys.get("PEXELS_API_KEY"),
            pixabay_api_key=self._stock_api_keys.get("PIXABAY_API_KEY"),
            require_external_assets=True,
            video_effects="subtle-motion",
            include_intro=True,
            include_outro=True,
            intro_seconds=2.8,
            outro_seconds=3.0,
            bookend_style="minimal-clean",
            voice_profile=self.config.voice_profile,
            voice_speed=self.config.voice_speed,
            melo_language=self.config.melo_language,
            melo_speaker=self.config.melo_speaker,
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
                    timeout_seconds=240,
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
            should_continue = self._prompt_preview_ready_actions()
        else:
            should_continue = self._prompt_yes_no(title=title, body=body, default_yes=True)

        self._hitl_stage = next_stage
        if should_continue:
            self._start_run_workflow()
            return

        self._set_status(f"{next_stage.title()} stage ready. Press R when you are ready.")

    def _prompt_preview_ready_actions(self) -> bool:
        project_dir = self._active_project_dir
        if project_dir is None:
            self._set_status("Preview complete. Workspace not found. Press R to finalize.")
            return False

        preview_path = project_dir / "review" / "preview.mp4"
        self._append_log(f"Preview artifact: {preview_path}")
        self._set_status(f"Preview ready: {preview_path}")

        actions = ["Play preview in terminal", "Finalize full output now"]
        current_action = actions[0]

        with self._modal_focus():
            while True:
                choice = self._select_from_list(
                    label=f"Preview Actions ({preview_path.name})",
                    options=actions,
                    current_value=current_action,
                )
                if choice is None:
                    return False
                if choice == "Finalize full output now":
                    return True

                played = self._play_project_preview(project_dir)
                current_action = "Play preview in terminal"
                if played:
                    self._set_status("Preview playback finished. Finalize when ready.")
                else:
                    self._set_status("Preview playback failed in terminal. Install/use mpv with terminal video backend.")

    def _open_debug_menu(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        options = [
            "Test terminal video playback",
            "Test voices",
        ]
        current_option = options[0]

        with self._modal_focus():
            while True:
                choice = self._select_from_list(
                    label="Debug",
                    options=options,
                    current_value=current_option,
                )
                if choice is None:
                    self._set_status("Debug menu closed.")
                    return

                choice_value = cast(str, choice)
                current_option = choice_value
                if choice_value == "Test terminal video playback":
                    self._run_debug_terminal_video_playback_test()
                    continue
                if choice_value == "Test voices":
                    self._run_debug_voice_speaker_test()
                    continue

    def _open_settings_menu(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        with self._modal_focus():
            while True:
                options = [
                    f"HITL: {'On' if self._hitl_enabled else 'Off'}",
                    f"Fast mode: {'On' if self.config.fast_mode else 'Off'}",
                ]
                choice = self._select_from_list(
                    label="Settings",
                    options=options,
                    current_value=options[0],
                )
                if choice is None:
                    self._set_status("Settings closed.")
                    return

                if choice.startswith("HITL:"):
                    self._hitl_enabled = not self._hitl_enabled
                    mode = "On" if self._hitl_enabled else "Off"
                    if not self._hitl_enabled:
                        with self._lock:
                            self._pending_unique_asset_prompt = None
                            self._pending_clip_review_prompt = None
                            self._pending_stage_transition_prompt = None
                            self._pending_scene_review_prompt = None
                    self._save_persisted_settings()
                    self._set_status(f"Settings updated: HITL {mode}.")
                    self._append_log(f"Settings: HITL set to {mode}.")
                    continue

                if choice.startswith("Fast mode:"):
                    self.config.fast_mode = not self.config.fast_mode
                    mode = "On" if self.config.fast_mode else "Off"
                    self._save_persisted_settings()
                    self._set_status(f"Settings updated: fast mode {mode}.")
                    self._append_log(
                        "Settings: fast mode set to "
                        f"{mode} (shorter run, lower resolution, shorter bookends, burned subtitles kept on)."
                    )
                    continue

    def _run_debug_voice_speaker_test(self) -> None:
        project_dir = self._resolve_debug_voice_project_dir()
        melo_speakers: list[str] = []
        try:
            available = self._run_with_spinner_modal(
                title="Debug Voices",
                message=f"Loading speakers ({self.config.melo_language})",
                task=lambda: self._load_melo_speakers(self.config.melo_language),
            )
            if available:
                melo_speakers = [str(item).strip() for item in available if str(item).strip()]
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: Debug voices failed while loading speakers: {exc}")
            self._set_status("Debug voices: failed loading Melo speakers; Piper voices are still available.")

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

        if not entries:
            self._set_status("Debug voices: no selectable voices available.")
            return

        labels = [str(item.get("label") or "").strip() for item in entries if str(item.get("label") or "").strip()]
        if not labels:
            self._set_status("Debug voices: no selectable voices available.")
            return

        label_to_entry = {str(item.get("label") or "").strip(): item for item in entries}
        current_label = labels[0]
        for item in entries:
            if str(item.get("engine") or "") == "melo" and str(item.get("speaker") or "") == self.config.melo_speaker:
                current_label = str(item.get("label") or current_label)
                break

        phrase = self.DEBUG_VOICE_TEST_PHRASE
        while True:
            selected_label = self._select_from_list(
                label="Debug voices",
                options=labels,
                current_value=current_label,
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

            self._preview_debug_voice_entry(project_dir, selected_value, selected_entry, phrase)
            continue

    def _preview_debug_voice_entry(
        self,
        project_dir: Path,
        selected_label: str,
        selected_entry: dict[str, Any],
        phrase: str,
    ) -> None:
        engine = str(selected_entry.get("engine") or "").strip().lower()
        if engine == "melo":
            speaker_id = str(selected_entry.get("speaker") or "").strip()
            if not speaker_id:
                self._set_status("Debug voices: selected Melo speaker is invalid.")
                return

            try:
                sample_path = self._run_with_spinner_modal(
                    title="Debug Voices",
                    message=f"Generating sample for {selected_label}",
                    task=lambda: self._generate_debug_voice_sample(
                        project_dir=project_dir,
                        speaker=speaker_id,
                        phrase=phrase,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERROR: Debug Melo voice generation failed: {exc}")
                self._set_status("Debug voices: Melo generation failed. See logs.")
                return

            if sample_path is None or not sample_path.exists():
                self._set_status("Debug voices: generated Melo audio file is missing.")
                return

            sample_file = cast(Path, sample_path)
            playback_cancel = threading.Event()
            self._set_status(f"Debug voices: playing {selected_label}.")
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
                self._set_status("Debug voices: preview cancelled. Pick another voice or Esc to exit.")
                return

            if played:
                self._set_status("Debug voices: playback complete. Pick another voice or Esc to exit.")
            else:
                self._set_status("Debug voices: playback failed. Pick another voice or Esc to exit.")
            return

        if engine == "piper":
            piper_cmd = self._resolve_piper_command()
            if not piper_cmd:
                self._show_piper_not_installed_modal()
                return

            self._append_log(f"Debug Piper runtime: {shlex.join(piper_cmd)}")
            voice_meta = cast(dict[str, Any], selected_entry.get("voice_meta") or {})
            voice_id = str(voice_meta.get("id") or "").strip()
            license_note = str(voice_meta.get("license_note") or "").strip()
            self._append_log(f"Debug Piper voice selected: {selected_label} ({voice_id})")
            if license_note:
                self._append_log(f"Debug Piper license note: {license_note}")

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
                self._append_log(f"ERROR: Piper debug voice generation failed: {exc}")
                self._set_status("Debug voices: Piper generation failed. See logs.")
                return

            if sample_path is None or not sample_path.exists():
                self._set_status("Debug voices: generated Piper audio file is missing.")
                return

            sample_file = cast(Path, sample_path)
            playback_cancel = threading.Event()
            self._set_status(f"Debug voices: playing {selected_label}.")
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
                self._set_status("Debug voices: preview cancelled. Pick another voice or Esc to exit.")
                return

            if played:
                self._set_status("Debug voices: playback complete. Pick another voice or Esc to exit.")
            else:
                self._set_status("Debug voices: playback failed. Pick another voice or Esc to exit.")
            return

        self._set_status("Debug voices: selected engine is unsupported.")

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

        by_label = {str(item.get("label") or ""): item for item in entries if str(item.get("label") or "")}
        current_label = ""
        if self.config.tts_engine == "piper":
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
                    current_label = str(item.get("label") or "")
                    break
        else:
            for item in entries:
                if str(item.get("engine") or "") == "melo" and str(item.get("speaker") or "") == self.config.melo_speaker:
                    current_label = str(item.get("label") or "")
                    break

        if not current_label and entries:
            current_label = str(entries[0].get("label") or "")

        return entries, by_label, current_label, had_warning

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

    def _resolve_debug_voice_project_dir(self) -> Path:
        if self._active_project_dir is not None:
            return self._active_project_dir

        latest = self._latest_project_workspace()
        if latest is not None:
            return latest

        root = self.config.project_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _run_debug_terminal_video_playback_test(self) -> None:
        resolved = self._resolve_debug_playback_media()
        if resolved is None:
            self._set_status(
                "Debug playback: media not found. Add playback-test.mp4 to your project dir or run preview first."
            )
            return

        project_dir, selected_path = resolved

        workspace_name = project_dir.name if project_dir.is_dir() else str(project_dir)

        mpv_bin = self._resolve_mpv_binary()
        if mpv_bin is None:
            self._append_log(
                "WARN: Debug playback requires mpv. Install with `brew install mpv` "
                "or ensure mpv is in PATH."
            )
            self._set_status("Debug playback: mpv not found. Install with `brew install mpv`.")
            return

        backends = self._resolved_video_backends()
        if not backends:
            supported = self._mpv_supported_terminal_vos()
            supported_text = ", ".join(sorted(supported)) if supported else "(none)"
            candidate_text = ", ".join(self._video_backend_candidates()) or "(none)"
            self._append_log(
                "WARN: Debug playback found no compatible terminal video backend "
                f"(candidates: {candidate_text}; mpv supported: {supported_text})."
            )
            self._set_status("Debug playback: no compatible terminal backend (kitty/sixel/tct).")
            return

        self._append_log(
            f"Debug playback preflight: project={workspace_name}, media={selected_path.name}, mpv={mpv_bin}, "
            f"backends={', '.join(backends)}"
        )
        self._set_status(f"Debug playback: testing {selected_path.name} via {backends[0]}...")

        played = self._play_media_path(
            selected_path,
            label=f"debug-playback-{project_dir.name}",
            audio_only=False,
        )
        if played:
            self._set_status(f"Debug playback passed: terminal video worked for {selected_path.name}.")
            return

        self._set_status(f"Debug playback failed: terminal backend did not play {selected_path.name}.")

    def _resolve_debug_playback_media(self) -> tuple[Path, Path] | None:
        candidate_roots = self._debug_candidate_roots()
        for root in candidate_roots:
            custom_path = root / "playback-test.mp4"
            if custom_path.exists():
                return root, custom_path

        for root in candidate_roots:
            preview_path = root / "review" / "preview.mp4"
            if preview_path.exists():
                return root, preview_path

        checked: list[str] = []
        for root in candidate_roots:
            checked.append(str(root / "playback-test.mp4"))
            checked.append(str(root / "review" / "preview.mp4"))
        if checked:
            self._append_log(f"WARN: Debug playback media not found in: {' | '.join(checked)}")
        return None

    def _debug_candidate_roots(self) -> list[Path]:
        candidates: list[Path] = []

        def add(path: Path | None) -> None:
            if path is None:
                return
            resolved = path.expanduser().resolve()
            if not resolved.exists() or not resolved.is_dir():
                return
            if resolved in candidates:
                return
            candidates.append(resolved)

        add(self._active_project_dir)
        add(self.config.project_dir)
        add(self._latest_project_workspace())
        add(self._repo_root)
        return candidates

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
            modal_height = 7 if not detail_text else 10
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
                footer = "Esc cancel" if allow_cancel else "Please wait"
                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(2, 2, body_text)
                    if detail_text:
                        wrapped = textwrap.wrap(str(detail_text), width=max(20, modal_width - 6))
                        for index, line in enumerate(wrapped[:3]):
                            win.addstr(4 + index, 2, self._trim_tail(line, modal_width - 4), self._attr("muted"))
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

    def _select_multiple_clips(self, entries: list[dict[str, str]]) -> list[int] | None:
        if self._stdscr is None:
            return None
        if not entries:
            return []

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
                exported_mp4 = self._export_final_mp4_to_downloads()

                if inspect_code == 0 and exported_mp4 is not None:
                    self._set_status(f"Replacement complete. MP4 exported to {exported_mp4.name}.")
                elif inspect_code == 0:
                    self._set_status("Replacement complete. MP4 export skipped.")
                else:
                    self._set_status(f"Replacement succeeded, inspect failed with exit code {inspect_code}.")

                if self._active_project_dir is not None:
                    clip_catalog = self._active_project_dir / "review" / "clip_catalog.json"
                    if clip_catalog.exists():
                        self._append_log(f"Clip catalog: {clip_catalog}")
                        self._queue_scene_review_prompt(self._active_project_dir)
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

    def _edit_parameters(self) -> None:
        if self._is_running():
            self._set_status("Cannot edit while a run is in progress.")
            return

        before = (
            self.config.prompt,
            tuple(self.config.asset_keywords),
            self.config.minutes,
            self.config.tts_engine,
            self.config.piper_voice_id,
            self.config.piper_speaker_id,
            self.config.melo_language,
            self.config.melo_speaker,
            self.config.voice_profile,
            round(float(self.config.voice_speed), 3),
        )
        had_warning = False
        self._set_status("Edit mode: Enter applies/keeps, Esc goes back one step.")

        def _escape_result(value: Any) -> tuple[str | None, bool]:
            if isinstance(value, tuple) and len(value) == 2:
                raw_value, raw_escaped = value
                parsed_value = raw_value if isinstance(raw_value, str) else None
                return parsed_value, bool(raw_escaped)
            if isinstance(value, str):
                return value, False
            return None, False

        steps = ["prompt", "keywords", "minutes", "voice", "profile", "speed"]
        step_index = 0
        while 0 <= step_index < len(steps):
            step = steps[step_index]

            if step == "prompt":
                prompt_result = _escape_result(
                    self._prompt_input("Prompt", self.config.prompt, return_escaped=True)
                )
                prompt_value, escaped = prompt_result
                if escaped:
                    if step_index == 0:
                        break
                    step_index -= 1
                    continue

                if prompt_value is not None:
                    candidate = prompt_value.strip()
                    if candidate:
                        self.config.prompt = candidate
                    else:
                        self._append_log("WARN: Prompt cannot be empty. Keeping previous value.")
                        had_warning = True

                step_index += 1
                continue

            if step == "keywords":
                keyword_default = ", ".join(self.config.asset_keywords)
                keyword_result = _escape_result(
                    self._prompt_input(
                        "Asset keywords (comma-separated)",
                        keyword_default,
                        return_escaped=True,
                    )
                )
                keyword_value, escaped = keyword_result
                if escaped:
                    step_index -= 1
                    continue

                if keyword_value is not None:
                    self.config.asset_keywords = self._normalize_asset_keywords(
                        [part.strip() for part in re.split(r"[,;\n]+", keyword_value) if part.strip()]
                    )

                step_index += 1
                continue

            if step == "minutes":
                minutes_result = _escape_result(
                    self._prompt_input("Minutes", str(self.config.minutes), return_escaped=True)
                )
                minutes_value, escaped = minutes_result
                if escaped:
                    step_index -= 1
                    continue

                if minutes_value is not None:
                    try:
                        minutes = int(minutes_value)
                        if minutes <= 0:
                            raise ValueError
                        self.config.minutes = minutes
                    except ValueError:
                        self._append_log("WARN: Minutes must be a positive integer. Keeping previous value.")
                        had_warning = True

                step_index += 1
                continue

            if step == "voice":
                try:
                    voice_entries, entry_by_label, current_label, load_warning = self._run_with_spinner_modal(
                        title="Voice",
                        message="Loading voices",
                        task=self._combined_voice_entries_for_selection,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"WARN: Voice picker failed while loading voices: {exc}")
                    self._set_status("Voice picker unavailable right now. Keeping previous voice.")
                    had_warning = True
                    step_index += 1
                    continue

                if load_warning:
                    had_warning = True
                if not voice_entries:
                    self._append_log("WARN: Could not load any selectable voices. Keeping previous voice.")
                    had_warning = True
                    step_index += 1
                    continue

                voice_result = _escape_result(
                    self._select_from_list(
                        label="Voice",
                        options=[item["label"] for item in voice_entries],
                        current_value=current_label,
                        return_escaped=True,
                    )
                )
                voice_value, escaped = voice_result
                if escaped:
                    step_index -= 1
                    continue

                if voice_value is not None:
                    selected_entry = entry_by_label.get(voice_value)
                    if selected_entry is not None:
                        engine = str(selected_entry.get("engine") or "").strip().lower()
                        if engine == "melo":
                            self.config.tts_engine = "melo"
                            self.config.melo_language = "EN"
                            self.config.melo_speaker = str(selected_entry.get("speaker") or self.config.melo_speaker)
                        elif engine == "piper":
                            voice_meta = selected_entry.get("voice_meta")
                            if isinstance(voice_meta, dict):
                                self.config.tts_engine = "piper"
                                self.config.piper_voice_id = str(voice_meta.get("id") or "").strip()
                                speaker_id = voice_meta.get("speaker_id")
                                self.config.piper_speaker_id = int(speaker_id) if speaker_id is not None else None

                step_index += 1
                continue

            if step == "profile":
                profile_result = _escape_result(
                    self._select_from_list(
                        label="Voice profile",
                        options=list(self.VOICE_PROFILE_CHOICES),
                        current_value=self.config.voice_profile,
                        return_escaped=True,
                    )
                )
                profile_value, escaped = profile_result
                if escaped:
                    step_index -= 1
                    continue

                if profile_value is not None:
                    self.config.voice_profile = profile_value

                step_index += 1
                continue

            if step == "speed":
                speed_result = _escape_result(
                    self._prompt_input(
                        "Voice speed (0.5-2.0)",
                        f"{self.config.voice_speed:.2f}",
                        return_escaped=True,
                    )
                )
                speed_value, escaped = speed_result
                if escaped:
                    step_index -= 1
                    continue

                if speed_value is not None:
                    try:
                        speed = float(speed_value)
                        if speed < 0.5 or speed > 2.0:
                            raise ValueError
                        self.config.voice_speed = speed
                    except ValueError:
                        self._append_log("WARN: Voice speed must be between 0.5 and 2.0. Keeping previous value.")
                        had_warning = True

                step_index += 1
                continue

        self._refresh_prior_total_seconds()
        self._refresh_stock_key_cache()

        after = (
            self.config.prompt,
            tuple(self.config.asset_keywords),
            self.config.minutes,
            self.config.tts_engine,
            self.config.piper_voice_id,
            self.config.piper_speaker_id,
            self.config.melo_language,
            self.config.melo_speaker,
            self.config.voice_profile,
            round(float(self.config.voice_speed), 3),
        )
        changed = after != before

        if changed:
            self._save_persisted_settings()
            self._append_log(
                "Updated config: "
                f"minutes={self.config.minutes}, "
                f"keywords={','.join(self.config.asset_keywords) if self.config.asset_keywords else 'auto'}, "
                f"voice={self._voice_display_value()}, "
                f"profile={self.config.voice_profile}, speed={self.config.voice_speed:.2f}"
            )

        if had_warning:
            self._set_status("Parameters updated with warnings.")
        elif not changed:
            self._set_status("No parameter changes.")
        else:
            self._set_status("Parameters updated.")

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
            footer = "Space mark | Enter delete | Esc cancel"
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
            if key in (10, 13, curses.KEY_ENTER):
                return sorted(selected)
            if key == 27:
                return None

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

        stdscr = self._stdscr
        height, width = stdscr.getmaxyx()

        modal_width = min(max(58, len(label) + 18), max(22, width - 2))
        modal_height = 8
        if modal_width < 22 or height < modal_height + 1:
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
                current_line = self._trim_tail(f"Current: {current_value}", modal_width - 4)
                input_prefix = "New: "

                if user_input:
                    shown_input = self._trim_tail(user_input, modal_width - len(input_prefix) - 4)
                else:
                    shown_input = "(keep current)"

                help_text = "Enter apply | Esc back | Backspace delete"

                try:
                    win.addstr(0, 2, title_text, self._attr("accent", bold=True))
                    win.addstr(2, 2, current_line)
                    win.addstr(4, 2, input_prefix, self._attr("accent", bold=True))
                    win.addstr(4, 2 + len(input_prefix), shown_input)
                    win.addstr(modal_height - 1, 2, self._trim_tail(help_text, modal_width - 4), self._attr("muted"))
                    cursor_col = min(modal_width - 2, 2 + len(input_prefix) + len(shown_input))
                    win.move(4, max(2 + len(input_prefix), cursor_col))
                except curses.error:
                    pass

                win.refresh()
                key = win.getch()

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
                if 32 <= key <= 126 and len(user_input) < max_input_len:
                    user_input += chr(key)
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
        return_escaped: Literal[True],
    ) -> tuple[str | None, bool]:
        ...

    def _select_from_list(
        self,
        label: str,
        options: list[str],
        current_value: str,
        *,
        return_escaped: bool = False,
    ) -> str | None | tuple[str | None, bool]:
        if self._stdscr is None:
            return (None, True) if return_escaped else None

        normalized = [str(option).strip() for option in options if str(option).strip()]
        if not normalized:
            return (None, True) if return_escaped else None

        stdscr = self._stdscr
        height, width = stdscr.getmaxyx()

        max_option_len = max(len(item) for item in normalized)
        modal_width = min(max(42, max_option_len + 8), max(12, width - 2))
        max_modal_height = max(6, height - 2)
        max_list_rows = max(1, max_modal_height - 4)
        list_rows = min(len(normalized), max_list_rows)
        modal_height = max(6, list_rows + 4)

        if modal_width < 12 or modal_height < 6:
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
            help_text = "Enter select | Esc back"
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
                line = self._trim_tail(f"{prefix} {item}", modal_width - 2)
                attr = curses.A_REVERSE if option_index == selected else 0
                try:
                    win.addstr(1 + row, 1, line, attr)
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
        actions = ("Run", "Settings", "Edit", "Debug", "Clean", "Quit")
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

        prompt_value = payload.get("prompt")
        if isinstance(prompt_value, str):
            candidate = prompt_value.strip()
            if candidate:
                self.config.prompt = candidate

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

        tts_engine_value = payload.get("tts_engine")
        if isinstance(tts_engine_value, str):
            candidate_engine = tts_engine_value.strip().lower()
            if candidate_engine in {"melo", "piper"}:
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

    def _save_persisted_settings(self) -> None:
        settings_path = self._settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 3,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "hitl_enabled": bool(self._hitl_enabled),
            "fast_mode": bool(self.config.fast_mode),
            "prompt": self.config.prompt,
            "asset_keywords": list(self.config.asset_keywords),
            "minutes": int(self.config.minutes),
            "tts_engine": self.config.tts_engine,
            "piper_voice_id": self.config.piper_voice_id,
            "piper_speaker_id": self.config.piper_speaker_id,
            "voice_profile": self.config.voice_profile,
            "voice_speed": round(float(self.config.voice_speed), 3),
            "melo_language": self.config.melo_language,
            "melo_speaker": self.config.melo_speaker,
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
    asset_keywords: list[str],
    project_dir: Path,
    minutes: int,
    tts_engine: str,
    piper_voice_id: str,
    piper_speaker_id: int | None,
    voice_profile: str,
    voice_speed: float,
    melo_language: str,
    melo_speaker: str,
    fast_mode: bool = False,
) -> int:
    language_value = str(melo_language).strip().upper() or "EN"
    if language_value not in LocalVideoMvpTui.MELO_LANGUAGE_CHOICES:
        language_value = "EN"

    config = TuiConfig(
        prompt=prompt,
        asset_keywords=[str(item).strip() for item in (asset_keywords or []) if str(item).strip()],
        project_dir=project_dir,
        minutes=max(1, minutes),
        fast_mode=bool(fast_mode),
        tts_engine=(str(tts_engine).strip().lower() or "melo"),
        piper_voice_id=str(piper_voice_id).strip(),
        piper_speaker_id=piper_speaker_id,
        voice_profile=voice_profile,
        voice_speed=max(0.5, min(2.0, float(voice_speed))),
        melo_language=language_value,
        melo_speaker=str(melo_speaker).strip() or "EN-US",
    )
    if config.tts_engine not in {"melo", "piper"}:
        config.tts_engine = "melo"
    app = LocalVideoMvpTui(config)
    return app.run()
