from __future__ import annotations

import curses
import datetime as dt
import os
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

    def __init__(self, config: TuiConfig) -> None:
        self.config = config
        self._stdscr: Any = None
        self._repo_root = Path(__file__).resolve().parents[2]

        self._lock = threading.Lock()
        self._logs: Deque[str] = deque(maxlen=1200)
        self._status = "Ready. Press R to generate now."
        self._running = False
        self._exit_requested = False

        self._follow_logs = True
        self._log_scroll = 0
        self._run_started_at: float | None = None
        self._last_elapsed_seconds: float | None = None

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

        if key in {curses.KEY_UP}:
            self._adjust_log_scroll(1)
            return
        if key in {curses.KEY_DOWN}:
            self._adjust_log_scroll(-1)
            return
        if key in {curses.KEY_PPAGE}:
            self._adjust_log_scroll(10)
            return
        if key in {curses.KEY_NPAGE}:
            self._adjust_log_scroll(-10)
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

        if char in {"i", "I"}:
            self._start_inspect_workflow()
            return

        if char in {"p", "P"}:
            self._edit_prompt()
            return

        if char in {"d", "D"}:
            self._edit_project_dir()
            return

        if char in {"m", "M"}:
            self._edit_minutes()
            return

        if char in {"f", "F"}:
            self._toggle_follow_mode()
            return

        if char in {"g", "G"}:
            self._jump_to_latest_logs()
            return

        if char in {"k", "K"}:
            self._adjust_log_scroll(1)
            return

        if char in {"j", "J"}:
            self._adjust_log_scroll(-1)
            return

    def _start_run_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._jump_to_latest_logs()
        self._mark_command_start()
        self._set_running(True)
        self._set_status("Starting run workflow...")
        self._worker = threading.Thread(target=self._run_workflow, daemon=True)
        self._worker.start()

    def _start_inspect_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._jump_to_latest_logs()
        self._mark_command_start()
        self._set_running(True)
        self._set_status("Inspecting project...")
        self._worker = threading.Thread(target=self._inspect_workflow, daemon=True)
        self._worker.start()

    def _run_workflow(self) -> None:
        try:
            self._append_log("Using preferred profile. Press R to run again after completion.")
            self._ensure_ollama_running()
            run_code = self._run_and_stream(self._build_run_command(), label="run")

            if run_code == 0:
                self._set_status("Run succeeded. Inspecting outputs...")
                self._run_and_stream(self._build_inspect_command(), label="inspect")
                self._set_status("Run complete.")
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
            process.wait()
            self._append_log(f"[{label}] exited with code {process.returncode}")
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

        hint = "R Run  I Inspect  P Prompt  D Project  M Minutes  J/K Scroll  F Follow  G Latest  Q Quit"
        self._safe_addstr(1, 0, hint, width, attr=self._attr("muted"))
        self._safe_hline(2, width)

        self._draw_box(3, 0, 5, width, title=" Configuration ", attr=self._attr("accent"))
        self._safe_addstr(4, 2, f"Prompt : {self._trim_tail(self.config.prompt, width - 14)}", width)
        self._safe_addstr(5, 2, f"Project: {self._trim_middle(str(self.config.project_dir), width - 14)}", width)
        self._safe_addstr(6, 2, f"Minutes: {self.config.minutes}", width)

        self._draw_box(8, 0, 5, width, title=" Runtime ", attr=self._attr("accent"))
        state_text, state_attr = self._state_display()
        self._safe_addstr(9, 2, f"State  : {state_text}", width, attr=state_attr)
        self._safe_addstr(10, 2, f"Status : {self._trim_tail(self._get_status(), width - 14)}", width)
        self._safe_addstr(11, 2, f"Log    : {self._trim_middle(str(self._latest_log_path), width - 14)}", width)

        logs_top = 13
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
        self._safe_addstr(1, 0, "R Run  I Inspect  Q Quit", width, attr=self._attr("muted"))
        self._safe_addstr(3, 0, f"Prompt: {self._trim_tail(self.config.prompt, width - 8)}", width)
        self._safe_addstr(4, 0, f"Project: {self._trim_middle(str(self.config.project_dir), width - 9)}", width)
        self._safe_addstr(5, 0, f"Minutes: {self.config.minutes}", width)
        state_text, state_attr = self._state_display()
        self._safe_addstr(7, 0, f"{state_text}", width, attr=state_attr)
        self._safe_addstr(8, 0, self._trim_tail(self._get_status(), width), width)

        logs = self._get_logs()
        log_rows = max(1, height - 10)
        for idx, line in enumerate(logs[-log_rows:]):
            self._safe_addstr(10 + idx, 0, self._trim_tail(line, width), width, attr=self._log_line_attr(line))

    def _state_display(self) -> tuple[str, int]:
        running = self._is_running()
        elapsed = self._elapsed_seconds()

        if running:
            frame = self._spinner_frame()
            return f"{frame} RUNNING  {elapsed:.1f}s", self._attr("ok", bold=True)

        if self._last_elapsed_seconds is not None:
            return f"IDLE  last run {self._last_elapsed_seconds:.1f}s", self._attr("muted")

        return "IDLE", self._attr("muted")

    def _footer_text(self, total_logs: int) -> str:
        with self._lock:
            follow = self._follow_logs
            scroll = self._log_scroll
        if follow:
            mode = "follow"
        else:
            mode = f"scroll +{scroll}"
        return f"Logs: {total_logs} | Mode: {mode} | File: {self._latest_log_path}"

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
            with self._lock:
                self._log_scroll = 0
                self._follow_logs = True
            return logs

        with self._lock:
            follow = self._follow_logs
            scroll = self._log_scroll

        max_scroll = max(0, total - rows)
        if follow:
            scroll = 0
        if scroll > max_scroll:
            scroll = max_scroll
            with self._lock:
                self._log_scroll = scroll

        start = max(0, total - rows - scroll)
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

    def _elapsed_seconds(self) -> float:
        with self._lock:
            started = self._run_started_at
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - started)

    def _mark_command_start(self) -> None:
        with self._lock:
            self._run_started_at = time.monotonic()
            self._last_elapsed_seconds = None

    def _mark_command_stop(self) -> None:
        with self._lock:
            if self._run_started_at is not None:
                self._last_elapsed_seconds = max(0.0, time.monotonic() - self._run_started_at)
            self._run_started_at = None

    def _adjust_log_scroll(self, delta: int) -> None:
        with self._lock:
            self._log_scroll = max(0, self._log_scroll + delta)
            self._follow_logs = self._log_scroll == 0

    def _toggle_follow_mode(self) -> None:
        with self._lock:
            self._follow_logs = not self._follow_logs
            if self._follow_logs:
                self._log_scroll = 0
        self._set_status("Follow mode on." if self._follow_logs else "Follow mode off (manual scroll).")

    def _jump_to_latest_logs(self) -> None:
        with self._lock:
            self._follow_logs = True
            self._log_scroll = 0
        self._set_status("Jumped to latest logs.")

    def _edit_prompt(self) -> None:
        if self._is_running():
            self._set_status("Cannot edit fields while command is running.")
            return

        value = self._prompt_input("Prompt", self.config.prompt)
        if value:
            self.config.prompt = value
            self._set_status("Prompt updated.")

    def _edit_project_dir(self) -> None:
        if self._is_running():
            self._set_status("Cannot edit fields while command is running.")
            return

        value = self._prompt_input("Project dir", str(self.config.project_dir))
        if value:
            self.config.project_dir = Path(value).expanduser().resolve()
            self._set_status("Project directory updated.")

    def _edit_minutes(self) -> None:
        if self._is_running():
            self._set_status("Cannot edit fields while command is running.")
            return

        value = self._prompt_input("Minutes", str(self.config.minutes))
        if not value:
            return

        try:
            minutes = int(value)
        except ValueError:
            self._set_status("Minutes must be an integer.")
            return

        if minutes <= 0:
            self._set_status("Minutes must be greater than zero.")
            return

        self.config.minutes = minutes
        self._set_status("Minutes updated.")

    def _prompt_input(self, label: str, current_value: str) -> str | None:
        if self._stdscr is None:
            return None

        stdscr = self._stdscr
        height, width = stdscr.getmaxyx()
        modal_h = 7
        modal_w = min(max(54, len(current_value) + 20), max(20, width - 4))
        top = max(0, (height - modal_h) // 2)
        left = max(0, (width - modal_w) // 2)

        win = curses.newwin(modal_h, modal_w, top, left)
        win.keypad(True)
        win.nodelay(False)
        win.timeout(-1)

        self._draw_box(top, left, modal_h, modal_w, title=f" {label} ", attr=self._attr("accent"))
        self._safe_addstr(top + 2, left + 2, f"Current: {self._trim_tail(current_value, modal_w - 12)}", width)
        self._safe_addstr(top + 4, left + 2, "> ", width, attr=self._attr("title", bold=True))
        stdscr.refresh()

        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        raw = b""
        try:
            raw = win.getstr(4, 4, max(1, modal_w - 6))
        except curses.error:
            raw = b""
        finally:
            curses.noecho()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            stdscr.nodelay(True)
            stdscr.timeout(120)

        value = raw.decode("utf-8", errors="ignore").strip()
        if not value:
            return None
        return value

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
            if self._follow_logs:
                self._log_scroll = 0

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
