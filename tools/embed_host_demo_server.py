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
    EmbedApplicationConfig,
    issue_embed_token,
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


def load_host_demo_config() -> tuple[EmbedApplicationConfig, str]:
    app_id = os.environ.get("WATER_AGENT_HOST_DEMO_APP_ID", "").strip()
    app_secret = os.environ.get(
        "WATER_AGENT_HOST_DEMO_APP_SECRET",
        "",
    ).strip()
    host_origin = os.environ.get(
        "WATER_AGENT_HOST_DEMO_ORIGIN",
        "http://127.0.0.1:5174",
    ).strip()
    source_ids = tuple(
        dict.fromkeys(
            item.strip()
            for item in os.environ.get(
                "WATER_AGENT_HOST_DEMO_ALLOWED_SOURCE_IDS",
                "",
            ).split(",")
            if item.strip()
        )
    )
    try:
        ttl = int(
            os.environ.get(
                "WATER_AGENT_HOST_DEMO_TOKEN_TTL_SECONDS",
                "300",
            )
        )
    except ValueError as exc:
        raise SystemExit("Host Demo Token TTL 必须是整数") from exc
    if not app_id or len(app_secret) < 32 or not source_ids:
        raise SystemExit(
            "缺少 Host Demo app_id、至少 32 字符密钥或数据源配置"
        )
    if ttl < 30 or ttl > 3600:
        raise SystemExit("Host Demo Token TTL 必须在 30～3600 秒之间")
    return (
        EmbedApplicationConfig(
            app_id=app_id,
            app_secret=app_secret,
            enabled=True,
            allowed_origins=(host_origin,),
            allowed_source_ids=source_ids,
            token_ttl_seconds=ttl,
        ),
        host_origin,
    )


def main() -> None:
    args = parse_args()
    config, host_origin = load_host_demo_config()
    if host_origin != f"http://{args.host}:{args.port}":
        raise SystemExit("Host Demo Origin 与监听地址不一致")

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
