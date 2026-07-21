from __future__ import annotations

import csv
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"


def query_result_files(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(item.relative_to(path)) for item in path.rglob("query_results_*.csv")}


def port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def get_deepseek_key() -> tuple[str, str]:
    value = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value, "process environment"
    if os.name == "nt":
        import winreg

        locations = [
            (winreg.HKEY_CURRENT_USER, "Environment", "user environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                "machine environment",
            ),
        ]
        for hive, subkey, source in locations:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "DEEPSEEK_API_KEY")
                if str(value).strip():
                    return str(value).strip(), source
            except OSError:
                pass
    return "", "not found"


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    return text


def start_server(
    data_dir: Path, agent_dir: Path, disable_examples: bool
) -> tuple[subprocess.Popen[str], list[str], str, str]:
    if port_open():
        raise RuntimeError("端口 8000 已被占用，无法保证隔离运行")
    key, key_source = get_deepseek_key()
    if not key:
        raise RuntimeError("既有环境中没有 DEEPSEEK_API_KEY")
    for name in ("DB_USER", "DB_PASSWORD"):
        if not os.getenv(name, "").strip():
            raise RuntimeError(f"当前子进程环境缺少 {name}")

    env = os.environ.copy()
    env.update(
        {
            "DEEPSEEK_API_KEY": key,
            "VANNA_DATA_DIR": str(data_dir),
            "AGENT_DATA_DIR": str(agent_dir),
            "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "1" if disable_examples else "0",
            "HF_HUB_OFFLINE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    process = subprocess.Popen(
        [str(PYTHON_EXE), "step4_server.py"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    logs: list[str] = []

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logs.append(line.rstrip())

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务提前退出：\n" + "\n".join(logs[-50:]))
        try:
            with urllib.request.urlopen(SERVER_URL + "/health", timeout=3) as response:
                if response.status == 200:
                    return process, logs, key_source, key
        except Exception:
            pass
        time.sleep(2)
    stop_server(process)
    raise RuntimeError("等待 /health 超时")


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def walk_json(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    found = [(path, value)]
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(walk_json(child, path + (str(key),)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(walk_json(child, path + (str(index),)))
    return found


def post_sse(query: str, timeout: int = 240) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": query,
            "conversation_id": None,
            "request_id": None,
            "metadata": {"query": query, "f2_probe": True},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        SERVER_URL + "/api/vanna/v2/chat_sse",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    result: dict[str, Any] = {
        "http_status": None,
        "event_count": 0,
        "errors": [],
        "sql": "",
        "answer": "",
        "rich_types": [],
        "event_rows": [],
        "event_columns": [],
    }
    answer_parts: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result["http_status"] = response.status
            while True:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                result["event_count"] += 1
                if event.get("type") == "error":
                    result["errors"].append(str(event.get("data") or event))
                for path, value in walk_json(event):
                    key = path[-1].lower() if path else ""
                    if key == "sql" and isinstance(value, str) and value.strip():
                        result["sql"] = value.strip()
                    elif key in {"type", "component_type"} and isinstance(value, str):
                        if "rich" in "/".join(path).lower() or value in {"dataframe", "chart", "plotly"}:
                            result["rich_types"].append(value)
                    elif key == "rows" and isinstance(value, list) and value:
                        result["event_rows"] = value
                    elif key == "columns" and isinstance(value, list) and value:
                        result["event_columns"] = value
                    elif key in {"text", "content"} and isinstance(value, str) and value.strip():
                        if not any(marker in "/".join(path).lower() for marker in ("memory", "prompt")):
                            answer_parts.append(value)
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["errors"].append(exc.read(2000).decode("utf-8", errors="replace"))
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["answer"] = "\n".join(dict.fromkeys(answer_parts))[-12000:]
    return result


def extract_chart_specs(answer: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for raw in re.findall(r"<!--\s*chart_spec:\s*([\s\S]*?)\s*-->", answer, flags=re.I):
        try:
            item = json.loads(raw)
            if isinstance(item, dict):
                specs.append(item)
        except json.JSONDecodeError:
            specs.append({"parse_error": raw[:200]})
    return specs


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return [], []
    return rows[0], [row for row in rows[1:] if any(value.strip() for value in row)]
