from __future__ import annotations

import curses
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
    def __init__(self, config: TuiConfig) -> None:
        self.config = config
        self._stdscr: Any = None

        self._lock = threading.Lock()
        self._logs: Deque[str] = deque(maxlen=800)
        self._status = "Ready. Press R to run."
        self._running = False
        self._exit_requested = False

        self._worker: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None

        self._started_ollama = False
        self._ollama_process: subprocess.Popen[str] | None = None
        self._ollama_log_handle: TextIO | None = None

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

    def _handle_key(self, key: int) -> None:
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

    def _start_run_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._set_running(True)
        self._set_status("Starting run workflow...")
        self._worker = threading.Thread(target=self._run_workflow, daemon=True)
        self._worker.start()

    def _start_inspect_workflow(self) -> None:
        if self._is_running():
            self._set_status("A command is already running.")
            return

        self._set_running(True)
        self._set_status("Inspecting project...")
        self._worker = threading.Thread(target=self._inspect_workflow, daemon=True)
        self._worker.start()

    def _run_workflow(self) -> None:
        try:
            self._append_log("Using onboarding run profile (press P/D/M to adjust core fields).")
            self._ensure_ollama_running()
            run_code = self._run_and_stream(self._build_run_command(), label="run")

            if run_code == 0:
                self._set_status("Run succeeded. Inspecting outputs...")
                self._run_and_stream(self._build_inspect_command(), label="inspect")
                self._set_status("Run complete. Press R to run again.")
            else:
                self._set_status(f"Run failed with exit code {run_code}.")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERROR: {exc}")
            self._set_status("Run failed before completion.")
        finally:
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
            self._set_running(False)

    def _run_and_stream(self, command: list[str], label: str) -> int:
        self._append_log(f"$ {shlex.join(command)}")
        process = subprocess.Popen(
            command,
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

        self._safe_addstr(0, 0, "Local Video MVP TUI", width)
        self._safe_addstr(1, 0, "R Run  I Inspect  P Prompt  D Project  M Minutes  Q Quit", width)

        self._safe_addstr(3, 0, f"Prompt: {self.config.prompt}", width)
        self._safe_addstr(4, 0, f"Project: {self.config.project_dir}", width)
        self._safe_addstr(5, 0, f"Minutes: {self.config.minutes}", width)

        running = self._is_running()
        status = self._get_status()
        self._safe_addstr(7, 0, f"State: {'RUNNING' if running else 'IDLE'}", width)
        self._safe_addstr(8, 0, f"Status: {status}", width)

        self._safe_addstr(10, 0, "Logs:", width)
        logs = self._get_logs()
        available_lines = max(1, height - 11)
        for index, line in enumerate(logs[-available_lines:]):
            self._safe_addstr(11 + index, 0, line, width)

        stdscr.refresh()

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
        prompt = f"{label} [{current_value}]: "

        stdscr.nodelay(False)
        stdscr.timeout(-1)
        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        try:
            stdscr.move(height - 1, 0)
            stdscr.clrtoeol()
            self._safe_addstr(height - 1, 0, prompt, width)
            stdscr.refresh()
            max_len = max(1, width - len(prompt) - 1)
            raw = stdscr.getstr(height - 1, min(len(prompt), max(0, width - 1)), max_len)
        except curses.error:
            raw = b""
        finally:
            curses.noecho()
            stdscr.nodelay(True)
            stdscr.timeout(120)
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        value = raw.decode("utf-8", errors="ignore").strip()
        if not value:
            return None
        return value

    def _safe_addstr(self, row: int, col: int, text: str, width: int) -> None:
        if self._stdscr is None:
            return
        if row < 0 or col < 0:
            return

        clipped = text.replace("\n", " ")
        if width > 0:
            clipped = clipped[: max(0, width - 1)]

        try:
            self._stdscr.addstr(row, col, clipped)
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

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._logs.append(line)

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


def run_tui(prompt: str, project_dir: Path, minutes: int) -> int:
    config = TuiConfig(prompt=prompt, project_dir=project_dir, minutes=max(1, minutes))
    app = LocalVideoMvpTui(config)
    return app.run()
