#!/usr/bin/env bash
# Launch a per-project tmux dev environment: one session per directory, named
# after the directory it's launched from. Pass an explicit name as $1 to override
# (useful when two project dirs share a basename).
#
# Layout:
#   ┌─────────────┬──────────────────┐
#   │             │ terminal         │
#   │ claude      ├──────────────────┤
#   │ (via proxy) │ contextlab top   │
#   └─────────────┴──────────────────┘
#
# If the contextlab proxy isn't running, it is started in a background window
# of the same session, so `scripts/tmux-dev.sh` is all a fresh clone needs.
set -euo pipefail

# Directory the script was invoked from — each project gets its own session.
INVOCATION_DIR="$PWD"
# The contextlab checkout this script lives in (works through symlinks on
# systems with GNU readlink; plain $BASH_SOURCE otherwise).
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed or is not on PATH." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed (https://docs.astral.sh/uv/) — needed to run the proxy and TUI." >&2
  exit 1
fi

# Session name: explicit arg wins; otherwise derive from the launch directory.
# tmux forbids '.' and ':' in target names, so map those (and spaces) to '_'.
if [ -n "${1:-}" ]; then
  SESSION="$1"
else
  SESSION="$(basename "$INVOCATION_DIR")"
fi
SESSION="${SESSION//[.: ]/_}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  exec tmux attach-session -t "$SESSION"
fi

CONTEXTLAB="http://127.0.0.1:8484"
proxy_up() { curl -sf -m 1 "$CONTEXTLAB/_contextlab/health" >/dev/null 2>&1; }

tmux new-session -d -s "$SESSION" -c "$INVOCATION_DIR" -n dev

# Start the proxy in a background window when it isn't already running
# (its SQLite db lands in the contextlab checkout, which gitignores *.db).
if ! proxy_up; then
  tmux new-window -d -t "$SESSION" -n proxy -c "$REPO_ROOT"
  tmux send-keys -t "$SESSION:proxy" "uv run contextlab proxy" C-m
  for _ in $(seq 1 20); do proxy_up && break; sleep 0.25; done
fi

# Route Claude Code through the proxy (dashboard: $CONTEXTLAB/_contextlab/app/).
# Falls back to a direct connection if the proxy still isn't up rather than
# breaking the session — the pane title says which path you got.
if proxy_up; then
  # export (not a one-shot prefix) so a manual claude relaunch in this pane
  # keeps routing through the proxy.
  tmux send-keys -t "$SESSION:dev.0" "export ANTHROPIC_BASE_URL=$CONTEXTLAB && claude" C-m
  tmux select-pane -t "$SESSION:dev.0" -T "claude→contextlab"
else
  tmux send-keys -t "$SESSION:dev.0" "claude" C-m
  tmux select-pane -t "$SESSION:dev.0" -T "claude (direct: contextlab proxy down)"
fi

tmux split-window -h -l 50% -t "$SESSION:dev.0" -c "$INVOCATION_DIR"
tmux select-pane -t "$SESSION:dev.1" -T "terminal"

# Bottom-right pane gets 2/3 of the terminal height — the TUI's context-growth
# chart needs the rows more than the scratch terminal does. Press `p` inside it
# to drop into this project's python REPL, Ctrl-D to come back, `q` to quit.
# Falls through to a plain venv python if the TUI exits nonzero — the pane
# never dies.
tmux split-window -v -l 67% -t "$SESSION:dev.1" -c "$INVOCATION_DIR"
tmux select-pane -t "$SESSION:dev.2" -T "contextlab top"
PY_FALLBACK='source .venv/bin/activate 2>/dev/null; python'
tmux send-keys -t "$SESSION:dev.2" \
  "uv run --project \"$REPO_ROOT\" contextlab top --workdir \"$INVOCATION_DIR\" || { $PY_FALLBACK; }" C-m

tmux select-pane -t "$SESSION:dev.0"

exec tmux attach-session -t "$SESSION"
