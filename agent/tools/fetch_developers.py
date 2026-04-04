"""Tool: 从飞书多维表格读取开发者名单"""

from __future__ import annotations
from typing import Any
from agent.tools.base import BaseTool, ToolResult
from agent.config import FeishuConfig


class FetchDevelopersTool(BaseTool):
    name = "fetch_developers"
    description = "从飞书多维表格读取开发者名单（姓名、GitLab 用户名）"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, feishu_config: FeishuConfig, use_mock: bool = False) -> None:
        self._feishu_config = feishu_config
        self._use_mock = use_mock

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self._use_mock:
            from agent.feishu.bitable import MockBitableReader
            reader = MockBitableReader()
            devs = await reader.fetch_developers()
        else:
            from agent.feishu.bitable import BitableReader
            reader = BitableReader(self._feishu_config)
            try:
                devs = await reader.fetch_developers()
            finally:
                await reader.close()

        return ToolResult(
            success=True,
            data=devs,
            metadata={"count": len(devs)},
        )
