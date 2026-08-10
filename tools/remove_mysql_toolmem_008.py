"""从 revision-3 正式 Chroma 删除 MYSQL_HYDRO_TREND_008 记录。

该样例的最新有效日流速与流量均为零，不再作为推荐问题。
用法：python tools/remove_mysql_toolmem_008.py [项目根目录]
默认项目根目录为 /opt/water-agent（容器内）。
"""

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/water-agent").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.memory import create_memory


MEM_DIR = (
    ROOT
    / "agent_data"
    / "mysql-lzh-monitor"
    / "mysql-lzh-monitor.revision-3-2-1785398545932380700-73407a36"
)
RECORD_ID = "toolmem-v1-d0b44e50e18085a244c8cd5d01287b4ca4d4eac3cbe4af685bc99d98db7abcd1"
SAMPLE_ID = "MYSQL_HYDRO_TREND_008"


def _close_memory(memory) -> None:
    try:
        memory._executor.shutdown(wait=True)
    except Exception:
        pass
    try:
        if memory._client is not None:
            memory._client._system.stop()
    except Exception:
        pass
    memory._collection = None
    memory._client = None


def main() -> None:
    memory = create_memory(MEM_DIR)
    collection = memory._get_collection()
    hits = collection.get(where={"created_from_sample_id": SAMPLE_ID})
    print("before count:", collection.count(), "hits:", hits["ids"])
    if RECORD_ID in (hits["ids"] or []):
        collection.delete(ids=[RECORD_ID])
        print("deleted:", RECORD_ID)
    else:
        print("record not found, skip")
    after = collection.count()
    print("after count:", after)
    _close_memory(memory)

    identity_path = MEM_DIR / ".asset_identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["memory_count"] = after
    identity_path.write_text(
        json.dumps(identity, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest_path = ROOT / "agent_data" / "mysql-lzh-monitor" / "asset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["memory_identity_hash"] = hashlib.sha256(
        identity_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("identity memory_count:", identity["memory_count"])
    print("manifest memory_identity_hash:", manifest["memory_identity_hash"])


if __name__ == "__main__":
    main()
