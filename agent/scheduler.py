"""
生产级调度引擎
==============
将日报生成从"手动执行"升级为"每天自动执行"的守护进程。

核心能力：
  - Cron 定时调度（默认每天早上 9:00）
  - 执行历史持久化（JSON 日志）
  - 失败自动重试（最多 3 次，间隔 5 分钟）
  - 优雅信号处理（SIGTERM/SIGINT）
  - 容器级健康检查（通过 PID 文件判断）
  - 启动时立即检查是否错过今日执行
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.config import AgentConfig
from agent.agent_core import DailyReportAgent
from agent.models import ReportStatus

logger = logging.getLogger(__name__)
console = Console()

# 执行历史文件
HISTORY_DIR = Path("data")
HISTORY_FILE = HISTORY_DIR / "execution_history.json"
DEFAULT_CRON = "0 9 * * 1-5"


class ExecutionRecord:
    """单次执行记录"""

    def __init__(
        self,
        run_id: str,
        scheduled_time: str,
        start_time: str,
        end_time: str = "",
        status: str = "running",
        developers: int = 0,
        commits: int = 0,
        errors: list[str] | None = None,
        retry_count: int = 0,
    ):
        self.run_id = run_id
        self.scheduled_time = scheduled_time
        self.start_time = start_time
        self.end_time = end_time
        self.status = status
        self.developers = developers
        self.commits = commits
        self.errors = errors or []
        self.retry_count = retry_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scheduled_time": self.scheduled_time,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "developers": self.developers,
            "commits": self.commits,
            "errors": self.errors,
            "retry_count": self.retry_count,
        }


class ExecutionHistory:
    """执行历史管理器 — 持久化到本地 JSON"""

    def __init__(self, history_file: Path = HISTORY_FILE):
        self.history_file = history_file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if self.history_file.exists():
            try:
                return json.loads(self.history_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self) -> None:
        # 只保留最近 90 天的记录
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        self._records = [r for r in self._records if r.get("start_time", "") > cutoff]

        payload = json.dumps(self._records, ensure_ascii=False, indent=2)
        tmp_file = self.history_file.with_suffix(".json.tmp")
        tmp_file.write_text(payload, encoding="utf-8")
        tmp_file.replace(self.history_file)

    def add(self, record: ExecutionRecord) -> None:
        self._records.append(record.to_dict())
        self._save()

    def update_run(self, run_id: str, **kwargs: Any) -> None:
        """按 run_id 更新执行记录，避免并发/重试时误更新最后一条。"""
        for i in range(len(self._records) - 1, -1, -1):
            if self._records[i].get("run_id") == run_id:
                self._records[i].update(kwargs)
                self._save()
                return

    def update_latest(self, **kwargs: Any) -> None:
        """兼容旧接口：更新最近一条记录。"""
        if self._records:
            self._records[-1].update(kwargs)
            self._save()

    def get_today_record(self) -> dict[str, Any] | None:
        """获取今日的执行记录"""
        today = datetime.now().strftime("%Y-%m-%d")
        for record in reversed(self._records):
            if record.get("scheduled_time", "").startswith(today):
                return record
        return None

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        return self._records[-n:]


class DailyReportScheduler:
    """
    生产级日报调度器
    ================
    管理定时任务的完整生命周期。
    """

    MAX_RETRIES = 3
    RETRY_INTERVAL_MINUTES = 5

    def __init__(self, config: AgentConfig, use_mock: bool = False) -> None:
        self.config = config
        self.use_mock = use_mock or config.gitlab.use_mock
        self.history = ExecutionHistory()
        self._scheduler: AsyncIOScheduler | None = None
        self._shutdown_event = asyncio.Event()
        self._current_retry = 0

    async def _execute_daily_report(self) -> None:
        """执行一次日报生成（带重试逻辑）"""
        now = datetime.now()
        run_id = now.strftime("%Y%m%d_%H%M%S")

        record = ExecutionRecord(
            run_id=run_id,
            scheduled_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            start_time=now.isoformat(),
            retry_count=self._current_retry,
        )
        self.history.add(record)

        logger.info("=" * 60)
        logger.info(f"⏰ 定时任务触发 | {now.strftime('%Y-%m-%d %H:%M:%S')}")
        if self._current_retry > 0:
            logger.info(f"🔄 第 {self._current_retry} 次重试")
        logger.info("=" * 60)

        try:
            pipeline = DailyReportAgent(self.config, use_mock=self.use_mock)
            report = await pipeline.run()

            end_time = datetime.now()
            self.history.update_run(
                run_id,
                end_time=end_time.isoformat(),
                status=report.status.value,
                developers=report.total_developers,
                commits=report.total_commits,
                errors=report.errors,
            )

            # 如果失败且还有重试次数，安排重试
            if report.status == ReportStatus.FAILED and self._current_retry < self.MAX_RETRIES:
                self._current_retry += 1
                if not self._schedule_retry(run_id):
                    self._current_retry = 0
            else:
                self._current_retry = 0  # 成功或重试耗尽，重置计数器

            if report.status == ReportStatus.SUCCESS:
                logger.info("✅ 日报定时任务执行成功")
            elif report.status == ReportStatus.PARTIAL:
                logger.warning("⚠️  日报定时任务部分成功")

        except Exception as e:
            logger.error(f"❌ 日报定时任务异常: {e}", exc_info=True)
            self.history.update_run(
                run_id,
                end_time=datetime.now().isoformat(),
                status="error",
                errors=[str(e)],
            )

            if self._current_retry < self.MAX_RETRIES:
                self._current_retry += 1
                if not self._schedule_retry(run_id):
                    self._current_retry = 0
            else:
                self._current_retry = 0

    def _schedule_retry(self, run_id: str) -> bool:
        """安排失败重试任务。"""
        if not self._scheduler:
            logger.error("❌ 调度器尚未初始化，无法安排重试任务")
            return False

        retry_time = datetime.now() + timedelta(minutes=self.RETRY_INTERVAL_MINUTES)
        logger.warning(
            f"🔄 将在 {self.RETRY_INTERVAL_MINUTES} 分钟后重试 "
            f"({self._current_retry}/{self.MAX_RETRIES})"
        )
        self._scheduler.add_job(
            self._execute_daily_report,
            "date",
            run_date=retry_time,
            id=f"retry_{run_id}_{self._current_retry}",
            replace_existing=True,
        )
        return True

    def _on_job_event(self, event: Any) -> None:
        """APScheduler 事件监听"""
        if hasattr(event, "exception") and event.exception:
            logger.error(f"🚨 调度任务异常: {event.exception}")
        if hasattr(event, "job_id") and "daily_report" in str(event.job_id):
            if event.code == EVENT_JOB_MISSED:
                logger.warning("⏭️  检测到错过的定时任务，将立即补执行")

    def _check_missed_today(self) -> bool:
        """检查今天是否已执行过 — 启动时用于判断是否需要补执行"""
        record = self.history.get_today_record()
        if record and record.get("status") in ("success", "partial"):
            return False  # 今天已成功执行
        return True  # 今天还没执行或执行失败

    async def start(self, run_immediately: bool = False) -> None:
        """启动调度器。"""
        minute, hour, day, month, day_of_week = self._parse_cron_expression(
            self.config.schedule_cron
        )

        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,       # 错过多次只补执行一次
                "max_instances": 1,     # 同一时间只允许一个实例
                "misfire_grace_time": 3600,  # 错过 1 小时内仍会补执行
            }
        )

        # 注册事件监听
        self._scheduler.add_listener(
            self._on_job_event,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )

        # 注册主定时任务
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
        self._scheduler.add_job(
            self._execute_daily_report,
            trigger,
            id="daily_report_main",
            name="Astribot 每日日报生成",
            replace_existing=True,
        )

        self._scheduler.start()

        # 计算下次执行时间
        next_run = trigger.get_next_fire_time(None, datetime.now())
        next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "未知"

        # 展示状态面板
        self._print_status_panel(minute, hour, day_of_week, next_run_str)

        # 注册信号处理
        self._setup_signal_handlers()

        if run_immediately:
            logger.info("🚀 --run-now: 调度器启动后立即执行一次日报...")
            await self._execute_daily_report()
            logger.info("✅ 立即执行完成，进入定时调度模式")

        # 检查是否需要补执行今日任务
        if self._should_startup_catchup(minute, hour, day, month, day_of_week):
            logger.info("📌 检测到今日尚未执行日报，将在 30 秒后补执行...")
            catchup_time = datetime.now() + timedelta(seconds=30)
            self._scheduler.add_job(
                self._execute_daily_report,
                "date",
                run_date=catchup_time,
                id="catchup_today",
                name="补执行今日日报",
                replace_existing=True,
            )

        # 保持运行
        try:
            await self._shutdown_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await self.stop()

    @staticmethod
    def _default_cron_parts() -> tuple[str, str, str, str, str]:
        minute, hour, day, month, day_of_week = DEFAULT_CRON.split()
        return minute, hour, day, month, day_of_week

    def _parse_cron_expression(self, cron_expr: str) -> tuple[str, str, str, str, str]:
        parts = cron_expr.split()
        if len(parts) != 5:
            logger.warning(
                f"⚠️  无效 Cron 表达式: {cron_expr!r}，将回退到默认值 {DEFAULT_CRON!r}"
            )
            return self._default_cron_parts()

        minute, hour, day, month, day_of_week = parts
        try:
            CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
            )
        except ValueError as e:
            logger.warning(
                f"⚠️  Cron 表达式解析失败: {cron_expr!r} ({e})，将回退到默认值 {DEFAULT_CRON!r}"
            )
            return self._default_cron_parts()

        return minute, hour, day, month, day_of_week

    def _should_startup_catchup(
        self,
        minute: str,
        hour: str,
        day: str,
        month: str,
        day_of_week: str,
        now: datetime | None = None,
    ) -> bool:
        """判断启动时是否应补执行当日报告。"""
        if day != "*" or month != "*":
            logger.info("ℹ️  检测到非每日 Cron（day/month 含约束），跳过启动补执行判定")
            return False

        if not (minute.isdigit() and hour.isdigit()):
            logger.info("ℹ️  检测到复杂时间表达式（非固定时分），跳过启动补执行判定")
            return False

        current = now or datetime.now()
        scheduled_total_minutes = int(hour) * 60 + int(minute)
        current_total_minutes = current.hour * 60 + current.minute

        if current_total_minutes < scheduled_total_minutes:
            return False

        if not self._check_missed_today():
            return False

        weekday = current.weekday()  # 0=周一
        if day_of_week == "*":
            return True
        return self._is_today_in_schedule(day_of_week, weekday)

    @staticmethod
    def _is_today_in_schedule(day_of_week: str, today: int) -> bool:
        """检查今天是否在 cron day_of_week 范围内"""
        # today: Python weekday, 0=周一 ... 6=周日
        name_map = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }

        def _normalize(token: str) -> int:
            token = token.strip().lower()
            if token in name_map:
                return name_map[token]
            if not token.isdigit():
                return -1

            num = int(token)
            # 标准 cron: 0/7=周日, 1=周一 ... 6=周六
            if num in (0, 7):
                return 6
            if 1 <= num <= 6:
                return num - 1
            return -1

        parts = day_of_week.replace(" ", "").split(",")
        for part in parts:
            if "-" in part:
                start, end = part.split("-", 1)
                start_num = _normalize(start)
                end_num = _normalize(end)
                if start_num >= 0 and end_num >= 0 and start_num <= today <= end_num:
                    return True
                continue

            if _normalize(part) == today:
                return True

        return False

    async def stop(self) -> None:
        """优雅停止调度器"""
        logger.info("\n🛑 正在优雅停止调度器...")
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=True)
        logger.info("👋 调度器已停止")

    def _setup_signal_handlers(self) -> None:
        """注册系统信号处理（SIGTERM/SIGINT）"""
        loop = asyncio.get_running_loop()

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info(f"\n📡 收到信号 {sig.name}，准备优雅退出...")
            self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal, sig)
            except NotImplementedError:
                # Windows 不支持 add_signal_handler
                pass

    def _print_status_panel(
        self, minute: str, hour: str, day_of_week: str, next_run: str
    ) -> None:
        """打印调度器状态面板"""
        # 将 cron day_of_week 转为可读文字
        dow_map = {
            "1-5": "周一至周五",
            "mon-fri": "周一至周五",
            "*": "每天",
            "1,2,3,4,5": "周一至周五",
        }
        dow_text = dow_map.get(day_of_week, day_of_week)

        # 最近执行历史
        recent = self.history.get_recent(5)

        time_text = (
            f"每天 {hour}:{minute.zfill(2)}"
            if hour.isdigit() and minute.isdigit()
            else f"Cron({minute} {hour})"
        )

        info_lines = [
            f"[bold green]● 调度器运行中[/bold green]",
            "",
            f"[bold]执行计划[/bold]",
            f"  ⏰ 时间: {time_text}",
            f"  📅 范围: {dow_text}",
            f"  🔜 下次执行: {next_run}",
            "",
            f"[bold]容错机制[/bold]",
            f"  🔄 失败重试: 最多 {self.MAX_RETRIES} 次，间隔 {self.RETRY_INTERVAL_MINUTES} 分钟",
            f"  ⏭️  错过补执行: 启动时自动检测并补执行",
            f"  📝 执行历史: {HISTORY_FILE}",
            "",
            f"[bold]运行模式[/bold]",
            f"  🔧 模式: {'🎭 Mock (开发)' if self.use_mock else '🚀 生产'}",
            f"  📋 PID: {os.getpid()}",
            "",
            f"[dim]按 Ctrl+C 优雅退出 | kill {os.getpid()} 也可安全停止[/dim]",
        ]

        console.print()
        console.print(Panel(
            "\n".join(info_lines),
            title="🤖 Astribot Daily Report Scheduler",
            border_style="green",
            padding=(1, 2),
        ))

        # 最近执行历史表格
        if recent:
            table = Table(title="📊 最近执行记录", show_lines=True)
            table.add_column("时间", style="cyan", width=20)
            table.add_column("状态", width=10)
            table.add_column("开发者", justify="right", width=8)
            table.add_column("Commits", justify="right", width=8)
            table.add_column("重试", justify="right", width=6)

            status_style = {
                "success": "[green]✅ 成功[/green]",
                "partial": "[yellow]⚠️  部分[/yellow]",
                "failed": "[red]❌ 失败[/red]",
                "error": "[red]💥 异常[/red]",
                "running": "[blue]🔄 运行中[/blue]",
            }

            for r in recent:
                table.add_row(
                    r.get("scheduled_time", "-"),
                    status_style.get(r.get("status", ""), r.get("status", "")),
                    str(r.get("developers", 0)),
                    str(r.get("commits", 0)),
                    str(r.get("retry_count", 0)),
                )

            console.print(table)
        console.print()
