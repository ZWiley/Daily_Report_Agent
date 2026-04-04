"""Tool: 从 GitLab 采集开发者 commit 记录"""

from __future__ import annotations
from typing import Any
from agent.tools.base import BaseTool, ToolResult
from agent.config import GitLabConfig
from agent.models import Developer


class CollectCommitsTool(BaseTool):
    name = "collect_commits"
    description = "从 GitLab 采集指定开发者列表在过去 N 小时内的所有 commit 记录，按仓库分组返回"
    parameters = {
        "type": "object",
        "properties": {
            "hours_lookback": {
                "type": "integer",
                "description": "回溯小时数",
                "default": 24,
            }
        },
        "required": [],
    }

    def __init__(self, gitlab_config: GitLabConfig, hours_lookback: int = 24, use_mock: bool = False) -> None:
        self._gitlab_config = gitlab_config
        self._hours_lookback = hours_lookback
        self._use_mock = use_mock

    async def execute(self, developers: list[Developer] | None = None, hours_lookback: int | None = None, **kwargs: Any) -> ToolResult:
        if developers is None:
            return ToolResult(success=False, error="缺少 developers 参数")

        lookback = hours_lookback or self._hours_lookback

        if self._use_mock:
            from agent.gitlab.collector import MockGitLabCollector
            collector = MockGitLabCollector(hours_lookback=lookback)
            commits = await collector.collect_all(developers)
        else:
            from agent.gitlab.collector import GitLabCollector
            collector = GitLabCollector(self._gitlab_config, hours_lookback=lookback)
            try:
                commits = await collector.collect_all(developers)
            finally:
                await collector.close()

        total = sum(dc.total_commits for dc in commits)
        active = sum(1 for dc in commits if dc.total_commits > 0)
        failed_developers = [
            dc.developer.name for dc in commits if getattr(dc, "collection_error", "")
        ]

        if developers and len(failed_developers) == len(developers):
            return ToolResult(
                success=False,
                error="GitLab 采集全部失败：" + "、".join(failed_developers),
                metadata={"failed_developers": failed_developers},
            )

        return ToolResult(
            success=True,
            data=commits,
            metadata={
                "total_commits": total,
                "active_developers": active,
                "failed_developers": failed_developers,
                "partial_failure": bool(failed_developers),
            },
        )
