"""HeadroomLens manager contract — tested against a scripted fake worker.

The real worker needs .venv-headroom; the manager's job (process lifecycle,
protocol, timeouts, graceful degradation) is what these tests pin down.
"""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from contextlab.metrics import headroom_lens
from contextlab.metrics.headroom_lens import HeadroomLens

MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    """Point the lens at this interpreter running a scripted worker."""

    def install(body: str) -> None:
        script = tmp_path / "worker.py"
        script.write_text(textwrap.dedent(body))
        monkeypatch.setattr(headroom_lens, "WORKER", script)

    return install


def test_scores_via_worker(fake_worker):
    fake_worker(
        """
        import json, sys
        for line in sys.stdin:
            req = json.loads(line)
            out = {"id": req["id"], "ok": True,
                   "lens": {"savings_pct": 12.5, "echo_n": len(req["messages"])}}
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()
        """
    )
    lens = HeadroomLens(python=sys.executable)
    assert lens.score(MESSAGES, "claude-x") == {"savings_pct": 12.5, "echo_n": 1}
    # Same process serves subsequent calls, ids advance.
    assert lens.score(MESSAGES, "claude-x")["echo_n"] == 1
    lens.close()


def test_include_after_reaches_worker(fake_worker):
    fake_worker(
        """
        import json, sys
        for line in sys.stdin:
            req = json.loads(line)
            out = {"id": req["id"], "ok": True,
                   "lens": {"include_after": req.get("include_after")}}
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()
        """
    )
    lens = HeadroomLens(python=sys.executable)
    assert lens.score(MESSAGES, None)["include_after"] is False
    assert lens.score(MESSAGES, None, include_after=True)["include_after"] is True
    lens.close()


def test_context_key_reaches_worker_and_shadow_returns(fake_worker):
    fake_worker(
        """
        import json, sys
        for line in sys.stdin:
            req = json.loads(line)
            out = {"id": req["id"], "ok": True,
                   "lens": {"shadow": {"context_key": req.get("context_key")}}}
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()
        """
    )
    lens = HeadroomLens(python=sys.executable)
    assert lens.score(MESSAGES, None)["shadow"]["context_key"] is None
    assert lens.score(MESSAGES, None, context_key="sess:abc123")["shadow"] == {
        "context_key": "sess:abc123"
    }
    lens.close()


def test_worker_error_returns_none_and_recovers(fake_worker):
    fake_worker(
        """
        import json, sys
        for line in sys.stdin:
            req = json.loads(line)
            sys.stdout.write(json.dumps({"id": req["id"], "ok": False, "error": "boom"}) + "\\n")
            sys.stdout.flush()
        """
    )
    lens = HeadroomLens(python=sys.executable)
    assert lens.score(MESSAGES, None) is None
    assert lens.enabled  # a declined payload is not a lens failure
    lens.close()


def test_timeout_kills_worker_and_eventually_disables(fake_worker):
    fake_worker(
        """
        import time
        time.sleep(60)
        """
    )
    lens = HeadroomLens(python=sys.executable)
    for _ in range(headroom_lens.MAX_FAILURES):
        assert lens.score(MESSAGES, None, timeout=0.2) is None
    assert not lens.enabled
    lens.close()


def test_missing_python_disables_quietly(tmp_path):
    lens = HeadroomLens(python=tmp_path / "nope" / "python")
    assert not lens.enabled
    assert lens.score(MESSAGES, None) is None


def test_empty_messages_not_scored(fake_worker):
    fake_worker("import sys\nraise SystemExit(1)\n")
    lens = HeadroomLens(python=sys.executable)
    assert lens.score([], None) is None
    lens.close()
