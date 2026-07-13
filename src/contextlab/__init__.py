"""contextlab — see what actually happens every time you send a prompt."""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="contextlab")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("proxy", help="run the instrumentation proxy")
    p.add_argument("--port", type=int, default=8484)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--upstream", default="https://api.anthropic.com")
    p.add_argument("--db", default="contextlab.db")
    t = sub.add_parser("top", help="compact terminal dashboard (spend, cache, feed, context)")
    t.add_argument("--url", default="http://127.0.0.1:8484")
    t.add_argument("--workdir", default=".", help="project dir whose .venv the `p` REPL uses")
    t.add_argument("--limit", type=int, default=500)
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
    elif args.command == "top":
        import os

        from .tui.app import run

        run(args.url, os.path.abspath(args.workdir), args.limit)
