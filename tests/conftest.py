"""pytest 根目录注入 + async 测试原生运行支持。"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_pyfunc_call(pyfuncitem):
    """无需 pytest-asyncio 也能运行 async def 测试。"""
    func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(func):
        return None
    funcargs = getattr(pyfuncitem, "funcargs", {}) or {}
    signature = inspect.signature(func)
    kwargs = {
        name: funcargs[name]
        for name in signature.parameters
        if name in funcargs
    }
    asyncio.run(func(**kwargs))
    return True
