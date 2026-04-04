"""Tool: 将日报写入飞书文档归档"""

from __future__ import annotations
from typing import Any
from agent.tools.base import BaseTool, ToolResult
from agent.config import FeishuConfig


class WriteFeishuDocTool(BaseTool):
    name = "write_to_feishu_doc"
    description = "将日报写入飞书文档（追加到已有文档或每天新建文档）"
    parameters = {
        "type": "object",
        "properties": {
            "markdown_content": {"type": "string", "description": "Markdown 日报内容"},
            "report_date": {"type": "string", "description": "日报日期 YYYY-MM-DD"},
        },
        "required": ["markdown_content", "report_date"],
    }

    def __init__(self, feishu_config: FeishuConfig, use_mock: bool = False) -> None:
        self._feishu_config = feishu_config
        self._use_mock = use_mock

    async def execute(self, markdown_content: str = "", report_date: str = "", **kwargs: Any) -> ToolResult:
        if not markdown_content:
            return ToolResult(success=False, error="日报内容为空")

        has_doc_config = bool(self._feishu_config.doc_id or self._feishu_config.doc_folder_token)
        if not self._use_mock and not has_doc_config:
            return ToolResult(success=True, data="skipped", metadata={"reason": "未配置文档目标"})

        if self._use_mock:
            from agent.feishu.docwriter import MockFeishuDocWriter
            writer = MockFeishuDocWriter()
        else:
            from agent.feishu.docwriter import FeishuDocWriter
            writer = FeishuDocWriter(self._feishu_config)

        try:
            ok = await writer.write_report(markdown_content, report_date)
            return ToolResult(success=ok, data="written" if ok else "write_failed")
        finally:
            if hasattr(writer, "close"):
                await writer.close()
