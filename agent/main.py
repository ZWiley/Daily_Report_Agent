"""
Astribot Daily Report Agent - 主入口
====================================
支持五种运行模式：
  1. `python -m agent.main run`      - 立即执行一次日报生成
  2. `python -m agent.main schedule`  - 🔥 启动守护进程，每天9点自动执行
  3. `python -m agent.main demo`      - 使用 Mock 数据演示
  4. `python -m agent.main history`   - 查看执行历史记录
  5. `python -m agent.main status`    - 查看调度器运行状态

Usage:
  python -m agent.main run [--mock]
  python -m agent.main schedule [--mock] [--run-now]
  python -m agent.main demo
  python -m agent.main history [--last N]
  python -m agent.main status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import __version__
from agent.config import get_config

console = Console()

# PID 文件用于检测调度器是否在运行
PID_FILE = Path("data/scheduler.pid")


def _process_exists(pid: int) -> bool:
    """检查进程是否存在。"""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _acquire_scheduler_pid_lock() -> None:
    """获取调度器单实例锁（PID 文件）。"""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    current_pid = os.getpid()

    if PID_FILE.exists():
        try:
            existing_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if existing_pid != current_pid and _process_exists(existing_pid):
                raise RuntimeError(f"调度器已在运行 (PID: {existing_pid})")
            # PID 文件存在但进程已不在，视为脏数据并清理
            PID_FILE.unlink(missing_ok=True)
        except ValueError:
            PID_FILE.unlink(missing_ok=True)

    PID_FILE.write_text(str(current_pid), encoding="utf-8")


def _release_scheduler_pid_lock() -> None:
    """释放调度器单实例锁，仅删除当前进程持有的 PID 文件。"""
    if not PID_FILE.exists():
        return

    try:
        owner_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return

    if owner_pid == os.getpid():
        PID_FILE.unlink(missing_ok=True)


def setup_logging(level: str = "INFO", log_to_file: bool = False) -> None:
    """
    配置日志系统。
    - 控制台: Rich 美化输出
    - 文件 (可选): 持久化到 logs/ 目录
    """
    handlers: list[logging.Handler] = [
        RichHandler(
            console=console,
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        )
    ]

    if log_to_file:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"agent_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )


async def run_once(use_mock: bool = False) -> None:
    """执行一次日报生成流程（使用 Agent Core）"""
    config = get_config()

    from agent.agent_core import DailyReportAgent
    agent = DailyReportAgent(config, use_mock=use_mock)
    report = await agent.run()

    # 输出结果
    if report.markdown_content:
        console.print()
        console.print(
            Panel(
                report.markdown_content,
                title="📋 生成的日报",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # 状态汇总
    status_color = {
        "success": "green",
        "partial": "yellow",
        "failed": "red",
    }
    color = status_color.get(report.status.value, "white")
    console.print(f"\n[{color}]状态: {report.status.value}[/{color}]")
    console.print(f"开发者: {report.total_developers} 人")
    console.print(f"Commits: {report.total_commits} 条")

    if report.errors:
        console.print(f"[yellow]警告: {len(report.errors)} 个错误[/yellow]")


def run_schedule(use_mock: bool = False, run_now: bool = False) -> None:
    """
    启动生产级定时调度器。
    默认每天上午 9:00 (周一至周五) 自动执行日报生成。
    """
    from agent.scheduler import DailyReportScheduler

    config = get_config()
    scheduler = DailyReportScheduler(config, use_mock=use_mock)

    try:
        _acquire_scheduler_pid_lock()
    except RuntimeError as e:
        console.print(f"[red]❌ {e}[/red]")
        console.print("[dim]请先停止已有实例，或删除无效的 data/scheduler.pid 后重试[/dim]")
        sys.exit(1)

    try:
        asyncio.run(scheduler.start(run_immediately=run_now))
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]👋 调度器已停止[/yellow]")
    finally:
        _release_scheduler_pid_lock()


def show_history(last_n: int = 10) -> None:
    """显示执行历史"""
    from agent.scheduler import ExecutionHistory

    history = ExecutionHistory()
    records = history.get_recent(last_n)

    if not records:
        console.print("[yellow]📭 暂无执行记录[/yellow]")
        console.print("[dim]运行 'python -m agent.main schedule' 启动定时调度[/dim]")
        return

    table = Table(
        title=f"📊 最近 {len(records)} 次执行记录",
        show_lines=True,
        title_style="bold",
    )
    table.add_column("执行时间", style="cyan", width=20)
    table.add_column("状态", width=12)
    table.add_column("开发者", justify="right", width=8)
    table.add_column("Commits", justify="right", width=8)
    table.add_column("重试", justify="right", width=6)
    table.add_column("耗时", justify="right", width=8)
    table.add_column("错误", width=30)

    status_style = {
        "success": "[green]✅ 成功[/green]",
        "partial": "[yellow]⚠️  部分成功[/yellow]",
        "failed": "[red]❌ 失败[/red]",
        "error": "[red]💥 异常[/red]",
        "running": "[blue]🔄 运行中[/blue]",
    }

    for r in records:
        # 计算耗时
        duration = "-"
        if r.get("start_time") and r.get("end_time"):
            try:
                start = datetime.fromisoformat(r["start_time"])
                end = datetime.fromisoformat(r["end_time"])
                secs = (end - start).total_seconds()
                duration = f"{secs:.1f}s"
            except (ValueError, TypeError):
                pass

        errors = r.get("errors", [])
        error_text = errors[0][:28] + "…" if errors else "-"

        table.add_row(
            r.get("scheduled_time", "-"),
            status_style.get(r.get("status", ""), r.get("status", "")),
            str(r.get("developers", 0)),
            str(r.get("commits", 0)),
            str(r.get("retry_count", 0)),
            duration,
            error_text,
        )

    console.print()
    console.print(table)
    console.print()


def show_status() -> None:
    """显示调度器当前状态"""
    is_running = False
    pid = None

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            is_running = _process_exists(pid)
            if not is_running:
                # 清理过期的 PID 文件
                PID_FILE.unlink(missing_ok=True)
        except ValueError:
            is_running = False
            PID_FILE.unlink(missing_ok=True)

    config = get_config()

    lines = []
    if is_running:
        lines.append(f"[bold green]● 调度器正在运行[/bold green]  (PID: {pid})")
    else:
        lines.append(f"[bold red]○ 调度器未运行[/bold red]")
        lines.append("")
        lines.append("[dim]启动命令: python -m agent.main schedule[/dim]")

    lines.append("")
    lines.append(f"[bold]配置信息[/bold]")
    lines.append(f"  ⏰ 调度计划: {config.schedule_cron}")
    lines.append(f"  🔧 默认模式: {'Mock' if config.gitlab.use_mock else '生产'}")
    lines.append(f"  ⏱️  回溯小时: {config.report_hours_lookback}h")

    # 查看今日执行情况
    from agent.scheduler import ExecutionHistory
    history = ExecutionHistory()
    today_record = history.get_today_record()

    lines.append("")
    if today_record:
        status = today_record.get("status", "unknown")
        status_text = {
            "success": "✅ 已成功执行",
            "partial": "⚠️  部分成功",
            "failed": "❌ 执行失败",
            "error": "💥 执行异常",
        }.get(status, status)
        lines.append(f"[bold]今日状态[/bold]: {status_text}")
        lines.append(f"  执行时间: {today_record.get('scheduled_time', '-')}")
        lines.append(f"  开发者: {today_record.get('developers', 0)} 人")
        lines.append(f"  Commits: {today_record.get('commits', 0)} 条")
    else:
        lines.append(f"[bold]今日状态[/bold]: 📭 尚未执行")

    console.print()
    console.print(Panel(
        "\n".join(lines),
        title="🤖 Astribot Daily Agent Status",
        border_style="blue",
        padding=(1, 2),
    ))
    console.print()


def print_banner() -> None:
    """打印启动 Banner"""
    banner = Text()
    banner.append("╔══════════════════════════════════════════╗\n", style="bold cyan")
    banner.append("║                                          ║\n", style="bold cyan")
    banner.append(f"║   🤖 Astribot Daily Report Agent v{__version__:<5} ║\n", style="bold cyan")
    banner.append("║                                          ║\n", style="bold cyan")
    banner.append("║   自动采集 · 智能摘要 · 飞书推送         ║\n", style="bold cyan")
    banner.append("║                                          ║\n", style="bold cyan")
    banner.append("╚══════════════════════════════════════════╝", style="bold cyan")
    console.print(banner)
    console.print()


def cli_entry() -> None:
    """CLI 入口点"""
    parser = argparse.ArgumentParser(
        description="Astribot Daily Report Automation Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m agent.main run              # 立即执行一次（生产模式）
  python -m agent.main run --mock       # 立即执行一次（Mock 数据）
  python -m agent.main demo             # 快速演示

  python -m agent.main schedule         # 🔥 启动定时调度（每天9点自动执行）
  python -m agent.main schedule --mock  # 定时调度（Mock 模式）
  python -m agent.main schedule --run-now  # 启动并立即执行一次

  python -m agent.main history          # 查看执行历史
  python -m agent.main history --last 20  # 查看最近20条
  python -m agent.main status           # 查看调度器状态
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="运行模式")

    # run 子命令
    run_parser = subparsers.add_parser("run", help="立即执行一次日报生成")
    run_parser.add_argument("--mock", action="store_true", help="使用 Mock 数据")

    # schedule 子命令
    sched_parser = subparsers.add_parser(
        "schedule",
        help="🔥 启动定时调度守护进程（每天上午9点自动执行）",
    )
    sched_parser.add_argument("--mock", action="store_true", help="使用 Mock 数据")
    sched_parser.add_argument(
        "--run-now",
        action="store_true",
        help="启动后立即执行一次，然后进入定时调度",
    )

    # demo 子命令
    subparsers.add_parser("demo", help="使用 Mock 数据快速演示")

    # history 子命令
    history_parser = subparsers.add_parser("history", help="查看执行历史记录")
    history_parser.add_argument(
        "--last", type=int, default=10, help="显示最近 N 条记录 (默认 10)"
    )

    # status 子命令
    subparsers.add_parser("status", help="查看调度器运行状态")

    args = parser.parse_args()

    if args.command == "status":
        # status 不需要 banner
        show_status()
        return

    if args.command == "history":
        show_history(last_n=args.last)
        return

    # 其他命令需要初始化日志和 banner
    config = get_config()
    log_to_file = args.command == "schedule"
    setup_logging(level=config.log_level, log_to_file=log_to_file)
    print_banner()

    if args.command == "run":
        asyncio.run(run_once(use_mock=args.mock))
    elif args.command == "schedule":
        run_schedule(use_mock=args.mock, run_now=args.run_now)
    elif args.command == "demo":
        asyncio.run(run_once(use_mock=True))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli_entry()
