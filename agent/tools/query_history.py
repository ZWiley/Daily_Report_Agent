"""Tool: 查询历史执行记录"""

from __future__ import annotations
from typing import Any
from agent.tools.base import BaseTool, ToolResult


class QueryHistoryTool(BaseTool):
    name = "query_history"
    description = "查询最近的日报执行历史（状态、耗时、错误），用于辅助决策"
    parameters = {
        "type": "object",
        "properties": {
            "last_n": {"type": "integer", "description": "查询最近 N 条", "default": 7}
        },
        "required": [],
    }

    async def execute(self, last_n: int = 7, **kwargs: Any) -> ToolResult:
        from agent.scheduler import ExecutionHistory

        history = ExecutionHistory()
        records = history.get_recent(last_n)

        if not records:
            return ToolResult(success=True, data={"records": [], "summary": "暂无执行记录"})

        success_count = sum(1 for r in records if r.get("status") == "success")
        total = len(records)

        return ToolResult(
            success=True,
            data={
                "records": records,
                "summary": f"最近 {total} 次执行：{success_count} 成功，{total - success_count} 失败/异常，成功率 {success_count/total:.0%}",
            },
        )
