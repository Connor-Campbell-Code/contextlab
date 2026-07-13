"""Proxy-side manager for the Headroom-lens sidecar.

Keeps one headroom_worker.py subprocess alive in .venv-headroom and scores
requests over a JSON-lines pipe. Everything degrades to None: missing venv,
worker crash, timeout, bad JSON — the proxy's own metrics never depend on
the lens. Scoring happens in record()'s worker thread, off the request path.
"""

from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("contextlab.headroom_lens")

WORKER = Path(__file__).with_name("headroom_worker.py")
DEFAULT_PYTHON = Path(__file__).resolve().parents[3] / ".venv-headroom" / "bin" / "python"
MAX_FAILURES = 3  # consecutive; then the lens turns itself off for the run


class HeadroomLens:
    def __init__(self, python: str | os.PathLike[str] | None = None) -> None:
        self._python = Path(
            python or os.environ.get("CONTEXTLAB_HEADROOM_PY") or DEFAULT_PYTHON
        )
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._failures = 0
        self._disabled = not self._python.is_file()
        if self._disabled:
            log.info("headroom lens disabled: %s not found", self._python)

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def _ensure_proc(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                [str(self._python), str(WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            log.info("headroom lens worker started (pid %d)", self._proc.pid)
        return self._proc

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc = None

    def _read_line(self, proc: subprocess.Popen[str], timeout: float) -> str | None:
        assert proc.stdout is not None
        # First score after a cold start pays the headroom import; the pipe
        # stays empty until then, so wait on the fd rather than blocking.
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            return None
        return proc.stdout.readline()

    def score(
        self,
        messages: list[dict[str, Any]],
        model: str | None,
        timeout: float = 30.0,
        include_after: bool = False,
    ) -> dict[str, Any] | None:
        """Would-be Headroom outcome for this request's messages, or None.

        include_after asks the worker to return the compressed message list
        (lens["messages_after"], None when nothing fired) so the caller can
        spot-check it against the API's count_tokens.
        """
        if self._disabled or not messages:
            return None
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            try:
                proc = self._ensure_proc()
                assert proc.stdin is not None
                proc.stdin.write(
                    json.dumps(
                        {
                            "id": req_id,
                            "messages": messages,
                            "model": model,
                            "include_after": include_after,
                        }
                    )
                    + "\n"
                )
                proc.stdin.flush()
                line = self._read_line(proc, timeout)
                if not line:
                    raise TimeoutError(f"no response within {timeout}s")
                resp = json.loads(line)
            except Exception:
                log.exception("headroom lens scoring failed")
                self._kill()
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._disabled = True
                    log.warning(
                        "headroom lens disabled after %d consecutive failures", self._failures
                    )
                return None

            self._failures = 0
            if resp.get("id") != req_id or not resp.get("ok"):
                if resp.get("error"):
                    log.info("headroom lens declined request: %s", resp["error"])
                return None
            return resp.get("lens")

    def close(self) -> None:
        with self._lock:
            self._kill()
