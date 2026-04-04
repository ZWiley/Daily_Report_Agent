"""
调度器模块测试
================
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent.config import AgentConfig
from agent.scheduler import DailyReportScheduler, ExecutionHistory, ExecutionRecord


def test_parse_cron_expression_fallback_to_default() -> None:
    """非法 cron 表达式应回退到默认配置。"""
    scheduler = DailyReportScheduler(AgentConfig(), use_mock=True)

    parts = scheduler._parse_cron_expression("invalid cron")

    assert parts == ("0", "9", "*", "*", "1-5")


def test_parse_cron_expression_supports_complex_fields() -> None:
    """合法复杂 cron 表达式应被保留。"""
    scheduler = DailyReportScheduler(AgentConfig(), use_mock=True)

    parts = scheduler._parse_cron_expression("*/15 9-18 * * 1-5")

    assert parts == ("*/15", "9-18", "*", "*", "1-5")


def test_execution_history_update_run_by_run_id(tmp_path: Path) -> None:
    """更新执行记录应按 run_id 精确命中而不是覆盖最后一条。"""
    history = ExecutionHistory(tmp_path / "execution_history.json")

    history.add(
        ExecutionRecord(
            run_id="run_1",
            scheduled_time="2026-04-04 09:00:00",
            start_time="2026-04-04T09:00:00",
        )
    )
    history.add(
        ExecutionRecord(
            run_id="run_2",
            scheduled_time="2026-04-04 10:00:00",
            start_time="2026-04-04T10:00:00",
        )
    )

    history.update_run("run_1", status="success", commits=12)

    records = history.get_recent(2)
    assert records[0]["run_id"] == "run_1"
    assert records[0]["status"] == "success"
    assert records[0]["commits"] == 12
    assert records[1]["run_id"] == "run_2"
    assert records[1]["status"] == "running"


def test_should_startup_catchup_for_fixed_time_schedule() -> None:
    """固定时分的 cron 在错过执行时应触发补执行。"""
    scheduler = DailyReportScheduler(AgentConfig(), use_mock=True)
    scheduler._check_missed_today = lambda: True  # type: ignore[method-assign]

    should_catchup = scheduler._should_startup_catchup(
        minute="0",
        hour="9",
        day="*",
        month="*",
        day_of_week="1-5",
        now=datetime(2026, 4, 6, 10, 0),  # 周一 10:00
    )

    assert should_catchup is True


def test_should_startup_catchup_skip_for_complex_hour() -> None:
    """复杂时分表达式（如 */2）应跳过补执行判定。"""
    scheduler = DailyReportScheduler(AgentConfig(), use_mock=True)
    scheduler._check_missed_today = lambda: True  # type: ignore[method-assign]

    should_catchup = scheduler._should_startup_catchup(
        minute="0",
        hour="*/2",
        day="*",
        month="*",
        day_of_week="*",
        now=datetime(2026, 4, 6, 10, 0),
    )

    assert should_catchup is False


def test_is_today_in_schedule_supports_sunday_numeric_values() -> None:
    """day_of_week 为 0/7 时应正确识别为周日。"""
    assert DailyReportScheduler._is_today_in_schedule("0", 6) is True
    assert DailyReportScheduler._is_today_in_schedule("7", 6) is True
