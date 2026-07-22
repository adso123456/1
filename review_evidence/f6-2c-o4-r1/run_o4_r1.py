from __future__ import annotations

import hashlib
import json
import locale
import sys
from pathlib import Path
from typing import Any


RUN_DIRECTORY = Path(r"E:\3\_training_backups\f6-2c-o4-r1-20260722-152015")
VALIDATION_WORKTREE = Path(
    r"E:\3\_validation_worktrees\f6-2c-o4-r1-20260722-152015"
)
RAW_SSE_DIRECTORY = RUN_DIRECTORY / "raw_sse"
EVIDENCE_DIRECTORY = RUN_DIRECTORY / "evidence"
EXPECTED_SHA256 = "c198afde8fa966bbe913b904328508dcf99162a7ef4765f0b8fec8f6ecd6e460"
QUESTION = (
    "\u67e5\u8be2\u5e74\u5ea6pH\u5e74\u5747\u503c"
    "\u6700\u9ad8\u7684\u7ad9\u70b9\u5217\u8868"
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    encoded = QUESTION.encode("utf-8")
    decoded = encoded.decode("utf-8")
    question_sha256 = hashlib.sha256(encoded).hexdigest()
    assert question_sha256 == EXPECTED_SHA256
    assert "?" not in QUESTION
    assert decoded == QUESTION

    write_json(
        EVIDENCE_DIRECTORY / "question-preflight.json",
        {
            "repr": repr(QUESTION),
            "length": len(QUESTION),
            "unicode_code_points": [f"U+{ord(char):04X}" for char in QUESTION],
            "utf8_hex": encoded.hex(),
            "utf8_sha256": question_sha256,
            "contains_question_mark": "?" in QUESTION,
            "round_trip_matches": decoded == QUESTION,
            "sys_stdin_encoding": sys.stdin.encoding,
            "sys_stdout_encoding": sys.stdout.encoding,
            "locale_preferred_encoding": locale.getpreferredencoding(False),
        },
    )

    sys.path.insert(0, str(VALIDATION_WORKTREE))
    import tools.regression_service_harness as harness

    original_urlopen = harness.urllib.request.urlopen
    outbound_call_count = 0

    def audited_urlopen(request: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal outbound_call_count
        outbound_call_count += 1
        if outbound_call_count != 1:
            raise RuntimeError("OUTBOUND_URLOPEN_CALLED_MORE_THAN_ONCE")

        body = request.data
        if not isinstance(body, bytes):
            raise RuntimeError("OUTBOUND_BODY_IS_NOT_BYTES")
        decoded_body = body.decode("utf-8")
        parsed = json.loads(decoded_body)
        message = parsed["message"]
        metadata_query = parsed["metadata"]["query"]
        message_sha256 = hashlib.sha256(message.encode("utf-8")).hexdigest()

        (EVIDENCE_DIRECTORY / "outbound-request-body.bin").write_bytes(body)
        (EVIDENCE_DIRECTORY / "outbound-request-body.utf8.txt").write_text(
            decoded_body,
            encoding="utf-8",
        )
        headers = dict(request.header_items())
        write_json(EVIDENCE_DIRECTORY / "outbound-request-headers.json", headers)
        write_json(
            EVIDENCE_DIRECTORY / "outbound-request-audit.json",
            {
                "urlopen_call_sequence": outbound_call_count,
                "body_length": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_utf8_decode_success": True,
                "message": message,
                "metadata_query": metadata_query,
                "message_utf8_sha256": message_sha256,
                "message_matches_question": message == QUESTION,
                "metadata_query_matches_question": metadata_query == QUESTION,
                "message_contains_question_mark": "?" in message,
                "content_type": request.get_header("Content-type"),
            },
        )

        assert message == QUESTION
        assert metadata_query == QUESTION
        assert "?" not in message
        assert message_sha256 == EXPECTED_SHA256
        return original_urlopen(request, *args, **kwargs)

    harness.urllib.request.urlopen = audited_urlopen
    write_json(
        EVIDENCE_DIRECTORY / "request-count.json",
        {"user_question_request_count": 0, "http_retry_count": 0},
    )
    try:
        result = harness.post_sse(
            QUESTION,
            raw_evidence_dir=RAW_SSE_DIRECTORY,
        )
    finally:
        harness.urllib.request.urlopen = original_urlopen

    if outbound_call_count != 1:
        raise RuntimeError("UNEXPECTED_OUTBOUND_REQUEST_COUNT")
    write_json(EVIDENCE_DIRECTORY / "harness-result.json", result)
    write_json(
        EVIDENCE_DIRECTORY / "request-count.json",
        {
            "user_question_request_count": 1,
            "http_retry_count": 0,
            "urlopen_call_count": outbound_call_count,
        },
    )
    print(
        json.dumps(
            {
                "http_status": result.get("http_status"),
                "event_count": result.get("event_count"),
                "errors": result.get("errors"),
                "answer_present": bool(result.get("answer")),
                "urlopen_call_count": outbound_call_count,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
