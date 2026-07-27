"""本地 5174 宿主页与短期嵌入 Token 签发服务。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.embed_access import (  # noqa: E402
    EmbedAccessError,
    issue_embed_token,
    load_embed_application_config,
)


def tamper_token(token: str) -> str:
    header, payload, signature = token.split(".")
    changed = ("A" if signature[0] != "A" else "B") + signature[1:]
    return ".".join((header, payload, changed))


class EmbedHostDemoHandler(SimpleHTTPRequestHandler):
    server_version = "WaterAgentEmbedHost/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/embed-token":
            self._serve_embed_token()
            return
        super().do_GET()

    def _serve_embed_token(self) -> None:
        config = self.server.embed_config  # type: ignore[attr-defined]
        token_mode = self.server.token_mode  # type: ignore[attr-defined]
        host_origin = self.server.host_origin  # type: ignore[attr-defined]
        if token_mode == "fail":
            self._json_response(
                503,
                {"error": "本地演示 Token 签发已禁用"},
            )
            return
        try:
            token, expires_at = issue_embed_token(
                config,
                subject="local-demo-user",
                parent_origin=host_origin,
                allowed_source_ids=(
                    ()
                    if token_mode == "no-sources"
                    else config.allowed_source_ids
                ),
            )
        except EmbedAccessError as exc:
            self._json_response(
                exc.status_code,
                {"error": exc.safe_message},
            )
            return
        if token_mode == "tampered":
            token = tamper_token(token)
        self._json_response(
            200,
            {"token": token, "expires_at": expires_at},
        )

    def _json_response(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # 只记录路径和状态；响应体中的 Token 不进入日志。
        super().log_message(format, *args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5174)
    parser.add_argument(
        "--token-mode",
        choices=("valid", "fail", "tampered", "no-sources"),
        default="valid",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_embed_application_config()
    if config is None:
        raise SystemExit("缺少嵌入应用环境变量，宿主 Demo 拒绝启动")
    host_origin = os.environ.get(
        "WATER_AGENT_EMBED_HOST_ORIGIN",
        f"http://{args.host}:{args.port}",
    ).strip()
    if host_origin not in config.allowed_origins:
        raise SystemExit("宿主 Origin 不在嵌入应用白名单中")

    directory = PROJECT_ROOT / "frontend" / "embed-host-demo"
    handler = partial(EmbedHostDemoHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.embed_config = config  # type: ignore[attr-defined]
    server.host_origin = host_origin  # type: ignore[attr-defined]
    server.token_mode = args.token_mode  # type: ignore[attr-defined]
    print(f"Embed host demo: {host_origin}")
    server.serve_forever()


if __name__ == "__main__":
    main()
