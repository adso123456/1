"""首轮 run_sql 确定性门禁的请求级状态与 Provider 策略。"""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from backend.request_diagnostics import write_trace_json


REQUIREMENT_REASON = "approved_sql_example_injected"
FORCED_RUN_SQL_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "run_sql"},
}


class RunSqlRequirementError(RuntimeError):
    """需要 run_sql，但无法构造安全的父类请求。"""


@dataclass
class RunSqlRequirementState:
    requires_run_sql: bool = False
    requirement_reasons: list[str] = field(default_factory=list)
    first_llm_call_seen: bool = False
    forced_tool_choice_applied: bool = False
    forced_tool_name: str = ""
    sql_example_injected_count: int = 0
    deepseek_non_thinking_tool_turn_active: bool = False
    non_thinking_turn_started_on_call: int | None = None
    successful_run_sql_completed: bool = False
    successful_run_sql_count: int = 0
    successful_run_sql_row_count: int | None = None
    successful_run_sql_columns: list[str] = field(default_factory=list)
    tool_phase_closed: bool = False
    tool_phase_close_reason: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class EffectiveRequestPolicy:
    effective_tool_choice: Any
    effective_extra_body: dict[str, Any] | None
    remove_reasoning_effort: bool
    remove_tools: bool
    remove_tool_choice: bool
    provider_strategy: str


_current_requirement: ContextVar[RunSqlRequirementState | None] = ContextVar(
    "run_sql_requirement", default=None
)


def initialize_run_sql_requirement() -> RunSqlRequirementState:
    state = RunSqlRequirementState()
    _current_requirement.set(state)
    return state


def get_run_sql_requirement() -> RunSqlRequirementState | None:
    return _current_requirement.get()


def clear_run_sql_requirement() -> None:
    _current_requirement.set(None)


def record_injected_sql_examples(injected_count: int) -> RunSqlRequirementState:
    """只以实际注入数量设置本请求门禁。"""
    state = get_run_sql_requirement() or initialize_run_sql_requirement()
    state.sql_example_injected_count = injected_count
    if injected_count > 0:
        state.requires_run_sql = True
        if REQUIREMENT_REASON not in state.requirement_reasons:
            state.requirement_reasons.append(REQUIREMENT_REASON)
    return state


def record_successful_run_sql(*, row_count: int, columns: list[str]) -> bool:
    """记录获批样例请求的成功 SELECT，并在首次成功后关闭工具阶段。"""
    state = get_run_sql_requirement()
    if state is None or not state.requires_run_sql:
        return False
    state.successful_run_sql_count += 1
    if state.successful_run_sql_completed:
        return False
    state.successful_run_sql_completed = True
    state.successful_run_sql_row_count = row_count
    state.successful_run_sql_columns = list(columns)
    state.tool_phase_closed = True
    state.tool_phase_close_reason = "first_successful_run_sql_for_approved_example"
    _write_trace_state(state)
    return True


def record_successful_run_sql_result(result: Any) -> bool:
    """仅接受执行成功且生产元数据标记为 SELECT 的 run_sql 结果。"""
    metadata = dict(getattr(result, "metadata", None) or {})
    if not getattr(result, "success", False) or metadata.get("query_type") != "SELECT":
        return False
    return record_successful_run_sql(
        row_count=int(metadata.get("row_count") or 0),
        columns=list(metadata.get("columns") or []),
    )


def _state_payload(state: RunSqlRequirementState) -> dict[str, Any]:
    return {
        "requires_run_sql": state.requires_run_sql,
        "requirement_reasons": state.requirement_reasons,
        "sql_example_injected_count": state.sql_example_injected_count,
        "successful_run_sql_completed": state.successful_run_sql_completed,
        "successful_run_sql_count": state.successful_run_sql_count,
        "successful_run_sql_row_count": state.successful_run_sql_row_count,
        "successful_run_sql_columns": state.successful_run_sql_columns,
        "tool_phase_closed": state.tool_phase_closed,
        "tool_phase_close_reason": state.tool_phase_close_reason,
    }


def _write_trace_state(state: RunSqlRequirementState) -> None:
    latest = state.decisions[-1] if state.decisions else {}
    write_trace_json(
        "run-sql-requirement.json",
        {**_state_payload(state), **latest, "calls": state.decisions},
    )


def _tool_names(tools: list[Any] | None) -> list[str]:
    names = []
    for tool in tools or []:
        if isinstance(tool, dict):
            name = tool.get("name") or (tool.get("function") or {}).get("name")
        else:
            name = getattr(tool, "name", "")
        if name:
            names.append(str(name))
    return names


