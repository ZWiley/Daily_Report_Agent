"""Tool: 调用 LLM 生成日报摘要"""

from __future__ import annotations
from typing import Any
from agent.tools.base import BaseTool, ToolResult
from agent.config import LLMConfig
from agent.models import DeveloperCommits


class GenerateSummaryTool(BaseTool):
    name = "generate_summary"
    description = "调用 LLM 将 commit 数据生成一份 Markdown 格式的团队日报"
    parameters = {
        "type": "object",
        "properties": {
            "report_date": {"type": "string", "description": "日报日期 YYYY-MM-DD"}
        },
        "required": ["report_date"],
    }

    def __init__(self, llm_config: LLMConfig, use_mock: bool = False) -> None:
        self._llm_config = llm_config
        self._use_mock = use_mock

    async def execute(self, developer_commits: list[DeveloperCommits] | None = None, report_date: str = "", **kwargs: Any) -> ToolResult:
        if developer_commits is None:
            return ToolResult(success=False, error="缺少 developer_commits 参数")

        if self._use_mock:
            from agent.llm.summarizer import MockLLMSummarizer
            summarizer = MockLLMSummarizer()
            content = await summarizer.generate_report(developer_commits, report_date)
        else:
            from agent.llm.summarizer import LLMSummarizer
            summarizer = LLMSummarizer(self._llm_config)
            try:
                content = await summarizer.generate_report(developer_commits, report_date)
            finally:
                await summarizer.close()

        return ToolResult(
            success=True,
            data=content,
            metadata={"length": len(content)},
        )
