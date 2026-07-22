# Exact source excerpt used to construct the O4 HTTP request.

def post_sse(
    query: str, timeout: int = 240, raw_evidence_dir: Path | None = None
) -> dict[str, Any]:
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