def _write_state(
    state: RunSqlRequirementState,
    *,
    llm_call_index: int,
    original_tool_choice: Any,
    effective_tool_choice: Any,
    run_sql_schema_present: bool,
    provider_hostname: str = "",
    model: str = "",
    provider_strategy: str = "",
    original_thinking: Any = None,
    effective_thinking: Any = None,
    original_reasoning_effort: Any = None,
    effective_reasoning_effort: Any = None,
    thinking_override_applied: bool = False,
    non_thinking_continuation_applied: bool = False,
    answer_only_continuation_applied: bool = False,
    final_payload_tools_present: bool = True,
    final_payload_tool_choice_present: bool = True,
    parent_llm_called: bool = False,
    error_type: str = "",
    failure_reason: str = "",
) -> None:
    decision = {
        "llm_call_index": llm_call_index,
        "provider_hostname": provider_hostname,
        "model": model,
        "provider_strategy": provider_strategy,
        "original_tool_choice": original_tool_choice,
        "effective_tool_choice": effective_tool_choice,
        "original_thinking": original_thinking,
        "effective_thinking": effective_thinking,
        "original_reasoning_effort": original_reasoning_effort,
        "effective_reasoning_effort": effective_reasoning_effort,
        "run_sql_schema_present": run_sql_schema_present,
        "thinking_override_applied": thinking_override_applied,
        "deepseek_non_thinking_tool_turn_active": (
            state.deepseek_non_thinking_tool_turn_active
        ),
        "non_thinking_turn_started_on_call": (
            state.non_thinking_turn_started_on_call
        ),
        "non_thinking_continuation_applied": (
            non_thinking_continuation_applied
        ),
        "answer_only_continuation_applied": answer_only_continuation_applied,
        "final_payload_tools_present": final_payload_tools_present,
        "final_payload_tool_choice_present": final_payload_tool_choice_present,
        "synthetic_reasoning_content_injected": False,
        "forced_tool_choice_applied": state.forced_tool_choice_applied,
        "forced_tool_name": state.forced_tool_name,
        "parent_llm_called": parent_llm_called,
        "error_type": error_type,
        "failure_reason": failure_reason,
    }
    state.decisions.append(decision)
    _write_trace_state(state)


