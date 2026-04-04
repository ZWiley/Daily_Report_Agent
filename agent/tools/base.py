"""
Tool 基类与注册表
=================
Harness 标准接口：每个工具声明 name / description / parameters，
Agent Loop 据此选择和调用工具。
"""

from __future__ import annotations

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """工具执行结果 — 统一返回格式"""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self, max_len: int = 200) -> str:
        """生成给 Agent Loop 的简短摘要（注入上下文用）"""
        if not self.success:
            return f"[FAILED] {self.error or 'unknown error'}"
        if self.data is None:
            return "[OK] 执行成功，无返回数据"
        text = str(self.data)
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text


class BaseTool(ABC):
    """Agent 工具基类"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具，返回标准化结果"""
        ...

    async def safe_execute(self, **kwargs: Any) -> ToolResult:
        """带计时和异常捕获的安全执行入口"""
        start = time.monotonic()
        try:
            result = await self.execute(**kwargs)
            result.metadata["duration_ms"] = round((time.monotonic() - start) * 1000)
            return result
        except Exception as e:
            duration = round((time.monotonic() - start) * 1000)
            logger.error(f"❌ Tool [{self.name}] 执行异常: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=str(e),
                metadata={"duration_ms": duration},
            )

    def to_schema(self) -> dict[str, Any]:
        """输出 OpenAI function-calling 兼容的 JSON Schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表 — Agent Loop 通过此发现和获取工具"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def list_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 function-calling schema（供 LLM 选择）"""
        return [t.to_schema() for t in self._tools.values()]

    def list_descriptions(self) -> str:
        """返回所有工具的可读描述（供 System Prompt 注入）"""
        lines = []
        for t in self._tools.values():
            lines.append(f"- **{t.name}**: {t.description}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._tools)
