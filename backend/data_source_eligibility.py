"""自动治理 v2 的项目适配性硬门。

本模块只判断表是否有资格进入水利/环保问数候选集，不进行质量评分，
也不生成或写入 effective_decision、selected_scope 和正式资产。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class EligibilityResult:
    status: str
    category: str
    confidence: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "category": self.category,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


_STRONG_DOMAIN_TOKENS = frozenset(
    {
        "water", "hydro", "hydrology", "meteorology", "meteorological",
        "pollutant", "pollution", "outlet", "sewage", "emission",
        "ecology", "ecological", "aquatic", "river", "lake", "watershed",
        "environment", "environmental", "remediation", "waterbody", "fish",
        "plankton", "sediment", "zoobenthos", "zooplankton", "unmaned",
        "unmanned",
    }
)
_STRONG_DOMAIN_CN = (
    "水质", "水文", "气象", "污染", "排污", "排放", "水环境", "生态环境",
    "水生态", "河流", "河湖", "湖泊", "流域", "水体", "水源", "水生",
    "污水", "无人船", "环保项目",
)
_DIMENSION_TOKENS = frozenset(
    {
        "station", "section", "area", "region", "district", "dictionary",
        "dict", "threshold", "directory", "catalog", "site", "point",
    }
)
_DIMENSION_CN = (
    "站点", "断面", "区域", "行政区划", "字典", "阈值", "目录", "点位",
)

_BACKUP_RE = re.compile(
    r"(?:^|_)(?:bak|backup|bark|copy|tmp|temp|old|archive)(?:_|$)",
    re.IGNORECASE,
)
_SYSTEM_LOG_RE = re.compile(
    r"(?:^|_)(?:sys|system|login|oper|operation|audit|access)_?log(?:_|$)",
    re.IGNORECASE,
)
_SUPPORT_FILE_RE = re.compile(
    r"(?:^|_)(?:attachment|file|video|picture|image|offline_upload|upload_cache|file_cache)(?:_|$)",
    re.IGNORECASE,
)
_MODEL_PARAM_RE = re.compile(
    r"(?:^|_)model(?:_|.*_)(?:param|parameter|config|setting)(?:_|$)",
    re.IGNORECASE,
)


def _identifier_tokens(value: str) -> set[str]:
    """按命名片段取 token，避免 station 命中 workstation。"""
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _positive_business_evidence(table: str, comment: str) -> tuple[str, list[str]]:
    """只使用表名和表注释产生正向证据；列名不能单独证明业务归属。"""
    subject = f"{table} {comment}".lower()
    tokens = _identifier_tokens(subject)
    strong_hits = sorted(tokens & _STRONG_DOMAIN_TOKENS)
    strong_hits.extend(term for term in _STRONG_DOMAIN_CN if term in subject)
    dimension_hits = sorted(tokens & _DIMENSION_TOKENS)
    dimension_hits.extend(term for term in _DIMENSION_CN if term in subject)

    if strong_hits and dimension_hits:
        return "water_environment_dimension", strong_hits[:2] + dimension_hits[:2]
    if strong_hits:
        return "water_environment_business", strong_hits[:3]

    # 仅保留已由当前项目确认的组合模式；survey/patrol 等泛词单独不准入。
    if {"dc", "survey"}.issubset(tokens) and tokens & {"info", "task"}:
        return "project_business_pattern", ["dc+survey", "info/task"]
    if {"camera", "patrol"}.issubset(tokens):
        return "project_business_pattern", ["camera+patrol"]
    if "巡回调查" in subject:
        return "project_business_pattern", ["巡回调查"]
    return "", []


def _result(
    status: str,
    category: str,
    confidence: float,
    *reasons: str,
) -> EligibilityResult:
    return EligibilityResult(status, category, confidence, tuple(reasons))


def evaluate_table_eligibility(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any] | None = None,
) -> EligibilityResult:
    """确定性判断单表的项目适配性；证据不足时返回 unknown。"""
    quality = quality or profile.get("quality") or {}
    table = str(profile.get("table") or "").lower()
    comment = str(
        profile.get("table_comment") or quality.get("table_comment") or ""
    ).lower()
    subject_text = f"{table} {comment}".lower()

    if str(profile.get("error") or ""):
        return _result(UNKNOWN, "profile_error", 1.0, "表画像读取失败")
    if bool(quality.get("skipped_by_total_timeout")):
        return _result(UNKNOWN, "profile_timeout", 1.0, "表画像因总超时被跳过")

    queryable_count = quality.get("queryable_column_count")
    if queryable_count is not None and int(queryable_count) <= 0:
        return _result(INELIGIBLE, "no_queryable_columns", 1.0, "没有可问数字段")

    row_estimate = quality.get("row_estimate")
    sample_count = int(quality.get("sample_row_count") or 0)
    if row_estimate == 0 and sample_count == 0:
        return _result(INELIGIBLE, "confirmed_empty", 1.0, "已确认是空表")

    if _BACKUP_RE.search(table) or any(
        marker in comment for marker in ("备份表", "临时表", "历史副本", "归档表")
    ):
        return _result(INELIGIBLE, "backup_or_temporary", 0.99, "备份、临时或归档对象")

    if _SYSTEM_LOG_RE.search(table) or any(
        marker in comment for marker in ("系统日志", "登录日志", "操作日志", "审计日志")
    ):
        return _result(INELIGIBLE, "system_log", 0.99, "系统运行或审计日志")

    if re.search(
        r"(?:^|_)(?:user|role|permission|oauth|client|login)(?:_|$)",
        table,
    ) or re.match(r"^sm_(?:user|role|permission|oauth|client)", table):
        return _result(INELIGIBLE, "identity_platform", 0.98, "身份、权限或登录平台表")

    if table.startswith("t_metadata_") or "metadata_registry" in table:
        return _result(INELIGIBLE, "metadata_registry", 0.98, "技术元数据注册表")

    if "offline_upload" in table or "upload_cache" in table:
        return _result(INELIGIBLE, "upload_cache", 0.99, "离线上传或缓存过程表")

    if _SUPPORT_FILE_RE.search(table):
        return _result(INELIGIBLE, "file_or_media_support", 0.98, "附件或媒体支撑表")

    if any(token in table for token in ("raster", "_uav_", "panorama")):
        return _result(INELIGIBLE, "media_asset", 0.97, "栅格、无人机或全景媒体资产")

    if (
        re.search(r"(?:^|_)(?:app|apk)(?:_|$)", table)
        and any(term in subject_text for term in ("version", "package", "install", "版本", "安装包"))
    ) or table.endswith("_app"):
        return _result(INELIGIBLE, "application_package", 0.97, "应用安装或版本管理表")

    business_category, business_evidence = _positive_business_evidence(
        table,
        comment,
    )

    if _MODEL_PARAM_RE.search(table) and any(
        term in subject_text for term in ("初始化", "参数", "parameter", "initial")
    ):
        return _result(INELIGIBLE, "model_runtime_parameter", 0.97, "模型初始化或运行参数")

    if table.startswith("model_") and any(
        token in table for token in ("_run", "_output", "artifact")
    ):
        return _result(INELIGIBLE, "model_artifact", 0.96, "模型运行记录或输出产物")

    if (
        any(
            token in table
            for token in ("service_directory", "interface_config", "route_config", "menu_config")
        )
        or table in {"wm_directory", "wt_service_directory"}
    ) and not business_category:
        return _result(INELIGIBLE, "platform_configuration", 0.96, "平台目录或接口配置")

    if business_category:
        return _result(
            ELIGIBLE,
            business_category,
            0.90,
            "业务证据：" + "、".join(business_evidence),
        )

    return _result(UNKNOWN, "insufficient_business_evidence", 0.0, "缺少项目适配性证据")
