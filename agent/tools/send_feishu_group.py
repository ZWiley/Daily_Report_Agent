"""Tool: 推送日报到飞书群"""

from __future__ import annotations
from typing import Any
from agent.tools.base import BaseTool, ToolResult
from agent.config import FeishuConfig


class SendFeishuGroupTool(BaseTool):
    name = "send_to_feishu_group"
    description = "通过 Webhook 将日报推送到飞书群（卡片消息）"
    parameters = {
        "type": "object",
        "properties": {
            "markdown_content": {"type": "string", "description": "Markdown 日报内容"}
        },
        "required": ["markdown_content"],
    }

    def __init__(self, feishu_config: FeishuConfig, use_mock: bool = False) -> None:
        self._feishu_config = feishu_config
        self._use_mock = use_mock

    async def execute(self, markdown_content: str = "", **kwargs: Any) -> ToolResult:
        if not markdown_content:
            return ToolResult(success=False, error="日报内容为空")

        if not self._use_mock and not self._feishu_config.webhook_url:
            return ToolResult(success=True, data="skipped", metadata={"reason": "未配置 Webhook"})

        if self._use_mock:
            from agent.feishu.messenger import MockFeishuMessenger
            messenger = MockFeishuMessenger()
        else:
            from agent.feishu.messenger import FeishuMessenger
            messenger = FeishuMessenger(self._feishu_config)

        try:
            ok = await messenger.send_webhook(markdown_content)
            return ToolResult(success=ok, data="sent" if ok else "send_failed")
        finally:
            if hasattr(messenger, "close"):
                await messenger.close()
