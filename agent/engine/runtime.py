"""声明式 Agent Engine 运行时。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from agent.engine.context import EngineContext
from agent.engine.spec import AgentExecutionError, ExecutionStep
from agent.models import DailyReport, DeveloperCommits
from agent.tools.base import ToolRegistry, ToolResult

ExecutorHandler = Callable[[ExecutionStep, EngineContext, "AgentEngine"], Awaitable[bool]]
HookHandler = Callable[[ExecutionStep, EngineContext, "AgentEngine", Optional[ToolResult]], None]
FallbackHandler = Callable[[ExecutionStep, EngineContext, "AgentEngine"], None]


@dataclass
class StepTrace:
    """单步执行追踪。"""

    step: int
    tool_name: str
    params_summary: str
    success: bool
    duration_ms: int = 0
    summary: str = ""
    stage_id: str = ""


@dataclass
class AgentTrace:
    """完整执行追踪。"""

    steps: list[StepTrace] = field(default_factory=list)
    total_duration_ms: int = 0

    def add(self, trace: StepTrace) -> None:
        self.steps.append(trace)
        self.steps.sort(key=lambda item: item.step)

    def to_dict(self) -> list[dict[str, Any]]:
        return [
            {
                "step": step.step,
                "stage_id": step.stage_id,
                "tool": step.tool_name,
                "success": step.success,
                "duration_ms": step.duration_ms,
                "summary": step.summary,
            }
            for step in self.steps
        ]


class AgentEngine:
    """声明式执行引擎。"""

    def __init__(
        self,
        execution_plan: list[ExecutionStep],
        tools: ToolRegistry,
        *,
        allowed_tools: set[str] | None = None,
        guardrails: dict[str, Any] | None = None,
        logger_instance: logging.Logger | None = None,
    ) -> None:
        self.execution_plan = list(execution_plan)
        self.tools = tools
        self.allowed_tools = set(allowed_tools or [])
        self.guardrails = dict(guardrails or {})
        self.logger = logger_instance or logging.getLogger(__name__)
        self.trace = AgentTrace()
        self._executors: dict[str, ExecutorHandler] = {
            "tool": self._execute_tool_step,
            "summary_loop": self._execute_summary_loop_step,
            "output_group": self._execute_output_group_step,
        }
        self._hooks: dict[str, HookHandler] = {}
        self._fallbacks: dict[str, FallbackHandler] = {}

    def register_executor(self, kind: str, handler: ExecutorHandler) -> None:
        self._executors[kind] = handler

    def register_hook(self, name: str, handler: HookHandler) -> None:
        self._hooks[name] = handler

    def register_fallback(self, name: str, handler: FallbackHandler) -> None:
        self._fallbacks[name] = handler

    async def run(self, context: EngineContext) -> EngineContext:
        start_time = time.monotonic()
        self.trace = AgentTrace()
        context.setdefault("step_results", {})
        context.setdefault("output_results", {})
        context.setdefault("runtime_errors", [])

        try:
            for step in self.execution_plan:
                executor = self._executors.get(step.kind)
                if executor is None:
                    raise AgentExecutionError(f"未知步骤类型: {step.kind}")

                should_continue = await executor(step, context, self)
                if not should_continue:
                    break
        finally:
            self.trace.total_duration_ms = round((time.monotonic() - start_time) * 1000)

        return context

    async def execute_tool(
        self,
        tool_name: str,
        *,
        stage_id: str,
        params: dict[str, Any] | None = None,
        step_number: int | None = None,
    ) -> ToolResult:
        actual_step = step_number if step_number is not None else self.next_step_number(tool_name)
        payload = params or {}
        self.logger.info(f"\n🔧 [{actual_step}] {stage_id} → {tool_name}")

        if self.allowed_tools and tool_name not in self.allowed_tools:
            result = ToolResult(success=False, error=f"工具未在 agent.json 中声明: {tool_name}")
            self._trace_tool_result(actual_step, stage_id, tool_name, payload, result)
            return result

        blocked_tools = set(self.guardrails.get("blocked_tools", []))
        if tool_name in blocked_tools:
            result = ToolResult(success=False, error=f"工具被 guardrail 禁用: {tool_name}")
            self._trace_tool_result(actual_step, stage_id, tool_name, payload, result)
            return result

        tool = self.tools.get(tool_name)
        if tool is None:
            result = ToolResult(success=False, error=f"未知工具: {tool_name}")
            self._trace_tool_result(actual_step, stage_id, tool_name, payload, result)
            return result

        result = await tool.safe_execute(**payload)
        self._trace_tool_result(actual_step, stage_id, tool_name, payload, result)
        return result

    def next_step_number(self, tool_name: str) -> int:
        max_steps = int(self.guardrails.get("max_steps", 12))
        next_step = len(self.trace.steps) + 1
        if next_step > max_steps:
            raise AgentExecutionError(
                f"超出执行步数上限 max_steps={max_steps}，即将执行工具: {tool_name}"
            )
        return next_step

    def record_step_result(self, context: EngineContext, key: str, result: ToolResult) -> None:
        context.setdefault("step_results", {})[key] = result

    def run_hook(
        self,
        hook_name: str,
        step: ExecutionStep,
        context: EngineContext,
        result: Optional[ToolResult] = None,
    ) -> None:
        hook = self._hooks.get(hook_name)
        if hook is None:
            raise AgentExecutionError(f"未知 after_step hook: {hook_name}")
        hook(step, context, self, result)

    def run_fallback(self, fallback_name: str, step: ExecutionStep, context: EngineContext) -> None:
        fallback = self._fallbacks.get(fallback_name)
        if fallback is None:
            raise AgentExecutionError(f"未知 fallback_action: {fallback_name}")
        fallback(step, context, self)

    def handle_step_failure(
        self,
        step: ExecutionStep,
        result: ToolResult,
        context: EngineContext,
    ) -> bool:
        error_message = step.failure_message or f"步骤失败: {step.id}"
        if result.error:
            error_message = f"{error_message}: {result.error}"

        if step.on_failure == "continue":
            self.append_error(context, error_message)
            return True

        if step.on_failure == "partial":
            self.mark_partial(context, error_message)
            return True

        self.mark_failed(context, error_message)
        return False

    def append_error(self, context: EngineContext, message: str) -> None:
        context.setdefault("runtime_errors", []).append(message)
        report = context.get("report")
        report_errors = getattr(report, "errors", None)
        if isinstance(report_errors, list):
            report_errors.append(message)

    def mark_partial(self, context: EngineContext, message: str | None = None) -> None:
        self._set_status(context, "partial")
        if message:
            self.append_error(context, message)

    def mark_failed(self, context: EngineContext, message: str | None = None) -> None:
        self._set_status(context, "failed")
        if message:
            self.append_error(context, message)

    def _set_status(self, context: EngineContext, status_name: str) -> None:
        context["engine_status"] = status_name
        report = context.get("report")
        if report is None or not hasattr(report, "status"):
            return

        current_status = getattr(report, "status", None)
        status_type = current_status.__class__ if current_status is not None else None
        enum_attr = status_name.upper()
        if status_type is not None and hasattr(status_type, enum_attr):
            setattr(report, "status", getattr(status_type, enum_attr))
        else:
            setattr(report, "status", status_name)

    async def _execute_tool_step(
        self,
        step: ExecutionStep,
        context: EngineContext,
        engine: "AgentEngine",
    ) -> bool:
        del engine
        if not step.tool:
            raise AgentExecutionError(f"step {step.id} 缺少 tool 字段")

        params = context.resolve(step.params)
        result = await self.execute_tool(step.tool, stage_id=step.id, params=params)
        self.record_step_result(context, step.id, result)

        if result.success and step.store:
            context.set(step.store, result.data)

        if result.success and step.after_step:
            self.run_hook(step.after_step, step, context, result=result)

        if result.success:
            return True
        return self.handle_step_failure(step, result, context)

    async def _execute_summary_loop_step(
        self,
        step: ExecutionStep,
        context: EngineContext,
        engine: "AgentEngine",
    ) -> bool:
        del engine
        if not step.generate_tool or not step.quality_tool:
            raise AgentExecutionError(
                f"step {step.id} 缺少 generate_tool 或 quality_tool 字段"
            )

        raw_retries = context.resolve(step.max_retries)
        raw_min_quality = context.resolve(step.min_quality)
        max_retries = int(
            raw_retries if raw_retries is not None else self.guardrails.get("max_retries", 2)
        )
        min_quality = int(
            raw_min_quality
            if raw_min_quality is not None
            else self.guardrails.get("required_quality_score", 6)
        )

        latest_markdown = str(context.get("markdown_content", ""))

        for attempt in range(max_retries + 1):
            generate_label = f"{step.id}.generate#{attempt + 1}"
            generate_params = context.resolve(step.generate_params)
            generate_result = await self.execute_tool(
                step.generate_tool,
                stage_id=generate_label,
                params=generate_params,
            )
            self.record_step_result(context, generate_label, generate_result)

            if not generate_result.success:
                self.append_error(
                    context,
                    f"{step.failure_message or '生成摘要失败'}: {generate_result.error}",
                )
                if attempt == max_retries:
                    if step.fallback_action:
                        self.run_fallback(step.fallback_action, step, context)
                        if step.after_step:
                            self.run_hook(step.after_step, step, context)
                        return True
                    self.mark_failed(context, "摘要生成失败且未配置 fallback_action")
                    return False
                continue

            latest_markdown = generate_result.data or ""
            if step.store:
                context.set(step.store, latest_markdown)

            quality_label = f"{step.id}.quality#{attempt + 1}"
            quality_params = context.resolve(step.quality_params)
            quality_result = await self.execute_tool(
                step.quality_tool,
                stage_id=quality_label,
                params=quality_params,
            )
            self.record_step_result(context, quality_label, quality_result)

            if not quality_result.success:
                score = 0
                passed = False
                issues = [f"质量检查失败: {quality_result.error}"]
            else:
                quality = quality_result.data or {}
                score = int(quality.get("score", 0))
                passed = bool(quality.get("passed", False))
                issues = list(quality.get("issues", []))

            self.logger.info(
                f"   质量评分: {score}/10 | {'✅ 通过' if passed else '❌ 未通过'}"
            )
            for issue in issues:
                self.logger.info(f"   - {issue}")

            if passed or score >= min_quality:
                if step.after_step:
                    self.run_hook(step.after_step, step, context, result=generate_result)
                return True

            if attempt < max_retries:
                self.logger.warning(
                    f"   🔄 质量不合格，将重新生成 ({attempt + 1}/{max_retries})"
                )
            else:
                self.logger.warning("   ⚠️  重试耗尽，保留当前版本")

        if step.store:
            context.set(step.store, latest_markdown)
        if step.after_step:
            self.run_hook(step.after_step, step, context)
        return True

    async def _execute_output_group_step(
        self,
        step: ExecutionStep,
        context: EngineContext,
        engine: "AgentEngine",
    ) -> bool:
        del engine
        if not step.channels:
            raise AgentExecutionError(f"step {step.id} 缺少 channels 配置")

        tasks = []
        channel_specs: list[dict[str, Any]] = []
        for channel in step.channels:
            tool_name = channel.get("tool")
            channel_id = channel.get("id", tool_name or "channel")
            if not tool_name:
                raise AgentExecutionError(f"step {step.id} 的 channel 缺少 tool 字段")

            step_number = self.next_step_number(tool_name)
            params = context.resolve(channel.get("params", {}))
            channel_specs.append(channel)
            tasks.append(
                self.execute_tool(
                    tool_name,
                    stage_id=f"{step.id}.{channel_id}",
                    params=params,
                    step_number=step_number,
                )
            )

        results = await asyncio.gather(*tasks)
        output_results = context.setdefault("output_results", {})
        for channel, result in zip(channel_specs, results):
            channel_id = channel.get("id", channel.get("tool", "channel"))
            output_results[channel_id] = result
            self.record_step_result(context, f"{step.id}.{channel_id}", result)

        if step.after_step:
            self.run_hook(step.after_step, step, context)
        return True

    def _trace_tool_result(
        self,
        step_number: int,
        stage_id: str,
        tool_name: str,
        params: dict[str, Any],
        result: ToolResult,
    ) -> None:
        self.trace.add(
            StepTrace(
                step=step_number,
                stage_id=stage_id,
                tool_name=tool_name,
                params_summary=str(list(params.keys())),
                success=result.success,
                duration_ms=result.metadata.get("duration_ms", 0),
                summary=result.summary(100),
            )
        )

        status_icon = "✅" if result.success else "❌"
        duration_ms = result.metadata.get("duration_ms", 0)
        self.logger.info(f"   {status_icon} {tool_name} ({duration_ms}ms)")


def sync_commit_collection(
    step: ExecutionStep,
    context: EngineContext,
    engine: AgentEngine,
    result: Optional[ToolResult] = None,
) -> None:
    del step
    developer_commits: list[DeveloperCommits] = list(context.get("developer_commits", []))
    report = context.get("report")
    if isinstance(report, DailyReport):
        report.developer_summaries = developer_commits
        report.compute_totals()

    context.set(
        "active_developers",
        sum(1 for dc in developer_commits if dc.total_commits > 0),
    )

    failed_developers = []
    if result is not None:
        failed_developers = list(result.metadata.get("failed_developers", []))

    if failed_developers:
        engine.mark_partial(
            context,
            "GitLab 部分采集失败：" + "、".join(failed_developers),
        )


def sync_markdown(
    step: ExecutionStep,
    context: EngineContext,
    engine: AgentEngine,
    result: Optional[ToolResult] = None,
) -> None:
    del step, engine, result
    report = context.get("report")
    if isinstance(report, DailyReport):
        report.markdown_content = str(context.get("markdown_content", ""))


def build_fallback_report(
    developer_commits: list[DeveloperCommits],
    report_date: str,
) -> str:
    lines = [
        f"## 📊 Astribot 团队日报 | {report_date}",
        "",
        "> ⚠️ LLM 摘要服务暂不可用，以下为原始数据汇总。",
        "",
    ]
    for developer_commit in developer_commits:
        if developer_commit.total_commits > 0:
            lines.append(f"### 👤 {developer_commit.developer.name}")
            for project_name, commits in developer_commit.commits_by_project().items():
                commit_msgs = "; ".join(
                    commit.message.split(":", 1)[-1].strip() for commit in commits
                )
                lines.append(f"**{project_name}** — {commit_msgs}")
            lines.append(
                f"> {developer_commit.total_commits} commits | +{developer_commit.total_additions}/-{developer_commit.total_deletions}"
            )
            lines.append("")
    return "\n".join(lines)


def use_fallback_report(
    step: ExecutionStep,
    context: EngineContext,
    engine: AgentEngine,
) -> None:
    del step
    report = context.get("report")
    report_date = str(context.get("report_date", getattr(report, "date", "")))
    developer_commits = list(context.get("developer_commits", []))
    markdown_content = build_fallback_report(developer_commits, report_date)
    context.set("markdown_content", markdown_content)

    if isinstance(report, DailyReport):
        report.markdown_content = markdown_content

    engine.mark_partial(context, "LLM 多次生成失败，已使用降级模板")


def evaluate_output_results(
    step: ExecutionStep,
    context: EngineContext,
    engine: AgentEngine,
    result: Optional[ToolResult] = None,
) -> None:
    del result
    output_results: dict[str, ToolResult] = context.get("output_results", {}) or {}
    active_results: list[bool] = []

    for channel in step.channels:
        channel_id = channel.get("id", channel.get("tool", "channel"))
        tool_name = channel.get("tool", channel_id)
        channel_result = output_results.get(channel_id)
        if channel_result is None:
            engine.append_error(context, f"输出通道未执行: {channel_id}")
            active_results.append(False)
            continue

        success_values = set(channel.get("success_values", []))
        skip_values = set(channel.get("skip_values", ["skipped"]))
        is_skipped = channel_result.data in skip_values
        is_success = channel_result.success and (
            not success_values or channel_result.data in success_values
        )

        if not is_skipped:
            active_results.append(is_success)
            if not is_success:
                error_message = channel_result.error or channel.get(
                    "failure_message", f"输出通道失败: {tool_name}"
                )
                engine.append_error(context, str(error_message))

    if not active_results:
        engine.mark_failed(context, "未配置任何可用输出通道（飞书群或飞书文档）")
    elif not any(active_results):
        engine.mark_failed(context, "已启用的输出通道全部失败")
    elif any(item is False for item in active_results):
        engine.mark_partial(context)
