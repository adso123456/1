"""本地 15174 宿主页演示服务器。"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EmbedHostDemoHandler(SimpleHTTPRequestHandler):
    server_version = "WaterAgentEmbedHost/2.0"

    def log_message(self, format: str, *args: object) -> None:
        super().log_message(format, *args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15174)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = PROJECT_ROOT / "frontend" / "embed-host-demo"
    handler = partial(EmbedHostDemoHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Embed host demo (Origin-only): http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
