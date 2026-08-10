"""表准入审核阶段 B：确定性评分 + 同业务表分组。

本模块只生成建议字段：
  proposed_decision / proposed_score / proposed_reason
  business_group / group_confidence / compared_tables_json / group_reason

严格遵守阶段 B 边界：
  不修改 effective_decision；
  不覆盖 selected_scope；
  不调用 prepare()；
  不生成正式 Metadata / DDL / Chroma；
  不增加 runtime_revision。

评分结果不能直接决定正式范围。决策契约（冻结版）：
- 每张表独立评分、独立判定；业务组只描述关系，不再 winner-takes-all；
- 组内唯一允许自动降级的是确定性重复证据（duplicate_structure /
  backup_mirror）；obsolete 只提示、不决策；
- update_interval 未知按中性计分；非时序表时间维度全部 N/A-neutral；
- confirmed_empty -> standby；数据状态未知 -> pending；
- 高置信非业务表（system_log / platform_config / media_asset /
  model_artifact / operation_trace）至少两类独立证据才可排除候选，
  排除候选落 standby（proposed 词汇不新增 exclude），业务反证可覆盖。
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# 阈值（与评审方案一致，可通过环境变量微调）
# ---------------------------------------------------------------------------

ACTIVE_MIN_SCORE = float(os.getenv("DATA_SOURCE_ACTIVE_MIN_SCORE", "80"))
PENDING_MIN_SCORE = float(os.getenv("DATA_SOURCE_PENDING_MIN_SCORE", "60"))
GROUP_MIN_GAP = float(os.getenv("DATA_SOURCE_GROUP_MIN_GAP", "5"))
GROUP_CONFIDENCE_THRESHOLD = float(
    os.getenv("DATA_SOURCE_GROUP_CONFIDENCE_THRESHOLD", "0.55")
)
MIN_CONFIDENCE_FOR_ACTIVE = float(
    os.getenv("DATA_SOURCE_MIN_CONFIDENCE_FOR_ACTIVE", "0.55")
)

# 时间类数据表：缺少最新数据时间/可用键属于关键指标未知，不能建议 active。
_TIME_DATA_ROLES = {"事实表", "日志表"}
# 静态表：允许没有最新数据时间，也不按新鲜度重罚。
_STATIC_ROLES = {"字典表", "配置表"}

# 明显备份/临时/历史标记：只扣分，不直接 blocked（旧表恢复写入后仍参与重评）。
_BACKUP_MARKS_CN = ("历史", "备份", "临时", "旧")
_BACKUP_MARKS_EN = ("old", "backup", "copy", "tmp", "bak")

_HISTORY_MARKS = ("log", "日志", "history", "历史", "audit", "流水")

# 非业务高置信类别 taxonomy：语义信号（表名/注释）+ 列结构信号。
# 判定需要两族独立证据才能达到 0.95；纯前缀/单关键词最高 0.55。
_NON_BUSINESS_TAXONOMY: dict[str, dict[str, tuple[str, ...]]] = {
    "system_log": {
        "semantic": (
            "login_log", "oper_log", "audit_log", "system log",
            "登录日志", "操作日志", "系统日志",
        ),
        "columns": (
            "user", "operator", "ip", "request_uri", "method",
            "module", "action", "browser", "os", "login_time",
            # 平台族稳定结构（真实画像跨表共现）：row_id/client_ip。
            "row_id", "client_ip",
        ),
    },
    "platform_config": {
        "semantic": (
            "route", "menu", "role", "permission", "oauth",
            "platform config", "路由", "菜单", "角色", "权限",
        ),
        "columns": (
            "path", "component", "permission", "role_id", "menu_id",
            "parent_id", "client_id", "redirect_uri",
            # 平台族稳定结构：row_id/route_name/route_id/uri 跨表共现。
            "row_id", "route_name", "route_id", "uri",
        ),
    },
    "media_asset": {
        "semantic": (
            "camera", "raster", "panorama", "uav", "picture",
            "spectrum", "影像", "栅格", "相机", "无人机", "全景",
        ),
        "columns": (
            "file", "url", "path", "image", "video", "thumbnail",
            "width", "height", "resolution", "tile", "layer",
            # 无人机/摄像头/全景真实画像稳定结构（drone_*/gateway_* 7 表共现）。
            "drone_sn", "drone_callsign", "drone_camera_list",
            "drone_device_model", "drone_mode_code",
            "gateway_sn", "gateway_callsign", "gateway_camera_list",
            "gateway_device_model", "gateway_mode_code",
            "device_id", "lon", "lat", "layer_id", "panorama", "station_id",
        ),
    },
    "model_artifact": {
        "semantic": (
            "model", "lasso", "algorithm", "prediction", "inversion",
            "模型", "算法", "预测", "反演",
        ),
        "columns": (
            "model_id", "algorithm", "parameter", "weight",
            "coefficient", "score", "run_id", "version",
            # EFDC/模型输出真实画像稳定结构（39 张表共现 efdc_i/efdc_j 等）。
            "efdc_i", "efdc_j", "result_type", "time_slot",
            "res_date", "hour", "model_name", "key_point_id",
            "river_name", "station_id", "cod", "chl",
            "input_name", "output_name",
        ),
    },
    "operation_trace": {
        "semantic": (
            "track", "trajectory", "patrol", "graphic",
            "轨迹", "巡检", "图形",
        ),
        "columns": (
            "track_id", "operator", "task_id", "path",
            "geometry", "lon", "lat", "start", "end",
            # 图形/操作轨迹稳定结构（entity_type 跨表共现）。
            "entity_type", "operate_type", "operate_time",
        ),
    },
    "identity_platform": {
        "semantic": (
            "user", "group", "role", "permission", "auth", "oauth",
            "account", "menu", "login",
        ),
        "columns": (
            "user_id", "group_id", "role_id", "permission_id",
            "account_id", "client_id", "username", "password",
            "menu_id", "parent_id", "redirect_uri",
            # 平台身份族稳定结构：row_id/role_name 跨表共现。
            "row_id", "role_name", "role_description",
        ),
        "max_confidence": 0.95,
    },
    "metadata_registry": {
        "semantic": (
            "metadata", "table_core", "data_field", "field_metadata",
            "schema_metadata", "元数据",
        ),
        "columns": (
            "table_name", "field_name", "column_name", "data_type",
            "metadata_id", "category_id", "field_type", "schema_name",
            # 元数据注册真实画像稳定结构（aliasname/layername 等 7+ 表共现）。
            "tablename", "fieldname", "aliasname", "authoritycode",
            "layername", "scale", "server", "xmin", "xmax",
            "ymin", "ymax",
        ),
        "max_confidence": 0.95,
    },
    # 中置信类别：语义 + 支撑结构只能到 0.75，不得声明 deterministic 排除。
    "workflow_support": {
        "semantic": (
            "task", "plan", "doc", "document", "survey",
            "offline", "upload", "download",
        ),
        "columns": (
            "task_id", "plan_id", "doc_id", "file_id",
            "status", "owner", "approver", "process_id",
            "task_name", "task_code", "task_type", "plan_name",
            "project_id", "project_name", "doc_name",
            "upload_id", "sync_id",
        ),
        "max_confidence": 0.75,
    },
    "infrastructure_reference": {
        "semantic": (
            "device", "equipment", "facility", "directory",
            "设备", "目录",
        ),
        "columns": (
            "device_id", "device_name", "equipment_id", "facility_id",
            "ip", "port", "device_code", "device_type",
            "device_status", "dict_type", "service_name",
        ),
        "max_confidence": 0.75,
    },
    "location_reference": {
        "semantic": (
            "address", "area", "city", "province", "street",
            "district", "行政区", "地址",
        ),
        "columns": (
            "area_code", "city_code", "province_code", "street_code",
            "address", "zipcode", "lng", "lat",
            "area_name", "city_name", "province_name",
            "street_name", "district_name",
        ),
        "max_confidence": 0.75,
    },
    "out_of_domain_candidate": {
        "semantic": (
            "gdp", "population", "economic", "economy",
            "经济", "人口", "财政",
        ),
        "columns": (
            "gdp", "population", "economy_value", "region_code",
            "stat_year",
        ),
        "max_confidence": 0.75,
    },
}

# 业务反证词：命中即不能仅凭非业务证据达到 0.9（防止误杀业务域 records/info）。
_BUSINESS_COUNTER_WORDS = (
    "waterquality", "hydrological", "meteorological", "pollutant",
    "outlet", "station", "ecology", "enterprise", "river", "lake",
    "section", "warn", "emission", "aquatic",
    "监测", "水质", "水文", "污染物", "排污口", "生态", "企业",
    "流域", "断面", "预警", "站点", "水生态",
)

# 职责/粒度标记：同组两表若职责或粒度不同，禁止判为结构重复。
_ROLE_MARKERS = (
    "info", "records", "standard", "threshold", "waterlevel",
    "setting", "year", "month", "day", "hour",
)

# 物理分片表：数字后缀 + 同 family >=3 张 + 结构指纹一致 + 存在统一入口。
_PHYSICAL_SHARD_RE = re.compile(r"^(?P<family>.+?)_(?P<num>\d+)$")

# 审计类列：全空不影响业务判断，不计入"大量空值"。
_AUDIT_COLUMN_MARKS = (
    "create_by", "created_by", "create_time", "created_at",
    "update_by", "updated_by", "update_time", "updated_at",
    "modify_by", "modify_time", "delete_flag", "is_deleted",
    "del_flag", "deleted_at",
)

# 时间粒度标记：同一业务组内出现多种粒度（日/时/月/年）视为需人工确认。
_GRANULARITY_MARKS = (
    "minute", "hour", "day", "month", "year", "旬",
    "分钟", "小时", "日报", "月报", "年报",
)


def _normalize_name(name: str) -> str:
    """表名归一化：去符号、去技术前缀/版本号/备份后缀，用于相似度比较。"""
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(name).lower())
    text = re.sub(r"^(t|tb|tbl|v)", "", text)
    text = re.sub(r"v?\d+$", "", text)
    text = re.sub(r"(bak|backup|copy|old|tmp)$", "", text)
    return text


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    previous = list(range(len(a) + 1))
    for index, char_b in enumerate(b, start=1):
        current = [index]
        for j in range(1, len(a) + 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (a[j - 1] != char_b),
                )
            )
        previous = current
    return previous[len(a)]


def _name_similarity(a: str, b: str) -> float:
    """表名相似度：0.0-1.0。相等 1.0，包含 0.7，编辑距离接近 0.5/0.3。"""
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return 0.7
    distance = _levenshtein(na, nb)
    if distance <= 2:
        return 0.55
    if distance <= max(len(na), len(nb)) * 0.25:
        return 0.35
    return 0.0


def _column_names(profile: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in profile.get("columns") or []:
        name = str(item.get("column") or "")
        if name:
            names.add(name.lower())
    return names


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _time_signal(left_time: str, right_time: str) -> float:
    if not left_time or not right_time:
        return 0.0
    if _normalize_name(left_time) == _normalize_name(right_time):
        return 1.0
    return 0.3


def _is_backup_mark(table_name: str, table_comment: str) -> bool:
    text = f"{table_name} {table_comment}".lower()
    if any(mark in text for mark in _BACKUP_MARKS_CN):
        return True
    for mark in _BACKUP_MARKS_EN:
        if re.search(rf"(?<![a-z0-9]){re.escape(mark)}(?![a-z0-9])", text):
            return True
    return False


def _is_history_like(table_name: str, role: str) -> bool:
    if role == "日志表":
        return True
    text = str(table_name).lower()
    return any(mark in text for mark in _HISTORY_MARKS)


# 审计/技术时间字段：不作为时序证据。
_AUDIT_TIME_COLUMNS = (
    "create_time", "created_at", "created_time",
    "update_time", "updated_at", "updated_time",
    "modify_time", "modified_at", "modified_time",
    "delete_time", "deleted_at",
    "import_time", "ingest_time", "sync_time",
)
# 时间字段词法边界（冻结契约）：
#   1. _looks_time_column：token 级时间类型识别，禁止 "date" in name 这类任意子串；
#   2. _is_audit_time_column：审计时间排除（update_time/created_at/sync_time 等）；
#   3. _business_time_column：业务观测时间（monitor_time/monitor_year/sampling_time 等）。
# 最终用于 is_time_series_like 的只能是 business_time_column。
_TIME_TYPE_TOKENS = (
    "date", "time", "datetime", "timestamp",
    "year", "month", "day", "hour", "at", "on",
)
# 中文无分隔符，使用明确的双字时间标记。
_TIME_TYPE_CN = ("时间", "日期", "年月", "年份", "月份")
# 业务观测时间前缀 token：时间类型 token + 观测前缀才构成业务观测时间。
_BUSINESS_TIME_PREFIX_TOKENS = (
    "monitor", "monitoring", "sampling", "sample", "measure",
    "measurement", "observe", "observation", "record", "report",
    "stat", "collect", "collection", "data",
)
_BUSINESS_TIME_CN = (
    "监测时间", "采样时间", "观测时间", "数据时间", "记录时间",
    "测量时间", "监测日期", "采样日期", "观测日期",
)
_OBJECT_MARKS = ("station", "section", "site", "断面", "站点", "测站")
_VALUE_MARKS = ("value", "val", "浓度", "流量", "水位", "雨量", "温度", "指标", "值")


def _tokenize_column_name(column: str) -> list[str]:
    """snake_case / camelCase / 非字母数字边界切分为独立 token。"""
    text = str(column or "")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    parts = re.split(r"[^0-9a-z\u4e00-\u9fff]+", text.lower())
    return [part for part in parts if part]


def _is_audit_time_column(column: str) -> bool:
    name = str(column or "").lower()
    return any(mark in name for mark in _AUDIT_TIME_COLUMNS)


def _looks_time_column(column: str) -> bool:
    """token 级时间类型识别：时间词必须是独立 token。

    update_by / candidate_id / validated_by 等 token 不含时间词，返回 False；
    monitor_year / stat_date / created_at(_at 后缀) 等返回 True。
    """
    name = str(column or "")
    if any(mark in name for mark in _TIME_TYPE_CN):
        return True
    tokens = _tokenize_column_name(name)
    return any(token in _TIME_TYPE_TOKENS for token in tokens)


def _business_time_column(column: str) -> bool:
    """业务观测时间：时间类型 + 业务观测前缀（monitor/sampling/stat 等）。

    create_time / updated_at / sync_time 等审计或纯时间字段不满足，不能作为时序证据。
    """
    if _is_audit_time_column(column):
        return False
    name = str(column or "")
    if any(mark in name for mark in _BUSINESS_TIME_CN):
        return True
    tokens = _tokenize_column_name(name)
    if not any(token in _TIME_TYPE_TOKENS for token in tokens):
        return False
    return any(token in _BUSINESS_TIME_PREFIX_TOKENS for token in tokens)


def _is_time_series_like(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> bool:
    """时序型判定（契约 v1 + 词法边界修复）：只有业务观测时间列才能作为时序证据。

    update_by/created_by 等 actor 字段、审计时间列、普通 date/time 字段
    均不作为业务时序证据。
    """
    columns = [str(item.get("column") or "") for item in (profile.get("columns") or [])]
    return any(_business_time_column(column) for column in columns)


def _business_time_series_strong(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> bool:
    """明确业务时序证据（契约 4 修订）：非审计业务时间列 + 业务结构/领域证据。"""
    columns = [
        str(item.get("column") or "")
        for item in (profile.get("columns") or [])
    ]
    business_time = [
        column for column in columns if _business_time_column(column)
    ]
    if not business_time:
        return False
    text = (
        f"{profile.get('table') or ''} "
        f"{quality.get('table_comment') or profile.get('table_comment') or ''}"
    ).lower()
    object_hit = any(
        mark in column.lower()
        for column in columns
        for mark in (
            "station", "section", "outlet", "enterprise", "pollutant",
            "monitor", "采样点", "站点", "断面", "排口", "企业", "污染物",
        )
    )
    value_hit = any(
        mark in column.lower()
        for column in columns
        for mark in (
            "value", "concentration", "flow", "level", "indicator",
            "state", "值", "浓度", "流量", "水位", "指标", "状态",
        )
    )
    if object_hit and value_hit:
        return True
    return any(
        mark in text
        for mark in (
            "waterquality", "hydrological", "meteorological", "pollutant",
            "outlet", "ecology", "enterprise", "sampling",
            "水质", "水文", "气象", "污染物", "排污", "生态", "采样", "监测",
        )
    )


def _role_markers(table_name: str) -> set[str]:
    lowered = str(table_name).lower()
    return {mark for mark in _ROLE_MARKERS if mark in lowered}


def _has_role_conflict(left_name: str, right_name: str) -> bool:
    """职责/粒度差异：任一方命中不同职责标记即禁止判为结构重复。"""
    left_marks = _role_markers(left_name)
    right_marks = _role_markers(right_name)
    if not left_marks or not right_marks:
        return False
    return left_marks != right_marks


def _physical_shard_evidence(
    profiles: list[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    """物理分片识别：满足冻结契约五条件 -> confidence 0.95。

    1. 表名存在明确数字分片后缀；
    2. 同 schema 同 family 数字分表 >= 3 张；
    3. 分表 structure_fingerprint 完全一致（不要求 data_fingerprint）；
    4. family 存在非数字统一入口（*_hour/_day/_month/_records 等）；
    5. 数字后缀无独立业务语义（模型/备份等其他 evidence 另行处理）。
    只提示不降级：1~2 张、结构不一致、无统一入口。
    """
    profiles_by_table: dict[str, Mapping[str, Any]] = {}
    for profile in profiles:
        table = str(profile.get("table") or "")
        if table:
            profiles_by_table[table] = profile
    digit_families: dict[str, list[str]] = defaultdict(list)
    for table in profiles_by_table:
        match = _PHYSICAL_SHARD_RE.match(table)
        if match and match.group("num").isdigit():
            digit_families[match.group("family")].append(table)
    evidence: dict[tuple[str, str], float] = {}
    for family, shards in digit_families.items():
        if len(shards) == 1:
            continue
        base = re.sub(r"_records$", "", family)
        unified = [
            table
            for table in profiles_by_table
            if table not in shards
            and table.startswith(base)
            and not _PHYSICAL_SHARD_RE.match(table)
        ]
        if not unified:
            continue
        fingerprints = {
            str(
                (profiles_by_table[table].get("quality") or {}).get(
                    "structure_fingerprint"
                )
                or ""
            )
            for table in shards
        }
        if not fingerprints or "" in fingerprints or len(fingerprints) > 1:
            continue
        if len(shards) == 2:
            # sibling==2：必须无职责/粒度差异，且结构指纹完全一致。
            if _has_role_conflict(shards[0], shards[1]):
                continue
        for table in shards:
            profile = profiles_by_table[table]
            key = (str(profile.get("schema") or ""), table)
            evidence[key] = 0.95
    return evidence


def classify_non_business_evidence(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> dict[str, Any]:
    """非业务高置信排除层（独立于 _infer_role）。

    返回 {role, confidence, semantic_hits, column_hits, business_counter}。
    置信规则（冻结契约，简单双证据）：
      表名/注释语义 + >=2 个类别结构列 -> 0.95；
      只有表名/注释语义（无结构证据）-> <=0.55（不自动降级）；
      仅列结构、无语义 -> <=0.55（不自动降级）；
      业务反证命中 -> 置信封顶 0.75（最多 standby/pending，不排除）。
    """
    table = str(profile.get("table") or "")
    comment = str(
        quality.get("table_comment") or profile.get("table_comment") or ""
    )
    semantic_text = f"{table} {comment}".lower()
    columns = _column_names(profile)
    business_counter = any(word in semantic_text for word in _BUSINESS_COUNTER_WORDS)

    best: dict[str, Any] = {
        "role": "",
        "confidence": 0.0,
        "semantic_hits": [],
        "column_hits": [],
        "business_counter": business_counter,
    }
    for role, spec in _NON_BUSINESS_TAXONOMY.items():
        max_confidence = float(spec.get("max_confidence", 0.95))
        semantic_hits = [word for word in spec["semantic"] if word in semantic_text]
        column_hits = [
            signal
            for signal in spec["columns"]
            if any(signal in name for name in columns)
        ]
        has_semantic = bool(semantic_hits)
        has_columns = len(column_hits) >= 2
        if max_confidence <= 0.75:
            # 中置信类别：必须语义 + 结构两族证据，封顶 0.75，
            # 避免仅凭 task/device/address 等单词或单列结构误杀业务表。
            if not (has_semantic and has_columns):
                continue
            confidence = 0.75
        elif has_semantic and has_columns:
            # 语义 + 结构两类独立证据：模型产物/平台身份等可确定性压 standby。
            confidence = 0.95
        elif has_semantic or has_columns:
            # 只有表名语义或仅列结构：<=0.55，不自动降级，
            # 避免普通业务表名恰好含 model/task 等词被自动压掉。
            confidence = 0.55
        else:
            continue
        confidence = min(confidence, max_confidence)
        if business_counter:
            confidence = min(confidence, 0.75)
        if confidence > best["confidence"]:
            best = {
                "role": role,
                "confidence": round(confidence, 2),
                "semantic_hits": semantic_hits,
                "column_hits": column_hits,
                "business_counter": business_counter,
            }
    return best


def _mostly_null_business_ratio(profile: Mapping[str, Any]) -> float:
    """业务列中空值率 >= 0.8 的比例（排除审计列）。

    监测表常含大量可选参数列（bod/flow 等按指标为空），
    用"多数业务列整体为空"而不是全表单元格空值率，避免误伤。"""
    columns = profile.get("columns") or []
    business = [
        column
        for column in columns
        if not any(
            mark in str(column.get("column") or "").lower()
            for mark in _AUDIT_COLUMN_MARKS
        )
    ]
    if not business:
        return 0.0
    mostly_null = [
        column
        for column in business
        if (column.get("sample_null_rate") or 0) >= 0.8
    ]
    return len(mostly_null) / len(business)


def group_tables(
    profiles: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把表达同一业务对象的表分组（L1 候选 + L2 确认）。

    只使用受限画像中的结构信息（列集合、时间列、角色、粒度），
    不读取原始样本值。返回的组带有置信度与说明。
    """
    items: list[dict[str, Any]] = []
    for profile in profiles:
        schema = str(profile.get("schema") or "")
        table = str(profile.get("table") or "")
        if not schema or not table:
            continue
        items.append(
            {
                "key": (schema, table),
                "name": table,
                "columns": _column_names(profile),
                "time_column": str(profile.get("time_column_candidate") or ""),
                "role": str(profile.get("table_role_candidate") or ""),
                "grain": str(profile.get("grain_candidate") or ""),
            }
        )

    parents = list(range(len(items)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parents[root_right] = root_left

    pair_scores: dict[tuple[int, int], dict[str, float]] = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            left, right = items[i], items[j]
            if not left["columns"] or not right["columns"]:
                continue
            name_sim = _name_similarity(left["name"], right["name"])
            jaccard = _jaccard(left["columns"], right["columns"])
            time_sig = _time_signal(left["time_column"], right["time_column"])
            pair_conf = round(
                min(1.0, 0.4 * name_sim + 0.4 * jaccard + 0.2 * time_sig),
                3,
            )
            # 只用强边建组，避免弱链接把大量无关表连成巨型组件，
            # 再反过来稀释组内置信度。
            # 强边条件：同名/版本族（允许低字段重合）、
            # 包含关系且字段重合足够、或字段结构高度一致。
            strong_edge = (
                (name_sim == 1.0 and jaccard >= 0.15)
                or (name_sim >= 0.7 and jaccard >= 0.35)
                or (jaccard >= 0.7 and time_sig >= 0.3)
            )
            if strong_edge and pair_conf >= GROUP_CONFIDENCE_THRESHOLD:
                pair_scores[(i, j)] = {
                    "confidence": pair_conf,
                    "jaccard": jaccard,
                }
                union(i, j)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        components[find(index)].append(index)

    groups: list[dict[str, Any]] = []
    for indexes in components.values():
        if len(indexes) < 2:
            continue
        member_keys = [items[index]["key"] for index in indexes]
        confidences = [
            pair_scores[(i, j)]["confidence"]
            for i in indexes
            for j in indexes
            if i < j and (i, j) in pair_scores
        ]
        jaccards = [
            pair_scores[(i, j)]["jaccard"]
            for i in indexes
            for j in indexes
            if i < j and (i, j) in pair_scores
        ]
        if not confidences:
            continue
        confidence = round(min(confidences), 3)
        min_jaccard = round(min(jaccards), 3)
        # 组名：取组内归一化表名的最长公共前缀，过短则用最高分表名。
        normalized = [
            _normalize_name(items[index]["name"]) for index in indexes
        ]
        group_name = _common_prefix(normalized)
        if len(group_name) < 6:
            group_name = normalized[0] or member_keys[0][1]
        reason_parts = [
            f"成员 {len(member_keys)} 张",
            f"字段 Jaccard 最低 {min_jaccard:.2f}",
            f"分组置信度 {confidence:.2f}",
        ]
        time_columns = {items[index]["time_column"] for index in indexes}
        if len(time_columns) == 1 and next(iter(time_columns)):
            reason_parts.append("时间列一致")
        groups.append(
            {
                "group_name": group_name,
                "members": member_keys,
                "confidence": confidence,
                "min_jaccard": min_jaccard,
                "reason": "；".join(reason_parts),
            }
        )
    return groups


def _common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    prefix = names[0]
    for name in names[1:]:
        length = 0
        for left, right in zip(prefix, name):
            if left != right:
                break
            length += 1
        prefix = prefix[:length]
        if not prefix:
            break
    return prefix


def _granularity_markers(table_name: str) -> set[str]:
    lowered = str(table_name).lower()
    return {mark for mark in _GRANULARITY_MARKS if mark in lowered}


def score_table(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
    comment_ratio: float = 0.0,
    *,
    static_volume: bool = False,
    volume_floor_eligible: bool = False,
) -> dict[str, Any]:
    """确定性评分：只依赖结构/画像指标，不调用 LLM，结果可复现。

    总分 100：
      完整度 25 / 数据新鲜度 20 / 有效数据量 15 / 时间覆盖 10
      主键与唯一性 10 / 字段注释与语义 10 / 持续更新迹象 5 / 索引质量 5

    扣分：大量空值 -15、缺核心字段 -30/-10、明显备份/临时表 -10。
    长期没有新数据 -20 只在更新周期可信时执行（V1 更新周期恒未知，暂缓）。
    """
    warnings: list[str] = []
    breakdown: dict[str, float] = {}
    deductions: list[tuple[str, float]] = []

    table_name = str(profile.get("table") or "")
    role = str(profile.get("table_role_candidate") or "")
    table_comment = str(
        quality.get("table_comment") or profile.get("table_comment") or ""
    )
    qcols = int(quality.get("queryable_column_count") or 0)
    row_estimate = quality.get("row_estimate")
    sample_count = int(quality.get("sample_row_count") or 0)
    latest = quality.get("latest_data_at")
    freshness_confidence = float(quality.get("freshness_confidence") or 0.0)
    time_coverage = quality.get("time_coverage_days")
    has_primary_key = bool(quality.get("has_primary_key"))
    has_unique_key = bool(quality.get("has_unique_key"))
    duplicate_ratio = quality.get("duplicate_key_ratio")
    error = str(profile.get("error") or "")
    skipped = bool(quality.get("skipped_by_total_timeout"))
    time_column = str(profile.get("time_column_candidate") or "")
    is_time_series = _is_time_series_like(profile, quality)

    # 1. 完整度 25
    if qcols >= 8:
        completeness = 25.0
    elif qcols >= 5:
        completeness = 20.0
    elif qcols >= 3:
        completeness = 14.0
    elif qcols >= 1:
        completeness = 8.0
    else:
        completeness = 0.0
    breakdown["完整度"] = completeness

    # 2. 数据新鲜度 20（非时序表 N/A-neutral；时序表 latest 缺失才低分）
    if not is_time_series:
        freshness = 20.0
        if latest is None or freshness_confidence < 0.5:
            warnings.append("非时序表，新鲜度按中性计分")
    elif latest:
        if freshness_confidence >= 0.5:
            freshness = 20.0
        else:
            freshness = 20.0
            warnings.append("更新周期未知，新鲜度按中性计分，未做新旧扣分")
    else:
        freshness = 5.0
        warnings.append("缺少最新数据时间，新鲜度按低分计")
    breakdown["数据新鲜度"] = freshness

    # 3. 有效数据量 15（静态/实体表按"已确认非空"相对评分；
    #    小型业务时序表 volume floor = max(原始分, 12)）
    if not is_time_series and static_volume:
        if row_estimate is None:
            volume = 12.0 if sample_count > 0 else 0.0
            if sample_count > 0:
                warnings.append("行数估算未知，已确认存在业务数据")
        elif row_estimate >= 1:
            volume = 15.0
        else:
            volume = 0.0
    else:
        if row_estimate is None:
            volume = 4.0 if sample_count > 0 else 0.0
            warnings.append("行数估算未知，按样本量计分")
        elif row_estimate >= 100_000:
            volume = 15.0
        elif row_estimate >= 10_000:
            volume = 13.0
        elif row_estimate >= 1_000:
            volume = 10.0
        elif row_estimate >= 100:
            volume = 7.0
        elif row_estimate >= 1:
            volume = 4.0
        else:
            volume = 0.0
        if volume_floor_eligible and is_time_series:
            volume = max(volume, 12.0)
            warnings.append("小型业务时序表，有效数据量按 volume floor=12 计分")
    breakdown["有效数据量"] = volume

    # 4. 时间覆盖连续性 10（非时序表 N/A-neutral）
    if not is_time_series:
        time_coverage_score = 10.0
        if time_coverage is None:
            warnings.append("非时序表，时间覆盖按中性计分")
    elif time_coverage is None:
        time_coverage_score = 0.0
        if latest:
            warnings.append("时间覆盖范围未知")
    elif time_coverage >= 365:
        time_coverage_score = 10.0
    elif time_coverage >= 90:
        time_coverage_score = 8.0
    elif time_coverage >= 30:
        time_coverage_score = 6.0
    elif time_coverage >= 7:
        time_coverage_score = 4.0
    else:
        time_coverage_score = 2.0
    breakdown["时间覆盖连续性"] = time_coverage_score

    # 5. 主键与唯一性 10
    key_score = (5.0 if has_primary_key else 0.0) + (
        2.0 if has_unique_key else 0.0
    )
    if duplicate_ratio == "unknown" or duplicate_ratio is None:
        if not has_primary_key and not has_unique_key:
            warnings.append("无可用键，重复键比例 unknown（未按质量差扣分）")
        else:
            warnings.append("重复键比例未知")
    elif duplicate_ratio == 0:
        key_score += 3.0
    else:
        key_score += 1.0
    breakdown["主键与唯一性"] = key_score

    # 6. 字段注释与语义清晰度 10
    comment_score = (5.0 if table_comment else 0.0) + round(
        5.0 * max(0.0, min(1.0, float(comment_ratio or 0.0))),
        2,
    )
    breakdown["字段注释与语义"] = comment_score

    # 7. 持续更新迹象 5（unknown 完全中性；仅时序表且无 latest 才低分）
    observed_interval = bool(quality.get("observed_update_interval"))
    if not is_time_series:
        update_score = 5.0
        if not observed_interval:
            warnings.append("非时序表，持续更新按中性计分")
    elif observed_interval:
        update_score = 5.0
    elif latest:
        update_score = 5.0
        warnings.append("更新周期未知，持续更新按中性计分")
    else:
        update_score = 0.0
    breakdown["持续更新迹象"] = update_score

    # 8. 索引质量 5
    breakdown["索引质量"] = 5.0 if (has_primary_key or has_unique_key) else 0.0

    score = sum(breakdown.values())

    # 扣分项（全部有明确依据，且不把"无法计算"当作质量差）
    mostly_null_ratio = _mostly_null_business_ratio(profile)
    if mostly_null_ratio >= 0.6:
        deductions.append(("大量空值", 15.0))
    elif mostly_null_ratio >= 0.4:
        deductions.append(("大量空值", 10.0))
    elif mostly_null_ratio >= 0.2:
        deductions.append(("大量空值", 5.0))
    # 长期没有新数据：更新周期可信时才扣分；V1 恒为未知，只展示不扣分。
    if qcols == 0:
        deductions.append(("缺少可用的业务字段", 30.0))
    elif is_time_series and not time_column:
        deductions.append(("数据表缺少时间类核心字段", 10.0))
    if _is_backup_mark(table_name, table_comment):
        deductions.append(("明显备份/临时/历史表", 10.0))

    for label, amount in deductions:
        warnings.append(f"{label}：-{amount:g}")
        score -= amount
    score = round(max(0.0, min(100.0, score)), 2)

    # 置信度：关键指标未知会降低置信，且不能建议 active。
    confidence = 1.0
    if skipped:
        confidence -= 0.35
    if error:
        confidence -= 0.30
    if sample_count == 0:
        confidence -= 0.25
    if row_estimate is None:
        confidence -= 0.10
    if (
        (duplicate_ratio == "unknown" or duplicate_ratio is None)
        and not has_primary_key
        and not has_unique_key
    ):
        confidence -= 0.15
    if latest is None and is_time_series:
        confidence -= 0.20
    confidence = round(max(0.0, confidence), 2)

    critical: list[str] = []
    if skipped:
        critical.append("表画像被总超时跳过")
    if error:
        critical.append("受限样本读取失败")
    if sample_count == 0:
        critical.append("无样本数据（空表或无法画像）")
    if latest is None and is_time_series:
        critical.append("数据表缺少最新数据时间")
    if (
        role in _TIME_DATA_ROLES
        and (duplicate_ratio == "unknown" or duplicate_ratio is None)
        and not has_primary_key
        and not has_unique_key
    ):
        critical.append("数据表无可用键且重复键比例 unknown")

    can_propose_active = (
        confidence >= MIN_CONFIDENCE_FOR_ACTIVE and not critical
    )
    return {
        "score": score,
        "breakdown": breakdown,
        "deductions": deductions,
        "warnings": warnings,
        "confidence": confidence,
        "can_propose_active": can_propose_active,
        "confirmed_empty": (
            row_estimate == 0
            and sample_count == 0
            and not error
            and not skipped
        ),
        "is_time_series": is_time_series,
    }


def _decide_independent(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
    scored_item: Mapping[str, Any],
    non_biz: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """每张表独立判定（冻结契约：组不再 winner-takes-all）。"""
    score = float(scored_item.get("score") or 0.0)
    reason_parts = [f"评分 {score:g}"]
    error = str(profile.get("error") or "")
    skipped = bool(quality.get("skipped_by_total_timeout"))
    sample_count = int(quality.get("sample_row_count") or 0)

    if error:
        reason_parts.append("受限样本读取失败")
        return "pending", reason_parts
    if skipped:
        reason_parts.append("表画像被总超时跳过")
        return "pending", reason_parts
    if non_biz["confidence"] >= 0.9:
        evidence = "、".join(
            (non_biz["semantic_hits"] or [])[:2]
            + (non_biz["column_hits"] or [])[:3]
        )
        reason_parts.append(
            f"non_business:{non_biz['role']}, "
            f"confidence={non_biz['confidence']:g}, evidence={evidence}"
        )
        return "standby", reason_parts
    if bool(scored_item.get("confirmed_empty")):
        reason_parts.append("confirmed_empty（确认 0 行空表）")
        return "standby", reason_parts
    if sample_count == 0:
        reason_parts.append("数据状态未知（无样本且行数非确认零）")
        return "pending", reason_parts
    if not bool(scored_item.get("can_propose_active")):
        reason_parts.extend(scored_item.get("warnings") or [])
        reason_parts.append("关键质量指标 unknown，需人工确认")
        return "pending", reason_parts
    if score >= ACTIVE_MIN_SCORE:
        decision = "active"
    elif score >= PENDING_MIN_SCORE:
        decision = "pending"
    else:
        decision = "standby"
    if 0.6 <= non_biz["confidence"] < 0.9:
        reason_parts.append(
            f"non_business 中置信:{non_biz['role']}, "
            f"confidence={non_biz['confidence']:g}"
        )
        if decision == "active":
            decision = "standby"
    reason_parts.extend(scored_item.get("warnings") or [])
    return decision, reason_parts


def _find_duplicate_evidence(
    key: tuple[str, str],
    other: tuple[str, str],
    member_names: Mapping[tuple[str, str], str],
    profiles_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    quality_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[bool, str]:
    """duplicate_structure：结构/数据指纹全等、样本>0、行数差≤10%、无职责/粒度差异。"""
    left_quality = quality_by_key.get(key) or {}
    left_sf = str(left_quality.get("structure_fingerprint") or "")
    left_df = str(left_quality.get("data_fingerprint") or "")
    if not left_sf or not left_df:
        return False, ""
    if int(left_quality.get("sample_row_count") or 0) <= 0:
        return False, ""
    left_rows = left_quality.get("row_estimate")
    other_profile = profiles_by_key.get(other) or {}
    if _is_backup_mark(
        str(other_profile.get("table") or ""),
        str(other_profile.get("table_comment") or ""),
    ):
        # backup 表归 backup_mirror 处理，不与主表判结构重复。
        return False, ""
    other_quality = quality_by_key.get(other) or {}
    if str(other_quality.get("structure_fingerprint") or "") != left_sf:
        return False, ""
    if str(other_quality.get("data_fingerprint") or "") != left_df:
        return False, ""
    if int(other_quality.get("sample_row_count") or 0) <= 0:
        return False, ""
    if _has_role_conflict(
        member_names.get(key, ""),
        member_names.get(other, ""),
    ):
        return False, ""
    other_rows = other_quality.get("row_estimate")
    if (
        left_rows is not None
        and other_rows is not None
        and other_rows > 0
        and abs(left_rows - other_rows) / max(left_rows, other_rows) > 0.10
    ):
        return False, ""
    return True, (
        "duplicate_structure 与 "
        f"{member_names.get(other, other[1])}（结构/数据指纹一致，行数差≤10%）"
    )


def _find_backup_evidence(
    key: tuple[str, str],
    members: list[tuple[str, str]],
    member_names: Mapping[tuple[str, str], str],
    profiles_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    quality_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[bool, str]:
    """backup_mirror：显式 backup marker + 同组非 marker 主表 + 结构相等或列 Jaccard≥0.9。"""
    profile = profiles_by_key.get(key) or {}
    table = str(profile.get("table") or "")
    comment = str(profile.get("table_comment") or "")
    if not _is_backup_mark(table, comment):
        return False, ""
    left_columns = _column_names(profile)
    left_sf = str((quality_by_key.get(key) or {}).get("structure_fingerprint") or "")
    for other in members:
        if other == key:
            continue
        other_profile = profiles_by_key.get(other) or {}
        if _is_backup_mark(
            str(other_profile.get("table") or ""),
            str(other_profile.get("table_comment") or ""),
        ):
            continue
        other_sf = str(
            (quality_by_key.get(other) or {}).get("structure_fingerprint") or ""
        )
        if left_sf and left_sf == other_sf:
            return True, (
                f"backup_mirror：{table} 与 "
                f"{member_names.get(other, other[1])} 结构指纹一致"
            )
        if left_columns and _jaccard(left_columns, _column_names(other_profile)) >= 0.90:
            return True, (
                f"backup_mirror：{table} 与 "
                f"{member_names.get(other, other[1])} 列重合≥0.90"
            )
    return False, ""


def _find_obsolete_hint(
    key: tuple[str, str],
    members: list[tuple[str, str]],
    member_names: Mapping[tuple[str, str], str],
    quality_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> str:
    """obsolete：只提示、不决策（latest 显著早于同组其他成员 >365 天）。"""
    from datetime import datetime

    def _parse(value: Any):
        try:
            return datetime.fromisoformat(str(value).replace("Z", ""))
        except Exception:
            return None

    left_latest = (quality_by_key.get(key) or {}).get("latest_data_at")
    if not left_latest:
        return ""
    left_dt = _parse(left_latest)
    if left_dt is None:
        return ""
    for other in members:
        if other == key:
            continue
        other_latest = (quality_by_key.get(other) or {}).get("latest_data_at")
        other_dt = _parse(other_latest) if other_latest else None
        if other_dt is not None and (other_dt - left_dt).days > 365:
            return (
                f"obsolete_candidate（latest 早于 "
                f"{member_names.get(other, other[1])} 超过 365 天，仅提示不决策）"
            )
    return ""


def compute_proposals(
    profiles: list[Mapping[str, Any]],
    comment_ratios: Mapping[tuple[str, str], float] | None = None,
    existing_reviews: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """生成全部 present 表的建议字段（阶段 B 唯一写入入口）。

    冻结契约：
      每表独立评分、独立判定；业务组只补充关系证据；
      组内唯一自动降级是 duplicate_structure / backup_mirror；
      obsolete 只提示；effective / selected_scope 一律不动。
    """
    comment_ratios = comment_ratios or {}
    profiles_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    quality_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    scored: dict[tuple[str, str], dict[str, Any]] = {}
    non_biz_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    shard_evidence = _physical_shard_evidence(profiles)
    for profile in profiles:
        schema = str(profile.get("schema") or "")
        table = str(profile.get("table") or "")
        if not schema or not table:
            continue
        key = (schema, table)
        profiles_by_key[key] = profile
        quality = profile.get("quality") or {}
        quality_by_key[key] = quality
        non_biz = classify_non_business_evidence(profile, quality)
        non_biz_by_key[key] = non_biz
        is_time_series = _is_time_series_like(profile, quality)
        static_volume = (
            not is_time_series
            and non_biz["confidence"] < 0.6
            and not _is_backup_mark(
                table,
                str(quality.get("table_comment") or profile.get("table_comment") or ""),
            )
        )
        volume_floor_eligible = (
            is_time_series
            and _business_time_series_strong(profile, quality)
            and int(quality.get("sample_row_count") or 0) > 0
            and (quality.get("row_estimate") is None or quality.get("row_estimate") >= 1)
            and non_biz["confidence"] < 0.6
            and shard_evidence.get(key, 0.0) < 0.9
            and not _is_backup_mark(
                table,
                str(quality.get("table_comment") or profile.get("table_comment") or ""),
            )
            and not str(profile.get("error") or "")
            and not bool(quality.get("skipped_by_total_timeout"))
        )
        scored[key] = score_table(
            profile,
            quality,
            comment_ratios.get(key, 0.0),
            static_volume=static_volume,
            volume_floor_eligible=volume_floor_eligible,
        )

    updates: dict[tuple[str, str], dict[str, Any]] = {}
    for key, profile in profiles_by_key.items():
        quality = quality_by_key[key]
        scored_item = scored[key]
        non_biz = non_biz_by_key[key]
        shard_confidence = shard_evidence.get(key, 0.0)
        if shard_confidence >= 0.9:
            decision = "standby"
            reason_parts = [
                f"评分 {scored_item['score']:g}",
                f"physical_shard, confidence={shard_confidence:g}",
            ]
        else:
            decision, reason_parts = _decide_independent(
                profile,
                quality,
                scored_item,
                non_biz,
            )
        updates[key] = {
            "business_group": "",
            "group_confidence": 0.0,
            "compared_tables_json": "[]",
            "group_reason": "",
            "proposed_decision": decision,
            "proposed_score": scored_item["score"],
            "proposed_reason": "；".join(reason_parts),
        }

    # 组级：只补充关系证据，唯一自动降级是确定性重复。
    groups = group_tables(profiles)
    for group in groups:
        members = group["members"]
        group_name = group["group_name"]
        group_confidence = group["confidence"]
        group_reason = group["reason"]
        member_names = {
            key: str(profiles_by_key[key].get("table") or "") for key in members
        }
        active_members = [
            key
            for key in members
            if updates[key]["proposed_decision"] == "active"
        ]
        active_members.sort(
            key=lambda key: (
                -scored[key]["score"],
                member_names.get(key, key[1]),
            )
        )
        # 1) backup_mirror 优先：backup 表降 standby，主表保留。
        for key in active_members:
            if updates[key]["proposed_decision"] != "active":
                continue
            backup, backup_detail = _find_backup_evidence(
                key,
                members,
                member_names,
                profiles_by_key,
                quality_by_key,
            )
            if backup:
                updates[key]["proposed_decision"] = "standby"
                updates[key]["proposed_reason"] += f"；{backup_detail}"
        # 2) duplicate_structure：同分/低分者降 standby，保留组内高分主表。
        remaining = [
            key
            for key in active_members
            if updates[key]["proposed_decision"] == "active"
        ]
        for index, key in enumerate(remaining):
            for other in remaining[index + 1 :]:
                duplicate, duplicate_detail = _find_duplicate_evidence(
                    key,
                    other,
                    member_names,
                    profiles_by_key,
                    quality_by_key,
                )
                if duplicate:
                    updates[other]["proposed_decision"] = "standby"
                    updates[other]["proposed_reason"] += f"；{duplicate_detail}"
        for key in members:
            hint = _find_obsolete_hint(key, members, member_names, quality_by_key)
            if hint:
                updates[key]["proposed_reason"] += f"；{hint}"
        compared = [
            {
                "schema_name": key[0],
                "table_name": key[1],
                "score": scored[key]["score"],
                "proposed_decision": updates[key]["proposed_decision"],
            }
            for key in sorted(members)
        ]
        compared_json = json.dumps(compared, ensure_ascii=False)
        for key in members:
            updates[key]["business_group"] = group_name
            updates[key]["group_confidence"] = group_confidence
            updates[key]["compared_tables_json"] = compared_json
            updates[key]["group_reason"] = group_reason
    return updates
