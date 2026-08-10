"""进程内日志环形缓冲：设置页"系统日志"读取最近日志，不依赖外部文件。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any


_MAX_RECORDS = 1500


class LogRingBufferHandler(logging.Handler):
    def __init__(self, maxlen: int = _MAX_RECORDS) -> None:
        super().__init__(level=logging.INFO)
        self._records: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item = {
                "ts": round(record.created, 3),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
        except Exception:
            return
        with self._lock:
            self._records.append(item)


_handler: LogRingBufferHandler | None = None


def install_log_buffer(maxlen: int = _MAX_RECORDS) -> None:
    """安装一次根 logger 环形缓冲。幂等。"""
    global _handler
    if _handler is not None:
        return
    _handler = LogRingBufferHandler(maxlen=maxlen)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(_handler)
    # 根 logger 默认 WARNING，会把 INFO 记录挡在缓冲外；放开到 INFO 以捕获完整日志。
    root.setLevel(logging.INFO)


def recent_logs(*, level: str = "INFO", limit: int = 300) -> dict[str, Any]:
    """返回按级别过滤后的最近日志（新在前）。"""
    if _handler is None:
        return {"logs": [], "total": 0}
    levels = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }
    threshold = levels.get(str(level or "").upper(), 20)
    with _handler._lock:
        items = list(_handler._records)
    filtered = [
        item for item in items if levels.get(str(item.get("level")), 0) >= threshold
    ]
    filtered.reverse()
    limited = filtered[: max(1, min(int(limit), 1000))]
    return {"logs": limited, "total": len(filtered)}
