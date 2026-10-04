"""contextlab — see what actually happens every time you send a prompt."""

from __future__ import annotations

import argparse
import logging
import os

# Default event store; CONTEXTLAB_DB lets the db live outside the checkout.
DEFAULT_DB = os.environ.get("CONTEXTLAB_DB", "contextlab.db")


def main() -> None:
    parser = argparse.ArgumentParser(prog="contextlab")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("proxy", help="run the instrumentation proxy")
    p.add_argument("--port", type=int, default=8484)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--upstream", default="https://api.anthropic.com")
    p.add_argument("--db", default=DEFAULT_DB)
    t = sub.add_parser("top", help="compact terminal dashboard (spend, cache, feed, context)")
    t.add_argument("--url", default="http://127.0.0.1:8484")
    t.add_argument("--workdir", default=".", help="project dir whose .venv the `p` REPL uses")
    t.add_argument("--limit", type=int, default=500)
    r = sub.add_parser("reprice", help="recompute stored dollars after a pricing table change")
    r.add_argument("--db", default=DEFAULT_DB)
    r.add_argument("--dry-run", action="store_true", help="report the change, write nothing")
    args = parser.parse_args()

    if args.command == "proxy":
        import uvicorn

        from .proxy.app import create_app
        from .store.db import EventStore

        logging.basicConfig(level=logging.INFO)
        app = create_app(args.upstream, EventStore(args.db))
        print(f"contextlab proxy: http://{args.host}:{args.port} -> {args.upstream}")
        print(f'  use it:  export ANTHROPIC_BASE_URL="http://{args.host}:{args.port}"')
        print(f"  events:  {args.db}  |  ws: /_contextlab/ws  |  recent: /_contextlab/recent")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    elif args.command == "reprice":
        from .store.reprice import reprice

        summary = reprice(args.db, dry_run=args.dry_run)
        changed = int(summary.pop("_changed")[0])
        for model, (n, old, new) in sorted(summary.items(), key=lambda kv: -kv[1][2]):
            print(f"  {model:28} {int(n):6} rows  ${old:10.2f} -> ${new:10.2f}")
        old_t = sum(v[1] for v in summary.values())
        new_t = sum(v[2] for v in summary.values())
        verb = "would update" if args.dry_run else "updated"
        print(f"  total ${old_t:.2f} -> ${new_t:.2f}; {verb} {changed} rows in {args.db}")
    elif args.command == "top":
        from .tui.app import run

        run(args.url, os.path.abspath(args.workdir), args.limit)
