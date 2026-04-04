"""
Agent Core 主链路测试
====================
覆盖声明式 Harness 的关键语义：execution_plan、质量门、输出组与 guardrails。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from agent.agent_core import DEFAULT_AGENT_SPEC, DailyReportAgent
from agent.config import AgentConfig
from agent.models import CommitInfo, Developer, DeveloperCommits, ReportStatus
from agent.tools.base import ToolResult


class DummyTool:
    def __init__(self, result: ToolResult | list[ToolResult]) -> None:
        if isinstance(result, list):
            self._results = result
        else:
            self._results = [result]
        self.calls: list[dict] = []

    async def safe_execute(self, **kwargs):
        self.calls.append(kwargs)
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


class DummyRegistry:
    def __init__(self, mapping: dict[str, ToolResult | list[ToolResult]]) -> None:
        self._mapping = {name: DummyTool(result) for name, result in mapping.items()}

    def get(self, name: str):
        return self._mapping.get(name)

    def list_names(self) -> list[str]:
        return list(self._mapping.keys())


def _build_commit(project_name: str = "repo-a") -> CommitInfo:
    return CommitInfo(
        sha="a" * 40,
        short_sha="aaaaaaaa",
        message="feat: 完成日报功能",
        authored_date=datetime.now(timezone.utc),
        project_name=project_name,
        additions=10,
        deletions=2,
    )


def _build_report_markdown() -> str:
    return (
        "## 📊 Astribot 团队日报 | 2026-04-04\n\n"
        "### 👤 张伟\n\n"
        "**repo-a** — 完成日报功能\n\n"
        "> 📈 1 commits | +10 / -2 lines\n"
    )


def _build_success_mapping() -> tuple[Developer, DeveloperCommits, dict[str, ToolResult]]:
    developer = Developer(name="张伟", gitlab_username="zhang.wei")
    dc = DeveloperCommits(developer=developer, commits=[_build_commit()])
    dc.compute_stats()
    return developer, dc, {
        "fetch_developers": ToolResult(success=True, data=[developer]),
        "collect_commits": ToolResult(success=True, data=[dc]),
        "generate_summary": ToolResult(success=True, data=_build_report_markdown()),
        "check_report_quality": ToolResult(
            success=True,
            data={"passed": True, "score": 10, "issues": []},
        ),
        "send_to_feishu_group": ToolResult(success=True, data="sent"),
        "write_to_feishu_doc": ToolResult(success=True, data="written"),
    }


@pytest.mark.asyncio
async def test_agent_fails_when_all_output_channels_are_skipped() -> None:
    """两个输出通道都 skipped 时，应判定为失败而不是 success。"""
    developer, dc, mapping = _build_success_mapping()
    mapping["fetch_developers"] = ToolResult(success=True, data=[developer])
    mapping["collect_commits"] = ToolResult(success=True, data=[dc])
    mapping["send_to_feishu_group"] = ToolResult(success=True, data="skipped")
    mapping["write_to_feishu_doc"] = ToolResult(success=True, data="skipped")

    agent = DailyReportAgent(AgentConfig(), use_mock=False)
    agent.tools = DummyRegistry(mapping)

    report = await agent.run()

    assert report.status == ReportStatus.FAILED
    assert any("未配置任何可用输出通道" in error for error in report.errors)


@pytest.mark.asyncio
async def test_agent_marks_partial_when_gitlab_collection_partially_fails() -> None:
    """GitLab 局部采集失败时，Agent 应保留结果但标记为 partial。"""
    ok_developer = Developer(name="张伟", gitlab_username="zhang.wei")
    failed_developer = Developer(name="李娜", gitlab_username="li.na")

    ok_dc = DeveloperCommits(developer=ok_developer, commits=[_build_commit()])
    ok_dc.compute_stats()
    failed_dc = DeveloperCommits(
        developer=failed_developer,
        collection_error="GitLab 429 rate limited",
    )

    agent = DailyReportAgent(AgentConfig(), use_mock=False)
    agent.tools = DummyRegistry(
        {
            "fetch_developers": ToolResult(success=True, data=[ok_developer, failed_developer]),
            "collect_commits": ToolResult(
                success=True,
                data=[ok_dc, failed_dc],
                metadata={
                    "failed_developers": [failed_developer.name],
                    "partial_failure": True,
                },
            ),
            "generate_summary": ToolResult(success=True, data=_build_report_markdown()),
            "check_report_quality": ToolResult(
                success=True,
                data={"passed": True, "score": 10, "issues": []},
            ),
            "send_to_feishu_group": ToolResult(success=True, data="sent"),
            "write_to_feishu_doc": ToolResult(success=True, data="written"),
        }
    )

    report = await agent.run()

    assert report.status == ReportStatus.PARTIAL
    assert any("GitLab 部分采集失败" in error for error in report.errors)


@pytest.mark.asyncio
async def test_agent_uses_fallback_report_after_summary_retries_are_exhausted() -> None:
    """当摘要连续失败时，应走 fallback_action 而不是直接崩掉。"""
    _, _, mapping = _build_success_mapping()
    mapping["generate_summary"] = [
        ToolResult(success=False, error="llm timeout #1"),
        ToolResult(success=False, error="llm timeout #2"),
        ToolResult(success=False, error="llm timeout #3"),
    ]

    agent = DailyReportAgent(AgentConfig(), use_mock=False)
    agent.tools = DummyRegistry(mapping)

    report = await agent.run()

    assert report.status == ReportStatus.PARTIAL
    assert "LLM 摘要服务暂不可用" in report.markdown_content
    assert any("已使用降级模板" in error for error in report.errors)


@pytest.mark.asyncio
async def test_agent_respects_max_steps_guardrail() -> None:
    """max_steps 不再是摆设，超限时应立即失败。"""
    _, _, mapping = _build_success_mapping()
    custom_spec = deepcopy(DEFAULT_AGENT_SPEC)
    custom_spec["guardrails"]["max_steps"] = 1

    agent = DailyReportAgent(AgentConfig(), use_mock=False, agent_spec=custom_spec)
    agent.tools = DummyRegistry(mapping)

    report = await agent.run()

    assert report.status == ReportStatus.FAILED
    assert any("max_steps=1" in error for error in report.errors)
    assert len(agent.trace.steps) == 1


@pytest.mark.asyncio
async def test_agent_respects_blocked_tools_guardrail() -> None:
    """blocked_tools 被声明后，对应输出工具应被真正拦截。"""
    _, _, mapping = _build_success_mapping()
    custom_spec = deepcopy(DEFAULT_AGENT_SPEC)
    custom_spec["guardrails"]["blocked_tools"] = ["send_to_feishu_group"]

    agent = DailyReportAgent(AgentConfig(), use_mock=False, agent_spec=custom_spec)
    agent.tools = DummyRegistry(mapping)

    report = await agent.run()

    assert report.status == ReportStatus.PARTIAL
    assert any("工具被 guardrail 禁用: send_to_feishu_group" in error for error in report.errors)
