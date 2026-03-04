from __future__ import annotations

import curses
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, TextIO


@dataclass
class TuiConfig:
    prompt: str
    project_dir: Path
    minutes: int


class LocalVideoMvpTui:
    SPINNER_FRAMES = ["-", "\\", "|", "/"]
    STOCK_ENV_KEYS = ("PEXELS_API_KEY", "PIXABAY_API_KEY")
    STAGE_LINE_RE = re.compile(r"\[local-video-mvp\]\s+Stage\s+(\d+(?:\.\d+)?)/(\d+):\s+(.+)")
    STAGE_COMPLETE_RE = re.compile(r"\[local-video-mvp\]\s+([A-Za-z0-9_]+)\s+completed in\s+([0-9]+(?:\.[0-9]+)?)s")

    def __init__(self, config: TuiConfig) -> None:
        self.config = config
        self._stdscr: Any = None
        self._repo_root = Path(__file__).resolve().parents[2]

        self._lock = threading.Lock()
        self._logs: Deque[str] = deque(maxlen=1200)
        self._status = "Ready. Press R to generate now."
        self._running = False
        self._exit_requested = False

        self._run_started_at: float | None = None
        self._last_elapsed_seconds: float | None = None
        self._workflow_kind: str | None = None
        self._stage_label: str | None = None
        self._stage_index: float | None = None
        self._stage_total: int | None = None
        self._prior_total_seconds: float | None = None

        self._stock_api_keys: dict[str, str] = {}
        self._stock_key_sources: dict[str, str] = {}
        self._stock_key_warnings: list[str] = []

        self._worker: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None

        self._started_ollama = False
        self._ollama_process: subprocess.Popen[str] | None = None
        self._ollama_log_handle: TextIO | None = None

        self._session_log_handle: TextIO | None = None
        self._session_log_path, self._latest_log_path = self._init_session_log_paths()

        self._color_enabled = False
        self._color_pairs: dict[str, int] = {}

        self._open_session_log()
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

    def _start_run_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._refresh_stock_key_cache()
        if not self._passes_asset_hard_guard_precheck():
            return

        self._mark_command_start(workflow_kind="run")
        self._set_running(True)
        self._set_status("Starting run workflow...")
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
            self._append_log("Using preferred profile. Press R to run again after completion.")
            self._append_log(self._asset_preflight_message())
            if self._stock_key_sources:
                self._append_log(f"Stock key sources: {self._stock_key_sources}")
            for warning in self._stock_key_warnings:
                self._append_log(f"WARN: {warning}")
            self._ensure_ollama_running()
            run_code = self._run_and_stream(self._build_run_command(), label="run")

            if run_code == 0:
                placeholder_count = self._count_placeholder_scenes()
                if placeholder_count > 0:
                    self._append_log(
                        f"ERROR: Hard guard violation: {placeholder_count} placeholder scenes detected after run."
                    )
                    self._set_status("Hard guard violation: placeholders detected. Run rejected.")
                    return

                self._set_status("Run succeeded. Inspecting outputs...")
                inspect_code = self._run_and_stream(self._build_inspect_command(), label="inspect")
                exported_mp4 = self._export_final_mp4_to_downloads()

                if inspect_code == 0 and exported_mp4 is not None:
                    self._set_status(f"Run complete. MP4 exported to {exported_mp4.name}.")
                elif inspect_code == 0:
                    self._set_status("Run complete. MP4 export skipped.")
                else:
                    self._set_status(f"Run succeeded, inspect failed with exit code {inspect_code}.")
            else:
                self._set_status(f"Run failed with exit code {run_code}.")
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

        if label == "run":
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
                        self._append_log(line)
                        self._update_status_from_log_line(line)
            process.wait()
            self._append_log(f"[{label}] exited with code {process.returncode}")

            if label == "run" and int(process.returncode or 0) == 0:
                self._refresh_prior_total_seconds()

            return int(process.returncode or 0)
        finally:
            self._set_active_process(None)

    def _build_run_command(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "run",
            "--prompt",
            self.config.prompt,
            "--project-dir",
            str(self.config.project_dir),
            "--minutes",
            str(self.config.minutes),
            "--resolution",
            "1280x720",
            "--script-engine",
            "ollama",
            "--ollama-model",
            "qwen2.5:14b",
            "--require-ollama",
            "--tts-engine",
            "melo",
            "--voice-profile",
            "calm-documentary",
            "--video-effects",
            "subtle-motion",
            "--include-intro",
            "--include-outro",
            "--bookend-style",
            "minimal-clean",
            "--caption-engine",
            "faster-whisper",
            "--caption-style",
            "engagement",
            "--duration-tolerance",
            "0.25",
            "--strict-commercial-safe",
            "--require-external-assets",
            "--verbose",
        ]

    def _build_inspect_command(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "local_video_mvp.cli",
            "inspect",
            "--project-dir",
            str(self.config.project_dir),
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

    def _draw(self) -> None:
        if self._stdscr is None:
            return

        stdscr = self._stdscr
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        if width < 50 or height < 16:
            self._draw_compact(height, width)
            stdscr.refresh()
            return

        spinner = self._spinner_frame() if self._is_running() else "*"
        title = f" Imagine TUI {spinner} "
        self._safe_addstr(0, 0, title, width, attr=self._attr("title", bold=True))

        hint = "R Run  Q Quit"
        self._safe_addstr(1, 0, hint, width, attr=self._attr("muted"))
        self._safe_hline(2, width)

        self._draw_box(3, 0, 5, width, title=" Configuration ", attr=self._attr("accent"))
        self._safe_addstr(4, 2, f"Prompt : {self._trim_tail(self.config.prompt, width - 14)}", width)
        self._safe_addstr(5, 2, f"Project: {self._trim_middle(str(self.config.project_dir), width - 14)}", width)
        self._safe_addstr(6, 2, f"Minutes: {self.config.minutes}", width)

        self._draw_box(8, 0, 6, width, title=" Runtime ", attr=self._attr("accent"))
        state_text, state_attr = self._state_display()
        self._safe_addstr(9, 2, f"State  : {state_text}", width, attr=state_attr)
        self._safe_addstr(10, 2, f"Phase  : {self._trim_tail(self._progress_phase_text(), width - 14)}", width)
        assets_text, assets_attr = self._asset_status_display()
        self._safe_addstr(11, 2, f"Assets : {self._trim_tail(assets_text, width - 14)}", width, attr=assets_attr)
        self._safe_addstr(12, 2, f"Status : {self._trim_tail(self._get_status(), width - 14)}", width)

        logs_top = 14
        logs_height = max(3, height - logs_top - 1)
        self._draw_box(logs_top, 0, logs_height, width, title=" Logs ", attr=self._attr("accent"))

        logs = self._get_logs()
        content_top = logs_top + 1
        content_bottom = logs_top + logs_height - 2
        content_rows = max(1, content_bottom - content_top + 1)
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
        self._safe_addstr(1, 0, "R Run  Q Quit", width, attr=self._attr("muted"))
        self._safe_addstr(3, 0, f"Prompt: {self._trim_tail(self.config.prompt, width - 8)}", width)
        self._safe_addstr(4, 0, f"Project: {self._trim_middle(str(self.config.project_dir), width - 9)}", width)
        self._safe_addstr(5, 0, f"Minutes: {self.config.minutes}", width)
        state_text, state_attr = self._state_display()
        self._safe_addstr(7, 0, f"{state_text}", width, attr=state_attr)
        self._safe_addstr(8, 0, self._trim_tail(self._progress_phase_text(), width), width)
        assets_text, assets_attr = self._asset_status_display()
        self._safe_addstr(9, 0, self._trim_tail(assets_text, width), width, attr=assets_attr)
        self._safe_addstr(10, 0, self._trim_tail(self._get_status(), width), width)

        logs = self._get_logs()
        log_rows = max(1, height - 12)
        for idx, line in enumerate(logs[-log_rows:]):
            self._safe_addstr(12 + idx, 0, self._trim_tail(line, width), width, attr=self._log_line_attr(line))

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
            return "Inspect workflow" if workflow_kind == "inspect" else "Waiting for run"

        if stage_index is None or stage_total is None or stage_total <= 0:
            return "Starting run..."

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
        report_path = self.config.project_dir / "run_report.json"
        prior_total: float | None = None

        if report_path.exists():
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

    def _downloads_export_mp4_path(self) -> Path:
        downloads_dir = (Path.home() / "Downloads").resolve()
        project_name = self._slugify(self.config.project_dir.name)
        return downloads_dir / f"{project_name}.mp4"

    def _export_final_mp4_to_downloads(self) -> Path | None:
        source_mp4 = self.config.project_dir / "output" / "final.mp4"
        if not source_mp4.exists():
            self._append_log(f"WARN: final.mp4 not found at {source_mp4}")
            return None

        target_mp4 = self._downloads_export_mp4_path()
        target_mp4.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_mp4, target_mp4)
        self._append_log(f"Exported final MP4 to {target_mp4}")
        return target_mp4

    def _count_placeholder_scenes(self) -> int:
        timeline_path = self.config.project_dir / "timeline.json"
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

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._logs.append(line)

        if self._session_log_handle is not None:
            timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                self._session_log_handle.write(f"{timestamp} {line}\n")
                self._session_log_handle.flush()
            except Exception:
                pass

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


def run_tui(prompt: str, project_dir: Path, minutes: int) -> int:
    config = TuiConfig(prompt=prompt, project_dir=project_dir, minutes=max(1, minutes))
    app = LocalVideoMvpTui(config)
    return app.run()
