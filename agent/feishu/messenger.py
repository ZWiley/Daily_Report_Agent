"""
飞书消息推送器
==============
支持两种方式推送日报：
  1. 群机器人 Webhook（简单，推荐）
  2. 飞书 API 发送消息到指定会话
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.config import FeishuConfig

logger = logging.getLogger(__name__)


class FeishuMessenger:
    """飞书消息推送器"""

    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> FeishuMessenger:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if not self._client.is_closed:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def send_webhook(self, markdown_content: str, title: str = "🤖 Astribot 团队日报") -> bool:
        """
        通过飞书群机器人 Webhook 发送富文本消息。

        Args:
            markdown_content: Markdown 格式的日报内容
            title: 消息卡片标题

        Returns:
            是否发送成功
        """
        if not self.config.webhook_url:
            logger.warning("⚠️  未配置飞书 Webhook URL，跳过推送")
            return False

        # 构建飞书交互卡片消息（Interactive Card）
        card = self._build_card(title, markdown_content)

        payload = {
            "msg_type": "interactive",
            "card": card,
        }

        resp = await self._client.post(
            self.config.webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info("✅ 日报已成功推送到飞书群")
            return True
        else:
            logger.error(f"❌ 飞书推送失败: {result}")
            return False

    def _build_card(self, title: str, markdown_content: str) -> dict[str, Any]:
        """
        构建飞书交互卡片（Interactive Card）。
        将 Markdown 分段转换为卡片元素。
        """
        # 将 Markdown 按分隔线拆分为多个区块
        sections = self._split_markdown_sections(markdown_content)

        elements: list[dict[str, Any]] = []

        for section in sections:
            if section.strip() == "---":
                elements.append({"tag": "hr"})
            elif section.strip():
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": section.strip(),
                    },
                })

        # 添加底部注释
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "🤖 由 Astribot Daily Agent 自动生成 | Powered by LLM",
                }
            ],
        })

        return {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
                "template": "blue",
            },
            "elements": elements,
        }

    @staticmethod
    def _split_markdown_sections(content: str) -> list[str]:
        """将 Markdown 内容按段落和分隔线拆分"""
        lines = content.split("\n")
        sections: list[str] = []
        current_section: list[str] = []

        for line in lines:
            if line.strip() == "---":
                if current_section:
                    sections.append("\n".join(current_section))
                    current_section = []
                sections.append("---")
            else:
                current_section.append(line)

        if current_section:
            sections.append("\n".join(current_section))

        return sections

    async def send_markdown_simple(self, markdown_content: str) -> bool:
        """
        简化版：直接发送 Markdown 文本消息。
        适用于不支持卡片的场景。
        """
        if not self.config.webhook_url:
            logger.warning("⚠️  未配置飞书 Webhook URL，跳过推送")
            return False

        payload = {
            "msg_type": "text",
            "content": {
                "text": markdown_content,
            },
        }

        resp = await self._client.post(
            self.config.webhook_url,
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json()

        success = result.get("code") == 0 or result.get("StatusCode") == 0
        if success:
            logger.info("✅ 日报已成功推送到飞书群（纯文本模式）")
        else:
            logger.error(f"❌ 飞书推送失败: {result}")
        return success


class MockFeishuMessenger:
    """Mock 飞书推送器（将日报输出到控制台和文件）"""

    async def send_webhook(self, markdown_content: str, title: str = "🤖 Astribot 团队日报") -> bool:
        """Mock 推送：打印到日志并保存文件"""
        from pathlib import Path
        from datetime import datetime

        # 保存到本地文件
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)

        filename = f"daily_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        filepath = output_dir / filename

        full_content = f"# {title}\n\n{markdown_content}"
        filepath.write_text(full_content, encoding="utf-8")

        logger.info(f"📄 [Mock] 日报已保存到: {filepath}")
        logger.info(f"📨 [Mock] 模拟推送到飞书群成功")

        # 同时输出到控制台
        print("\n" + "=" * 60)
        print(f"  📨 飞书群推送预览: {title}")
        print("=" * 60)
        print(full_content)
        print("=" * 60 + "\n")

        return True
