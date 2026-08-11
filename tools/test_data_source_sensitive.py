"""共享敏感字段判定与发布值形态兜底回归。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_sensitive import (
    contains_sensitive_typical_value,
    is_sensitive_column,
)


def main() -> int:
    sensitive_columns = (
        ("contact_number", "责任人联系电话"),
        ("stationContactNumber", ""),
        ("contact_person", "联系人"),
        ("responsible_person", "站点责任人"),
        ("station_responsible_person", ""),
        ("legal_representative", "法定代表人"),
        ("real_name", "真实姓名"),
        ("user_real_name", ""),
        ("telephone", "联系电话"),
        ("tel", "电话"),
        ("fax", "传真"),
        ("contact", "联系人"),
        ("mobile", "手机号"),
        ("email", "邮箱"),
        ("id_card", "身份证"),
        ("home", "住址"),
    )
    assert all(is_sensitive_column(name, comment) for name, comment in sensitive_columns)

    normal_columns = (
        ("station_name", "监测站点名称"),
        ("area_name", "行政区名称"),
        ("monitor_number", "监测数量"),
        ("personnel_count", "人员数量"),
    )
    assert not any(is_sensitive_column(name, comment) for name, comment in normal_columns)

    assert contains_sensitive_typical_value(["13800000000"], "varchar(20)")
    assert contains_sensitive_typical_value(["water@example.com"], "varchar(100)")
    assert contains_sensitive_typical_value(["11010519491231002X"], "char(18)")
    assert not contains_sensitive_typical_value([13800000000.0], "double(20,8)")
    assert not contains_sensitive_typical_value(["42010000000"], "bigint")
    print("data source sensitive classifier tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
