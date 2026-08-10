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


_BUSINESS_TERMS = (
    "water", "hydro", "meteorolog", "pollut", "outlet", "sewage",
    "emission", "ecolog", "aquatic", "river", "lake", "watershed",
    "station", "section", "survey", "patrol", "warning", "warn",
    "environment", "treatment", "remediation", "waterbody", "fish",
    "plankton", "sediment", "zoobenthos", "zooplankton", "unmaned_ship",
    "unmanned_ship", "task_info", "task_directory",
    "水质", "水文", "气象", "污染", "排污", "排放", "生态", "河流",
    "河湖", "湖泊", "流域", "断面", "站点", "巡查", "调查", "预警",
    "治理", "防治", "水体", "水源", "水生", "污水", "无人船",
    "环保项目", "治理项目", "目标考核",
    # 兼容历史画像中已落盘的错误编码中文注释；新画像仍使用上面的正常中文词。
    "姘磋川", "姘存枃", "姹℃煋", "鐩戞祴", "璋冩煡", "宸℃煡",
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


def _text(profile: Mapping[str, Any], quality: Mapping[str, Any]) -> str:
    table = str(profile.get("table") or "")
    comment = str(
        profile.get("table_comment") or quality.get("table_comment") or ""
    )
    columns = " ".join(
        str(column.get("column") or column.get("name") or "")
        for column in (profile.get("columns") or [])
        if isinstance(column, Mapping)
    )
    return f"{table} {comment} {columns}".lower()


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
    text = _text(profile, quality)

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
        and any(term in text for term in ("version", "package", "install", "版本", "安装包"))
    ) or table.endswith("_app"):
        return _result(INELIGIBLE, "application_package", 0.97, "应用安装或版本管理表")

    has_business_evidence = any(term in text for term in _BUSINESS_TERMS)

    if _MODEL_PARAM_RE.search(table) and any(
        term in text for term in ("初始化", "参数", "parameter", "initial")
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
    ) and not has_business_evidence:
        return _result(INELIGIBLE, "platform_configuration", 0.96, "平台目录或接口配置")

    if has_business_evidence:
        return _result(ELIGIBLE, "water_environment_business", 0.90, "命中水利或环保业务语义")

    return _result(UNKNOWN, "insufficient_business_evidence", 0.0, "缺少项目适配性证据")
