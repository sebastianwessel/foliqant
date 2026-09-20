"""Concise progress reporting and graceful curation pause control."""

from __future__ import annotations

import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Literal, TextIO

from ..errors import ModelError

ProgressMode = Literal["auto", "always", "never"]
type _SignalHandler = Callable[[int, FrameType | None], object] | int | None


class CurationControl:
    """Coordinate CLI progress and a resumable first-interrupt pause."""

    def __init__(
        self,
        progress: ProgressMode = "never",
        *,
        stream: TextIO | None = None,
        handle_signals: bool = False,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self._stream = stream or sys.stderr
        self._tty = self._stream.isatty()
        self._progress = progress == "always" or (progress == "auto" and self._stream.isatty())
        self._handle_signals = handle_signals
        self._heartbeat_seconds = heartbeat_seconds
        self._pause_requested = False
        self._forced_interrupt = False
        self._pause_notice_emitted = False
        self._active_depth = 0
        self._previous_sigint: _SignalHandler = None
        self._write_lock = threading.Lock()
        self._snapshot = ""
        self._progress_line_open = False
        self._progress_width = 0
        self._reported_run: Path | None = None

    @property
    def pause_requested(self) -> bool:
        """Whether the first interrupt requested a pause."""
        return self._pause_requested

    @contextmanager
    def active(self) -> Iterator[None]:
        """Install and always restore the optional SIGINT handler."""
        outermost = self._active_depth == 0
        self._active_depth += 1
        if outermost and self._handle_signals:
            self._previous_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle_sigint)
        try:
            yield
        finally:
            self._active_depth -= 1
            if outermost and self._handle_signals:
                assert self._previous_sigint is not None
                signal.signal(signal.SIGINT, self._previous_sigint)
                self._previous_sigint = None
            if outermost and self._pause_requested and not self._pause_notice_emitted:
                if self._forced_interrupt:
                    self._write(
                        "[foliqant] interrupted immediately; the current candidate may repeat "
                        "when the same command is resumed."
                    )
                    self._pause_notice_emitted = True
                else:
                    self._write(
                        "[foliqant] stopped before a safe pause boundary; the current candidate "
                        "may repeat. Resume by running the same command."
                    )
                    self._pause_notice_emitted = True
            if outermost:
                self._finish_progress_line()

    def _handle_sigint(self, _signum: int, _frame: FrameType | None) -> None:
        # A signal handler must only change trivial state. Output happens in the
        # heartbeat thread or at a safe orchestration boundary.
        if self._pause_requested:
            self._forced_interrupt = True
            raise KeyboardInterrupt
        self._pause_requested = True

    def checkpoint(self) -> None:
        """Stop at a safe boundary after persisting the current outcome."""
        if not self._pause_requested:
            return
        self._emit_pause_notice()
        raise ModelError("INTERRUPTED", "Curation paused; run the same command to resume")

    def report(
        self,
        phase: str,
        run: Path,
        *,
        completed: int = 0,
        total: int = 0,
        accepted: int = 0,
        quarantined: int = 0,
        reused: int = 0,
        cached: int = 0,
        elapsed_seconds: float | None = None,
    ) -> None:
        """Publish one concise progress snapshot to stderr when enabled."""
        percent = (
            100
            if total == 0 and phase == "completed"
            else (int(completed * 100 / total) if total else 0)
        )
        elapsed = "" if elapsed_seconds is None else f" elapsed={int(elapsed_seconds)}s"
        self._snapshot = (
            f"[foliqant] phase={phase} completed={completed}/{total} "
            f"percent={percent}% accepted={accepted} quarantined={quarantined} "
            f"reused={reused} cached={cached}{elapsed}"
        )
        if self._progress:
            if self._reported_run != run:
                self._write(f"[foliqant] run={run}")
                self._reported_run = run
            self._write_progress(self._snapshot)

    @contextmanager
    def local_request(self) -> Iterator[None]:
        """Repeat the latest generating snapshot while one local call is active."""
        started = time.monotonic()
        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(self._heartbeat_seconds):
                suffix = f" elapsed={int(time.monotonic() - started)}s"
                pause = " pause=requested" if self._pause_requested else ""
                if self._progress and self._snapshot:
                    self._write_progress(self._snapshot.split(" elapsed=", 1)[0] + suffix + pause)

        worker = threading.Thread(target=heartbeat, name="foliqant-progress", daemon=True)
        if self._progress:
            worker.start()
        try:
            yield
        except ModelError as error:
            if error.code != "TIMEOUT":
                raise
            raise ModelError(
                "TIMEOUT",
                "A local generation request exceeded its deadline; completed results are saved. "
                "Check that the model server has finished the timed-out request, then rerun "
                "the same command with unchanged settings to resume. "
                "The deadline applies to each request, not the whole run.",
            ) from error
        finally:
            stopped.set()
            if worker.is_alive():
                worker.join()

    def _emit_pause_notice(self) -> None:
        if self._pause_notice_emitted:
            return
        self._pause_notice_emitted = True
        self._write("[foliqant] paused safely. Resume by running the same command.")

    def _write(self, message: str) -> None:
        with self._write_lock:
            if self._progress_line_open:
                print(file=self._stream)
                self._progress_line_open = False
            print(message, file=self._stream, flush=True)

    def _write_progress(self, message: str) -> None:
        with self._write_lock:
            if self._tty:
                self._progress_width = max(self._progress_width, len(message))
                print(
                    "\r" + message.ljust(self._progress_width),
                    end="",
                    file=self._stream,
                    flush=True,
                )
                self._progress_line_open = True
            else:
                print(message, file=self._stream, flush=True)

    def _finish_progress_line(self) -> None:
        with self._write_lock:
            if self._progress_line_open:
                print(file=self._stream, flush=True)
                self._progress_line_open = False


def cached_request_count(cache_dir: Path) -> int:
    """Count immutable completed local-call cache records."""
    calls = cache_dir / "calls"
    if not calls.is_dir():
        return 0
    return sum(path.is_file() for path in calls.glob("*.json"))
