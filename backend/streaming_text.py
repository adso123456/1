"""增量正文过滤：抑制流尾 chart_spec/chart_type 注释。"""

from __future__ import annotations


class ChartAnnotationTailFilter:
    _PREFIXES = ("<!-- chart_spec:", "<!-- chart_type:")
    _MARKER = "<!--"

    def __init__(self) -> None:
        self._pending = ""
        self._suppressing = False

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        output: list[str] = []
        while self._pending:
            if self._suppressing:
                end = self._pending.find("-->")
                if end < 0:
                    self._pending = self._pending[-2:]
                    break
                self._pending = self._pending[end + 3 :]
                self._suppressing = False
                continue

            marker = self._pending.find(self._MARKER)
            if marker < 0:
                keep = min(len(self._MARKER) - 1, len(self._pending))
                emit_upto = len(self._pending) - keep
                output.append(self._pending[:emit_upto])
                self._pending = self._pending[emit_upto:]
                break
            if marker:
                output.append(self._pending[:marker])
                self._pending = self._pending[marker:]
                continue

            if any(prefix.startswith(self._pending) for prefix in self._PREFIXES):
                break
            if any(self._pending.startswith(prefix) for prefix in self._PREFIXES):
                self._pending = self._pending[
                    min(len(prefix) for prefix in self._PREFIXES if self._pending.startswith(prefix)) :
                ]
                self._suppressing = True
                continue
            output.append(self._pending[0])
            self._pending = self._pending[1:]
        return "".join(output)

    def finish(self) -> str:
        if self._suppressing:
            self._pending = ""
            return ""
        value = self._pending
        self._pending = ""
        return value
