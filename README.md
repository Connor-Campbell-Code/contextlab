# contextlab

See what actually happens every time you send a prompt.

contextlab is a transparent reverse proxy for the Anthropic API that tees every request into SQLite and analyzes it off the hot path — then shows you the per-prompt signals that plain token counters hide:

- **Context composition** — how much of each request is system prompt, tools, history, tool results
- **Cache *dollar* economics** — what caching actually saved you, in USD, not tokens
- **Turn-over-turn deltas** — watch your context grow and spot the bloat
- **Tool-result sizes** — which tools are eating your window
- **Latency decomposition** — time-to-first-token vs. generation
- **Compression headroom** (optional) — what prompt compression would save on your real traffic

Token counts come from the API's own `usage` block — billing ground truth, never estimates.

Two frontends share the same live feed:

- **`contextlab top`** — a compact terminal dashboard built for a tmux pane (Textual)
- **A browser dashboard** — richer charts plus a "Learn" view that explains every tile (React)

## Quickstart (terminal only, no Node required)

Requires Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Connor-Campbell-Code/contextlab && cd contextlab
uv run contextlab proxy                      # listens on 127.0.0.1:8484
```

In the shell where you run your agent (e.g. Claude Code):

```sh
export ANTHROPIC_BASE_URL="http://127.0.0.1:8484"
claude                                       # use it normally
```

In another pane (tmux split works nicely):

```sh
uv run contextlab top
```

Every prompt you send now streams into the TUI: spend, cache hit ratio, per-turn feed, and a stacked context-composition chart.

`contextlab top` keys: `q` quit · `s` cycle session filter · `p` drop into a REPL using `--workdir`'s `.venv`.

## One-command tmux workspace

`scripts/tmux-dev.sh` builds the whole setup for you — run it from any project directory:

```
┌─────────────┬──────────────────┐
│             │ terminal         │
│ claude      ├──────────────────┤
│ (via proxy) │ contextlab top   │
└─────────────┴──────────────────┘
```

It starts the proxy in a background tmux window if one isn't already running, launches Claude Code routed through it, and opens the TUI — one session per project, named after the directory. Re-running it from the same directory reattaches. Symlink it somewhere on your PATH to launch from anywhere:

```sh
ln -s "$(pwd)/scripts/tmux-dev.sh" ~/.local/bin/tmux_code
```

## Browser dashboard

Requires Node ≥ 20. Build once and the proxy serves it:

```sh
cd dashboard
npm ci
npm run build
```

Then open <http://127.0.0.1:8484/_contextlab/app/>.

For dashboard development, `npm run dev` starts Vite with HMR and proxies the data endpoints to the proxy on `:8484`.

## Optional: compression headroom lens

The proxy can score every request with [headroom](https://pypi.org/project/headroom-ai/) to estimate what prompt compression would have saved, and calibrate that estimate against the API's own `count_tokens` endpoint. It runs in a separate venv so its dependencies never touch the proxy's:

```sh
uv venv .venv-headroom
uv pip install --python .venv-headroom/bin/python headroom-ai tiktoken
```

That's it — the proxy auto-detects `.venv-headroom` at the repo root on the next start. If the venv is missing the lens silently stays off; if it misbehaves it disables itself after 3 failures. Point `CONTEXTLAB_HEADROOM_PY` at a different interpreter to relocate it.

### Shadow cache ledger

Token counts alone can't tell you whether compression saves money on cached
traffic: rewriting history converts 0.1× cache reads into full-price cache
writes for everything downstream of the first changed byte. The lens
therefore keeps a **shadow ledger** per conversation — a simulation of the
session as it would have unfolded with compression on since turn 1, tracking
byte-prefix survival between consecutive simulated requests and pricing both
worlds in cache-adjusted dollars. The KPI (`shadow` in the TUI, "Shadow
ledger" tile in the dashboard) is the net: positive means compression would
have saved money on your traffic, negative means the cache it busts costs
more than the tokens it saves. Reset turns and the turn after (instrument
warmup) are excluded from the sums.

The simulation models current headroom behavior: session-sticky retrieval
tool injection, and — when the installed headroom supports the
`frozen_message_count` parameter in library-mode `compress()`
([headroom PR #2178](https://github.com/headroomlabs-ai/headroom/pull/2178))
— cache-aware compression that never rewrites already-sent messages. On
stock headroom the worker detects the missing parameter and degrades
gracefully to measuring legacy behavior; `CONTEXTLAB_FROZEN_PREFIX=0`
forces legacy measurement either way.

## CLI reference

```
contextlab proxy [--port 8484] [--host 127.0.0.1]
                 [--upstream https://api.anthropic.com] [--db contextlab.db]
contextlab top   [--url http://127.0.0.1:8484] [--workdir .] [--limit 500]
```

HTTP surface (everything else is proxied through to `--upstream` untouched):

| Endpoint | What |
|---|---|
| `GET /_contextlab/recent?limit=N` | last N analyzed requests |
| `WS /_contextlab/ws` | live event feed |
| `GET /_contextlab/health` | liveness |
| `GET /_contextlab/app/` | the built browser dashboard |

Environment variables:

| Variable | What |
|---|---|
| `CONTEXTLAB_DASHBOARD` | override the dashboard `dist/` directory the proxy serves |
| `CONTEXTLAB_HEADROOM_PY` | python interpreter for the headroom lens worker |
| `CONTEXTLAB_SPOTCHECK_EVERY` | calibrate the lens against `count_tokens` every N requests (default 25) |

## Privacy note

`contextlab.db` stores the **full request/response bodies** of your captured traffic (zlib-compressed) so metrics can be recomputed later. It stays on your machine and is gitignored — treat it like the transcripts it contains: don't commit it or share it.

## Development

```sh
uv sync
uv run pytest          # python: proxy, metrics, TUI model parity
cd dashboard && npm run lint
```

The TUI and the web dashboard implement the same KPI math in two languages; `src/contextlab/tui/model.py` is the documented source of truth and `tests/test_tui_model.py` holds the parity tests.

## License

[MIT](LICENSE)
