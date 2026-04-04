"""Tool: 日报质量检查（规则校验 — 不消耗额外 token）"""

from __future__ import annotations
import re
from typing import Any
from agent.tools.base import BaseTool, ToolResult


class CheckQualityTool(BaseTool):
    name = "check_report_quality"
    description = "检查日报质量：是否包含必要章节、内容长度、数据一致性"
    parameters = {
        "type": "object",
        "properties": {
            "report_content": {"type": "string", "description": "Markdown 日报内容"},
            "expected_developers": {"type": "integer", "description": "预期开发者数量"},
            "expected_commits": {"type": "integer", "description": "预期 commit 总数"},
        },
        "required": ["report_content"],
    }

    async def execute(
        self,
        report_content: str = "",
        expected_developers: int = 0,
        expected_commits: int = 0,
        **kwargs: Any,
    ) -> ToolResult:
        issues: list[str] = []

        # 基础长度检查
        if len(report_content) < 100:
            issues.append(f"内容过短（{len(report_content)} 字符，最低 100）")

        # 标题检查
        if "日报" not in report_content and "Daily" not in report_content:
            issues.append("缺少日报标题")

        # 开发者章节数检查（按活跃开发者评估）
        heading_count = len(re.findall(r"^###\s", report_content, re.MULTILINE))
        if expected_developers > 0:
            minimum_headings = max(1, expected_developers)
            if heading_count < minimum_headings:
                issues.append(
                    f"开发者章节不足（检测到 {heading_count} 个，预期至少 {minimum_headings} 个）"
                )

        # 数据行检查
        has_stats = bool(re.search(r"commits?", report_content, re.IGNORECASE))
        if not has_stats:
            issues.append("缺少 commit 统计数据")

        # 提交数一致性检查：汇总每个开发者的统计行
        if expected_commits > 0:
            stat_line_commits = [
                int(value)
                for value in re.findall(r"^>.*?(\d+)\s+commits?", report_content, re.IGNORECASE | re.MULTILINE)
            ]
            if not stat_line_commits:
                issues.append("缺少开发者级 commit 统计行")
            else:
                actual_commit_sum = sum(stat_line_commits)
                if actual_commit_sum != expected_commits:
                    issues.append(
                        f"commit 统计不一致（日报统计 {actual_commit_sum}，预期 {expected_commits}）"
                    )

        passed = len(issues) == 0
        score = max(0, 10 - len(issues) * 2)

        return ToolResult(
            success=True,
            data={
                "passed": passed,
                "score": score,
                "issues": issues,
            },
        )
