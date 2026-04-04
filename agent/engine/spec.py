"""Agent spec / execution plan 的加载、归一化与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT_SPEC_FILE = Path(__file__).resolve().parent.parent / "agent.json"

DEFAULT_AGENT_SPEC: dict[str, Any] = {
    "name": "daily-report-agent",
    "version": "2.0.0",
    "tools": [
        "fetch_developers",
        "collect_commits",
        "generate_summary",
        "check_report_quality",
        "send_to_feishu_group",
        "write_to_feishu_doc",
        "query_history",
    ],
    "guardrails": {
        "max_steps": 12,
        "max_retries": 2,
        "token_budget": 20000,
        "required_quality_score": 6,
        "blocked_tools": [],
    },
    "memory": {
        "short_term": "current_run_context",
        "long_term": "data/execution_history.json",
    },
    "schedule": {
        "default_cron": "0 9 * * 1-5",
        "timezone": "Asia/Shanghai",
    },
    "execution_plan": [
        {
            "id": "fetch_developers",
            "kind": "tool",
            "tool": "fetch_developers",
            "store": "developers",
            "failure_message": "读取开发者名单失败",
            "on_failure": "fail",
        },
        {
            "id": "collect_commits",
            "kind": "tool",
            "tool": "collect_commits",
            "params": {"developers": "$developers"},
            "store": "developer_commits",
            "after_step": "sync_commit_collection",
            "failure_message": "GitLab 采集失败",
            "on_failure": "fail",
        },
        {
            "id": "generate_report",
            "kind": "summary_loop",
            "generate_tool": "generate_summary",
            "quality_tool": "check_report_quality",
            "generate_params": {
                "developer_commits": "$developer_commits",
                "report_date": "$report_date",
            },
            "quality_params": {
                "report_content": "$markdown_content",
                "expected_developers": "$active_developers",
                "expected_commits": "$report.total_commits",
            },
            "store": "markdown_content",
            "max_retries": "$guardrails.max_retries",
            "min_quality": "$guardrails.required_quality_score",
            "fallback_action": "use_fallback_report",
            "after_step": "sync_markdown",
            "failure_message": "LLM 生成失败",
        },
        {
            "id": "deliver_report",
            "kind": "output_group",
            "channels": [
                {
                    "id": "feishu_group",
                    "tool": "send_to_feishu_group",
                    "params": {"markdown_content": "$markdown_content"},
                    "success_values": ["sent"],
                    "skip_values": ["skipped"],
                    "failure_message": "飞书群推送失败",
                },
                {
                    "id": "feishu_doc",
                    "tool": "write_to_feishu_doc",
                    "params": {
                        "markdown_content": "$markdown_content",
                        "report_date": "$report_date",
                    },
                    "success_values": ["written"],
                    "skip_values": ["skipped"],
                    "failure_message": "飞书文档写入失败",
                },
            ],
            "after_step": "evaluate_output_results",
        },
    ],
}


class AgentExecutionError(RuntimeError):
    """执行计划或运行期出现不可恢复错误。"""


@dataclass
class ExecutionStep:
    """声明式执行步骤定义。"""

    id: str
    kind: str = "tool"
    tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    store: str | None = None
    failure_message: str = ""
    on_failure: str = "fail"
    after_step: str | None = None
    channels: list[dict[str, Any]] = field(default_factory=list)
    generate_tool: str | None = None
    quality_tool: str | None = None
    generate_params: dict[str, Any] = field(default_factory=dict)
    quality_params: dict[str, Any] = field(default_factory=dict)
    max_retries: Any = None
    min_quality: Any = None
    fallback_action: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExecutionStep":
        return cls(
            id=raw["id"],
            kind=raw.get("kind", "tool"),
            tool=raw.get("tool"),
            params=raw.get("params", {}),
            store=raw.get("store"),
            failure_message=raw.get("failure_message", ""),
            on_failure=raw.get("on_failure", "fail"),
            after_step=raw.get("after_step"),
            channels=raw.get("channels", []),
            generate_tool=raw.get("generate_tool"),
            quality_tool=raw.get("quality_tool"),
            generate_params=raw.get("generate_params", {}),
            quality_params=raw.get("quality_params", {}),
            max_retries=raw.get("max_retries"),
            min_quality=raw.get("min_quality"),
            fallback_action=raw.get("fallback_action"),
        )


def _deep_copy_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(spec))


def load_agent_spec(spec_path: Path | None = None) -> dict[str, Any]:
    path = spec_path or AGENT_SPEC_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return _deep_copy_spec(DEFAULT_AGENT_SPEC)


def normalize_agent_spec(
    spec: dict[str, Any],
    default_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _deep_copy_spec(default_spec or DEFAULT_AGENT_SPEC)
    for key, value in spec.items():
        if isinstance(value, dict) and isinstance(normalized.get(key), dict):
            normalized[key].update(value)
        else:
            normalized[key] = value
    return normalized


def build_execution_plan(spec: dict[str, Any]) -> list[ExecutionStep]:
    raw_steps = spec.get("execution_plan") or DEFAULT_AGENT_SPEC["execution_plan"]
    steps = [ExecutionStep.from_dict(raw) for raw in raw_steps]
    if not steps:
        raise AgentExecutionError("execution_plan 不能为空")

    seen: set[str] = set()
    for step in steps:
        if step.id in seen:
            raise AgentExecutionError(f"execution_plan 存在重复 step id: {step.id}")
        seen.add(step.id)
    return steps