def build_effective_request_policy(
    *,
    llm_call_index: int,
    tools: list[Any] | None,
    original_payload: dict[str, Any],
    provider_hostname: str,
    model: str,
) -> EffectiveRequestPolicy:
    """构造本轮策略；DeepSeek 首轮强制工具时临时关闭 Thinking。"""
    state = get_run_sql_requirement() or initialize_run_sql_requirement()
    is_first_call = not state.first_llm_call_seen
    state.first_llm_call_seen = True
    run_sql_schema_present = "run_sql" in _tool_names(tools)
    original_tool_choice = original_payload.get("tool_choice")
    raw_extra_body = original_payload.get("extra_body")
    original_extra_body = (
        deepcopy(raw_extra_body) if isinstance(raw_extra_body, dict) else raw_extra_body
    )
    original_thinking = (
        deepcopy(original_extra_body.get("thinking"))
        if isinstance(original_extra_body, dict)
        else None
    )
    original_reasoning_effort = original_payload.get("reasoning_effort")
    effective_tool_choice = original_tool_choice
    effective_extra_body = deepcopy(original_extra_body)
    effective_thinking = deepcopy(original_thinking)
    effective_reasoning_effort = original_reasoning_effort
    remove_reasoning_effort = False
    remove_tools = False
    remove_tool_choice = False
    thinking_override_applied = False
    non_thinking_continuation_applied = False
    provider_strategy = "original_request"
    is_deepseek = (
        provider_hostname.lower() == "api.deepseek.com"
        and model.lower().startswith("deepseek-")
    )

    if state.requires_run_sql and is_first_call:
        if not run_sql_schema_present:
            _write_state(
                state,
                llm_call_index=llm_call_index,
                original_tool_choice=original_tool_choice,
                effective_tool_choice=None,
                run_sql_schema_present=False,
                provider_hostname=provider_hostname,
                model=model,
                provider_strategy="fail_closed_missing_run_sql",
                error_type="RunSqlRequirementError",
                failure_reason="run_sql schema is missing",
            )
            raise RunSqlRequirementError(
                "首轮请求需要 run_sql，但实际工具 Schema 中不存在 run_sql"
            )
        effective_tool_choice = FORCED_RUN_SQL_TOOL_CHOICE
        state.forced_tool_choice_applied = True
        state.forced_tool_name = "run_sql"
        provider_strategy = "named_run_sql_first_tool_call"
        if is_deepseek:
            if raw_extra_body is not None and not isinstance(raw_extra_body, dict):
                _write_state(
                    state,
                    llm_call_index=llm_call_index,
                    original_tool_choice=original_tool_choice,
                    effective_tool_choice=None,
                    run_sql_schema_present=True,
                    provider_hostname=provider_hostname,
                    model=model,
                    provider_strategy="fail_closed_thinking_override",
                    original_thinking=original_thinking,
                    original_reasoning_effort=original_reasoning_effort,
                    error_type="RunSqlRequirementError",
                    failure_reason="extra_body is not a mapping",
                )
                raise RunSqlRequirementError(
                    "DeepSeek 首轮 Thinking 覆盖失败：extra_body 不是映射"
                )
            effective_extra_body = deepcopy(raw_extra_body) if raw_extra_body else {}
            effective_extra_body["thinking"] = {"type": "disabled"}
            effective_thinking = {"type": "disabled"}
            effective_reasoning_effort = None
            remove_reasoning_effort = "reasoning_effort" in original_payload
            thinking_override_applied = True
            provider_strategy = "deepseek_non_thinking_first_tool_call"
            state.deepseek_non_thinking_tool_turn_active = True
            state.non_thinking_turn_started_on_call = llm_call_index
    elif state.tool_phase_closed:
        state.forced_tool_choice_applied = False
        state.forced_tool_name = ""
        remove_tools = True
        remove_tool_choice = True
        remove_reasoning_effort = "reasoning_effort" in original_payload
        effective_tool_choice = None
        effective_reasoning_effort = None
        provider_strategy = "answer_only_continuation"
        if state.deepseek_non_thinking_tool_turn_active:
            if raw_extra_body is not None and not isinstance(raw_extra_body, dict):
                raise RunSqlRequirementError(
                    "DeepSeek answer-only 续轮覆盖失败：extra_body 不是映射"
                )
            effective_extra_body = deepcopy(raw_extra_body) if raw_extra_body else {}
            effective_extra_body["thinking"] = {"type": "disabled"}
            effective_thinking = {"type": "disabled"}
            thinking_override_applied = True
            provider_strategy = "deepseek_non_thinking_answer_only_continuation"
    elif state.deepseek_non_thinking_tool_turn_active:
        state.forced_tool_choice_applied = False
        state.forced_tool_name = ""
        if raw_extra_body is not None and not isinstance(raw_extra_body, dict):
            _write_state(
                state,
                llm_call_index=llm_call_index,
                original_tool_choice=original_tool_choice,
                effective_tool_choice=None,
                run_sql_schema_present=run_sql_schema_present,
                provider_hostname=provider_hostname,
                model=model,
                provider_strategy="fail_closed_thinking_override",
                original_thinking=original_thinking,
                original_reasoning_effort=original_reasoning_effort,
                error_type="RunSqlRequirementError",
                failure_reason="extra_body is not a mapping",
            )
            raise RunSqlRequirementError(
                "DeepSeek 非 Thinking 工具链续轮覆盖失败：extra_body 不是映射"
            )
        effective_extra_body = deepcopy(raw_extra_body) if raw_extra_body else {}
        effective_extra_body["thinking"] = {"type": "disabled"}
        effective_thinking = {"type": "disabled"}
        effective_reasoning_effort = None
        remove_reasoning_effort = "reasoning_effort" in original_payload
        thinking_override_applied = True
        non_thinking_continuation_applied = True
        provider_strategy = "deepseek_non_thinking_tool_continuation"
    else:
        state.forced_tool_choice_applied = False
        state.forced_tool_name = ""

    _write_state(
        state,
        llm_call_index=llm_call_index,
        original_tool_choice=original_tool_choice,
        effective_tool_choice=effective_tool_choice,
        run_sql_schema_present=run_sql_schema_present,
        provider_hostname=provider_hostname,
        model=model,
        provider_strategy=provider_strategy,
        original_thinking=original_thinking,
        effective_thinking=effective_thinking,
        original_reasoning_effort=original_reasoning_effort,
        effective_reasoning_effort=effective_reasoning_effort,
        thinking_override_applied=thinking_override_applied,
        non_thinking_continuation_applied=non_thinking_continuation_applied,
        answer_only_continuation_applied=state.tool_phase_closed,
        final_payload_tools_present=not remove_tools and bool(tools),
        final_payload_tool_choice_present=(
            not remove_tool_choice and "tool_choice" in original_payload
        ),
    )
    return EffectiveRequestPolicy(
        effective_tool_choice=effective_tool_choice,
        effective_extra_body=(
            effective_extra_body if isinstance(effective_extra_body, dict) else None
        ),
        remove_reasoning_effort=remove_reasoning_effort,
        remove_tools=remove_tools,
        remove_tool_choice=remove_tool_choice,
        provider_strategy=provider_strategy,
    )


def mark_parent_llm_called() -> None:
    """在真正调用父类前标记；不产生重试或额外决策。"""
    state = get_run_sql_requirement()
    if state is None or not state.decisions:
        return
    state.decisions[-1]["parent_llm_called"] = True
    _write_trace_state(state)
