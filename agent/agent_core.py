"""
Agent Core — 日报 Agent 装配层
=============================
职责聚焦为：
1. 加载 Agent Spec
2. 组装 ToolRegistry
3. 组装轻量 Engine 内核（内置步骤 + 日报 hooks / fallback）
4. 为日报场景准备初始上下文并触发执行
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from agent.config import AgentConfig
from agent.engine import (
    AGENT_SPEC_FILE,
    DEFAULT_AGENT_SPEC,
    AgentEngine,
    AgentExecutionError,
    AgentTrace,
    EngineContext,
    build_execution_plan,
    load_agent_spec,
    normalize_agent_spec,
)
from agent.engine.runtime import (
    evaluate_output_results,
    sync_commit_collection,
    sync_markdown,
    use_fallback_report,
)
from agent.models import DailyReport
from agent.tools.base import ToolRegistry
from agent.tools.check_quality import CheckQualityTool
from agent.tools.collect_commits import CollectCommitsTool
from agent.tools.fetch_developers import FetchDevelopersTool
from agent.tools.generate_summary import GenerateSummaryTool
from agent.tools.query_history import QueryHistoryTool
from agent.tools.send_feishu_group import SendFeishuGroupTool
from agent.tools.write_feishu_doc import WriteFeishuDocTool

logger = logging.getLogger(__name__)


class DailyReportAgent:
    """日报场景的 Agent 装配器。"""

    def __init__(
        self,
        config: AgentConfig,
        use_mock: bool = False,
        agent_spec: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.use_mock = use_mock or config.gitlab.use_mock
        self._agent_spec = normalize_agent_spec(
            agent_spec or load_agent_spec(AGENT_SPEC_FILE)
        )
        self._guardrails = self._agent_spec.get("guardrails", {})
        self._allowed_tools = set(self._agent_spec.get("tools", []))
        self._execution_plan = build_execution_plan(self._agent_spec)

        self._tools = self._build_tool_registry()
        self.engine = self._build_engine()
        self.trace = self.engine.trace

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @tools.setter
    def tools(self, registry: ToolRegistry) -> None:
        self._tools = registry
        if hasattr(self, "engine"):
            self.engine.tools = registry

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(FetchDevelopersTool(self.config.feishu, self.use_mock))
        registry.register(
            CollectCommitsTool(
                self.config.gitlab,
                self.config.report_hours_lookback,
                self.use_mock,
            )
        )
        registry.register(GenerateSummaryTool(self.config.llm, self.use_mock))
        registry.register(CheckQualityTool())
        registry.register(SendFeishuGroupTool(self.config.feishu, self.use_mock))
        registry.register(WriteFeishuDocTool(self.config.feishu, self.use_mock))
        registry.register(QueryHistoryTool())
        return registry

    def _build_engine(self) -> AgentEngine:
        engine = AgentEngine(
            execution_plan=self._execution_plan,
            tools=self._tools,
            allowed_tools=self._allowed_tools,
            guardrails=self._guardrails,
            logger_instance=logger,
        )
        engine.register_hook("sync_commit_collection", sync_commit_collection)
        engine.register_hook("sync_markdown", sync_markdown)
        engine.register_hook("evaluate_output_results", evaluate_output_results)
        engine.register_fallback("use_fallback_report", use_fallback_report)
        return engine

    def build_context(
        self,
        report_date: str | None = None,
        initial_data: dict[str, Any] | None = None,
    ) -> EngineContext:
        actual_report_date = report_date or datetime.now().strftime("%Y-%m-%d")
        report = DailyReport(date=actual_report_date)
        context_data: dict[str, Any] = {
            "report": report,
            "report_date": actual_report_date,
            "guardrails": self._guardrails,
            "step_results": {},
            "output_results": {},
            "developers": [],
            "developer_commits": [],
            "active_developers": 0,
            "markdown_content": "",
            "runtime_errors": [],
        }
        if initial_data:
            context_data.update(initial_data)
        return EngineContext(context_data)

    async def run(self) -> DailyReport:
        """按 execution_plan 执行日报链路。"""
        start_time = time.monotonic()
        context = self.build_context()
        report: DailyReport = context.get("report")
        report_date = str(context.get("report_date", report.date))

        logger.info("=" * 60)
        logger.info("🤖 Daily Report Agent 启动 (Extensible Engine)")
        logger.info(f"📅 日期: {report_date}")
        logger.info(f"🔧 模式: {'Mock' if self.use_mock else '生产'}")
        logger.info(f"📐 执行计划: {', '.join(step.id for step in self.engine.execution_plan)}")
        logger.info(f"🛠️  工具: {', '.join(self.tools.list_names())}")
        logger.info("=" * 60)

        try:
            await self.engine.run(context)
        except AgentExecutionError as exc:
            self.engine.mark_failed(context, str(exc))
        finally:
            self.trace = self.engine.trace

        return self._finalize(report, start_time)

    def _finalize(self, report: DailyReport, start_time: float) -> DailyReport:
        elapsed = time.monotonic() - start_time
        self.trace.total_duration_ms = round(elapsed * 1000)
        status_value = getattr(report.status, "value", report.status)

        logger.info("\n" + "=" * 60)
        logger.info("🏁 Agent 执行完成")
        logger.info(f"   状态: {status_value}")
        logger.info(f"   步骤: {len(self.trace.steps)}")
        logger.info(f"   耗时: {elapsed:.2f}s")
        if report.errors:
            for err in report.errors:
                logger.warning(f"   ⚠️  {err}")
        logger.info("=" * 60)

        return report
