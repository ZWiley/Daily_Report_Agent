"""
Engine 内核测试
==============
验证 context 解析、可插拔 executor / hook，以及自定义 fallback 能力。
"""

from __future__ import annotations

import pytest

from agent.engine import AgentEngine, EngineContext, ExecutionStep
from agent.tools.base import ToolResult, ToolRegistry


class DummyTool:
    def __init__(self, result: ToolResult | list[ToolResult]) -> None:
        if isinstance(result, list):
            self._results = result
        else:
            self._results = [result]

    async def safe_execute(self, **kwargs):
        del kwargs
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


class DummyRegistry:
    def __init__(self, mapping: dict[str, ToolResult | list[ToolResult]]) -> None:
        self._mapping = {name: DummyTool(result) for name, result in mapping.items()}

    def get(self, name: str):
        return self._mapping.get(name)

    def list_names(self) -> list[str]:
        return list(self._mapping.keys())


def test_engine_context_supports_nested_get_set_and_resolve() -> None:
    context = EngineContext(
        {
            "report": {"title": "日报"},
            "guardrails": {"max_retries": 2},
            "developers": [{"name": "张伟"}],
        }
    )

    context.set("payload.summary.markdown", "hello")

    assert context.get("payload.summary.markdown") == "hello"
    assert context.get("report.title") == "日报"
    assert context.get("developers.0.name") == "张伟"
    assert context.resolve({"retry": "$guardrails.max_retries"}) == {"retry": 2}


@pytest.mark.asyncio
async def test_engine_supports_custom_executor_and_hook() -> None:
    context = EngineContext({"payload": {"value": 20}})
    engine = AgentEngine(
        execution_plan=[
            ExecutionStep.from_dict(
                {"id": "custom_step", "kind": "custom", "after_step": "increment_value"}
            )
        ],
        tools=ToolRegistry(),
    )

    async def custom_executor(step, runtime_context, runtime_engine):
        runtime_context.set("payload.value", runtime_context.get("payload.value", 0) + 1)
        if step.after_step:
            runtime_engine.run_hook(step.after_step, step, runtime_context)
        return True

    def increment_hook(step, runtime_context, runtime_engine, result=None):
        del step, runtime_engine, result
        runtime_context.set("payload.value", runtime_context.get("payload.value", 0) + 1)

    engine.register_executor("custom", custom_executor)
    engine.register_hook("increment_value", increment_hook)

    await engine.run(context)

    assert context.get("payload.value") == 22
    assert len(engine.trace.steps) == 0


@pytest.mark.asyncio
async def test_engine_supports_custom_fallback_action_with_builtin_summary_loop() -> None:
    engine = AgentEngine(
        execution_plan=[
            ExecutionStep.from_dict(
                {
                    "id": "summarize",
                    "kind": "summary_loop",
                    "generate_tool": "generate_summary",
                    "quality_tool": "check_report_quality",
                    "generate_params": {"input": "$source_text"},
                    "fallback_action": "custom_fallback",
                    "max_retries": 1,
                }
            )
        ],
        tools=DummyRegistry(
            {
                "generate_summary": [
                    ToolResult(success=False, error="timeout #1"),
                    ToolResult(success=False, error="timeout #2"),
                ]
            }
        ),
        allowed_tools={"generate_summary", "check_report_quality"},
        guardrails={"max_steps": 5, "max_retries": 1, "required_quality_score": 6},
    )
    context = EngineContext({"source_text": "hello"})

    def custom_fallback(step, runtime_context, runtime_engine):
        del step
        runtime_context.set("summary", "fallback summary")
        runtime_engine.mark_partial(runtime_context, "used custom fallback")

    engine.register_fallback("custom_fallback", custom_fallback)

    await engine.run(context)

    assert context.get("summary") == "fallback summary"
    assert context.get("engine_status") == "partial"
    assert "used custom fallback" in context.get("runtime_errors", [])
