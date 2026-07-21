"""验证数据库环境变量、校验和受控 psycopg2 连接参数。"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import build_db_kwargs, validate_db_config


BASE_ENV = {
    "DB_HOST": "localhost",
    "DB_PORT": "5433",
    "DB_NAME": "gt_monitor",
    "DB_USER": "readonly_placeholder",
    "DB_PASSWORD": "password_placeholder",
    "DB_CONNECT_TIMEOUT": "10",
    "DB_STATEMENT_TIMEOUT_MS": "30000",
    "DB_LOCK_TIMEOUT_MS": "5000",
}


def raises_value_error(callback) -> tuple[bool, str]:
    try:
        callback()
    except ValueError as exc:
        return True, str(exc)
    return False, "未抛出 ValueError"


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    source = (PROJECT_ROOT / "config" / "settings.py").read_text(encoding="utf-8")
    hardcode_removed = 'user="postgres"' not in source and "test123456" not in source
    results.append(("数据库账号密码硬编码已移除", hardcode_removed, "源码静态检查"))

    for key in ("DB_USER", "DB_PASSWORD"):
        environ = dict(BASE_ENV)
        environ.pop(key)
        config = build_db_kwargs(environ)
        raised, message = raises_value_error(lambda: validate_db_config(config))
        results.append((f"缺少 {key} 时校验失败", raised and key in message, message))

    invalid_integer_checks = []
    for key in (
        "DB_PORT",
        "DB_CONNECT_TIMEOUT",
        "DB_STATEMENT_TIMEOUT_MS",
        "DB_LOCK_TIMEOUT_MS",
    ):
        for value in ("0", "-1", "not-an-integer"):
            environ = dict(BASE_ENV)
            environ[key] = value
            raised, message = raises_value_error(lambda env=environ: build_db_kwargs(env))
            invalid_integer_checks.append(raised and key in message)
    results.append(
        (
            "端口和超时严格解析为正整数",
            all(invalid_integer_checks),
            f"checks={len(invalid_integer_checks)}",
        )
    )

    config = build_db_kwargs(BASE_ENV)
    expected_options = (
        "-c default_transaction_read_only=on "
        "-c statement_timeout=30000 "
        "-c lock_timeout=5000"
    )
    options_ok = (
        config.get("options") == expected_options
        and config.get("connect_timeout") == 10
        and config.get("application_name") == "vanna-water-agent"
        and "sslmode" not in config
    )
    results.append(("连接参数包含受控只读和超时配置", options_ok, str(config)))

    ssl_config = build_db_kwargs({**BASE_ENV, "DB_SSLMODE": "require"})
    results.append(
        (
            "sslmode 仅在环境变量明确提供时加入",
            ssl_config.get("sslmode") == "require",
            str(ssl_config.get("sslmode")),
        )
    )

    injected_env = {
        **BASE_ENV,
        "DB_STATEMENT_TIMEOUT_MS": "1000 -c default_transaction_read_only=off",
        "DB_OPTIONS": "-c default_transaction_read_only=off",
    }
    raised, message = raises_value_error(lambda: build_db_kwargs(injected_env))
    ignored_options = build_db_kwargs({**BASE_ENV, "DB_OPTIONS": "malicious"})
    injection_blocked = raised and ignored_options.get("options") == expected_options
    results.append(("用户输入不能注入 options", injection_blocked, message))

    secret = "secret-that-must-not-leak"
    output = io.StringIO()
    config_without_user = build_db_kwargs(
        {**BASE_ENV, "DB_USER": "", "DB_PASSWORD": secret}
    )
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        raised, message = raises_value_error(
            lambda: validate_db_config(config_without_user)
        )
    no_secret_leak = raised and secret not in message and secret not in output.getvalue()
    results.append(("日志和异常不泄露密码", no_secret_leak, message))

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
