"""表准入审核编排：Eligibility → Quality → Proposed Decision。

本模块只生成建议字段：
  proposed_decision / proposed_score / proposed_reason
  business_group / group_confidence / compared_tables_json / group_reason

严格遵守阶段 B 边界：
  不修改 effective_decision；
  不覆盖 selected_scope；
  不调用 prepare()；
  不生成正式 Metadata / DDL / Chroma；
  不增加 runtime_revision。

Quality Score 只表示数据质量，不能决定业务归属或正式范围。决策契约：
- Eligibility 先行且不可被高质量分覆盖；
- Proposed Decision 独立组合质量结果与确定性跨表约束；
- 组内唯一允许自动降级的是确定性重复证据（duplicate_structure /
  backup_mirror）；obsolete 只提示、不决策；
- update_interval 未知按中性计分；非时序表时间维度全部 N/A-neutral；
- confirmed_empty -> standby；数据状态未知 -> pending；
- 旧 non-business taxonomy 仅保留诊断兼容，不参与 Quality 或 Proposed。
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date
from typing import Any, Mapping

from backend.data_source_eligibility import evaluate_table_eligibility
from backend.data_source_proposed_decision import (
    ProposalConstraint,
    apply_constraint,
    decide_proposal,
)
from backend.data_source_quality_scoring import (
    _business_time_column,
    _is_audit_time_column,
    _is_time_series_like,
    _looks_time_column,
    score_table,
)


# ---------------------------------------------------------------------------
# 阈值（与评审方案一致，可通过环境变量微调）
# ---------------------------------------------------------------------------

GROUP_MIN_GAP = float(os.getenv("DATA_SOURCE_GROUP_MIN_GAP", "5"))
GROUP_CONFIDENCE_THRESHOLD = float(
    os.getenv("DATA_SOURCE_GROUP_CONFIDENCE_THRESHOLD", "0.55")
)

# 明显备份/临时/历史标记：用于分组与确定性 proposal constraint，不进入 Quality Score。
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
    baseline_year_month: tuple[int, int],
) -> dict[tuple[str, str], dict[str, Any]]:
    """识别物理分片，并区分冗余分片与必要查询入口。"""
    profiles_by_table: dict[tuple[str, str], Mapping[str, Any]] = {}
    for profile in profiles:
        schema = str(profile.get("schema") or "")
        table = str(profile.get("table") or "")
        if schema and table:
            profiles_by_table[(schema, table)] = profile
    digit_families: dict[tuple[str, str], list[str]] = defaultdict(list)
    for schema, table in profiles_by_table:
        match = _PHYSICAL_SHARD_RE.match(table)
        if match and match.group("num").isdigit():
            digit_families[(schema, match.group("family"))].append(table)
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for (schema, family), shards in digit_families.items():
        if len(shards) < 2:
            continue
        base = re.sub(r"_records$", "", family)
        unified = [
            table
            for candidate_schema, table in profiles_by_table
            if candidate_schema == schema
            if table not in shards
            and not _PHYSICAL_SHARD_RE.match(table)
            and (
                table in {base, family}
                or bool(
                    re.fullmatch(
                        re.escape(base)
                        + r"_(?:minute|hour|day|month|year)_records",
                        table,
                    )
                )
            )
        ]
        evidence_shards = [
            table
            for table in shards
            if str(
                (profiles_by_table[(schema, table)].get("quality") or {}).get(
                    "data_fingerprint"
                )
                or ""
            )
        ]
        if len(evidence_shards) < 2:
            continue
        fingerprints = {
            str(
                (profiles_by_table[(schema, table)].get("quality") or {}).get(
                    "structure_fingerprint"
                )
                or ""
            )
            for table in evidence_shards
        }
        if not fingerprints or "" in fingerprints or len(fingerprints) > 1:
            continue
        data_fingerprints = {
            str(
                (profiles_by_table[(schema, table)].get("quality") or {}).get(
                    "data_fingerprint"
                )
                or ""
            )
            for table in evidence_shards
        }
        if len(data_fingerprints) != len(evidence_shards):
            continue
        semantic_signatures = {
            (
                str(profile.get("table_role_candidate") or ""),
                str(profile.get("grain_candidate") or ""),
                str(profile.get("time_column_candidate") or ""),
            )
            for table in evidence_shards
            for profile in (profiles_by_table[(schema, table)],)
        }
        if len(semantic_signatures) > 1:
            continue
        suffixes = sorted(
            int(_PHYSICAL_SHARD_RE.match(table).group("num"))  # type: ignore[union-attr]
            for table in shards
        )
        consecutive = suffixes == list(range(suffixes[0], suffixes[-1] + 1))
        year_match = re.search(r"(?:^|_)((?:19|20)\d{2})$", family)
        family_confidence = 0.98 if year_match and consecutive else 0.95
        shard_role = "redundant_shard" if unified else "required_access_shard"
        for table in shards:
            match = _PHYSICAL_SHARD_RE.match(table)
            month = int(match.group("num")) if match else 0
            shard_year_month = (
                (int(year_match.group(1)), month) if year_match else None
            )
            closed_time_partition = bool(
                shard_year_month
                and 1 <= month <= 12
                and shard_year_month < baseline_year_month
            )
            evidence[(schema, table)] = {
                "confidence": family_confidence,
                "family": family,
                "role": shard_role,
                "closed_time_partition": closed_time_partition,
            }
        for table in unified:
            evidence[(schema, table)] = {
                "confidence": 0.95,
                "family": family,
                "role": "unified_entry",
                "closed_time_partition": False,
            }
    return evidence


def _governance_year_month() -> tuple[int, int]:
    """一次 proposal 计算只读取一次当前年月，避免跨日期边界不一致。"""
    today = date.today()
    return today.year, today.month


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


def _find_duplicate_evidence(
    key: tuple[str, str],
    other: tuple[str, str],
    member_names: Mapping[tuple[str, str], str],
    profiles_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    quality_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[bool, str]:
    """duplicate_structure：结构/数据指纹全等、样本>0、行数差≤10%、无职责/粒度差异。"""
    left_profile = profiles_by_key.get(key) or {}
    if _is_backup_mark(
        str(left_profile.get("table") or ""),
        str(left_profile.get("table_comment") or ""),
    ):
        return False, ""
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
    eligibility_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    shard_evidence = _physical_shard_evidence(
        profiles,
        _governance_year_month(),
    )
    for profile in profiles:
        schema = str(profile.get("schema") or "")
        table = str(profile.get("table") or "")
        if not schema or not table:
            continue
        key = (schema, table)
        profiles_by_key[key] = profile
        quality = profile.get("quality") or {}
        quality_by_key[key] = quality
        eligibility_by_key[key] = evaluate_table_eligibility(
            profile,
            quality,
        ).as_dict()
        shard = shard_evidence.get(key) or {}
        scored[key] = score_table(
            profile,
            quality,
            comment_ratios.get(key, 0.0),
            closed_physical_time_partition=bool(
                shard.get("closed_time_partition")
            ),
        )

    updates: dict[tuple[str, str], dict[str, Any]] = {}
    for key, profile in profiles_by_key.items():
        quality = quality_by_key[key]
        scored_item = scored[key]
        eligibility = eligibility_by_key[key]
        shard = shard_evidence.get(key) or {}
        shard_confidence = float(shard.get("confidence") or 0.0)
        shard_role = str(shard.get("role") or "")
        shard_family = str(shard.get("family") or "")
        constraints: list[ProposalConstraint] = []
        if shard_role == "redundant_shard" and shard_confidence >= 0.9:
            constraints.append(
                ProposalConstraint(
                    "physical_shard_redundant",
                    detail=f"family={shard_family}",
                    confidence=shard_confidence,
                )
            )
        proposal = decide_proposal(eligibility, scored_item, constraints)
        reasons = list(proposal.reasons)
        if shard_role == "required_access_shard":
            reasons.append(
                f"physical_shard_required_access, family={shard_family}"
            )
        updates[key] = {
            "business_group": "",
            "group_confidence": 0.0,
            "compared_tables_json": "[]",
            "group_reason": "",
            "proposed_decision": proposal.decision,
            "proposed_score": scored_item["score"],
            "proposed_reason": "；".join(reasons),
            "quality_metrics_patch": {
                "physical_shard_family": shard_family,
                "physical_shard_role": shard_role,
                "closed_physical_time_partition": bool(
                    shard.get("closed_time_partition")
                ),
            } if shard_role else {},
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
        eligible_members = [
            key
            for key in members
            if eligibility_by_key[key].get("status") == "eligible"
        ]
        eligible_members.sort(
            key=lambda key: (
                -scored[key]["score"],
                member_names.get(key, key[1]),
            )
        )
        # 1) backup_mirror 优先：backup 表降 standby，主表保留。
        backup_members: set[tuple[str, str]] = set()
        for key in eligible_members:
            backup, backup_detail = _find_backup_evidence(
                key,
                members,
                member_names,
                profiles_by_key,
                quality_by_key,
            )
            if backup:
                backup_members.add(key)
                decision, reasons = apply_constraint(
                    [updates[key]["proposed_reason"]],
                    ProposalConstraint("backup_mirror", backup_detail),
                )
                updates[key]["proposed_decision"] = decision
                updates[key]["proposed_reason"] = "；".join(reasons)
        # 2) duplicate_structure：同分/低分者降 standby，保留组内高分主表。
        remaining = [
            key for key in eligible_members if key not in backup_members
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
                    decision, reasons = apply_constraint(
                        [updates[other]["proposed_reason"]],
                        ProposalConstraint("duplicate_structure", duplicate_detail),
                    )
                    updates[other]["proposed_decision"] = decision
                    updates[other]["proposed_reason"] = "；".join(reasons)
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
