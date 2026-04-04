"""
Pipeline 编排引擎
=================
串联 飞书读取 → GitLab采集 → LLM摘要 → 飞书推送+文档写入 的完整流程。

设计原则：
  - 每个阶段独立可测试
  - 失败不中断整体流程（优雅降级）
  - 完整的日志追踪
  - 支持 Mock 模式无缝切换
  - 飞书群推送与文档写入并行执行
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime

from agent.config import AgentConfig
from agent.feishu.bitable import BitableReader, MockBitableReader
from agent.feishu.docwriter import FeishuDocWriter, MockFeishuDocWriter
from agent.feishu.messenger import FeishuMessenger, MockFeishuMessenger
from agent.gitlab.collector import GitLabCollector, MockGitLabCollector
from agent.llm.summarizer import LLMSummarizer, MockLLMSummarizer
from agent.models import DailyReport, ReportStatus

logger = logging.getLogger(__name__)


class DailyReportPipeline:
    """
    日报生成 Pipeline
    =================

    执行流程：
        Step 1: 从飞书多维表格读取开发者列表
        Step 2: 从 GitLab 采集每位开发者的 commit 记录
        Step 3: 调用 LLM 生成智能摘要
        Step 4: 推送到飞书群 (Webhook 卡片消息)       ← 并行
        Step 5: 写入飞书文档 (按日期归档)              ← 并行

    Error Handling:
        - 每个阶段独立 try-catch，记录错误但不中断
        - 最终报告包含所有错误信息
        - 支持部分成功（partial）状态
    """

    def __init__(self, config: AgentConfig, use_mock: bool = False) -> None:
        self.config = config
        self.use_mock = use_mock or config.gitlab.use_mock

        # 根据模式初始化各模块
        if self.use_mock:
            logger.info("🎭 运行在 Mock 模式")
            self.bitable_reader = MockBitableReader()
            self.gitlab_collector = MockGitLabCollector(
                hours_lookback=config.report_hours_lookback,
            )
            self.llm_summarizer = MockLLMSummarizer()
            self.messenger = MockFeishuMessenger()
            self.doc_writer = MockFeishuDocWriter()
        else:
            logger.info("🚀 运行在生产模式")
            self.bitable_reader = BitableReader(config.feishu)
            self.gitlab_collector = GitLabCollector(
                config=config.gitlab,
                hours_lookback=config.report_hours_lookback,
            )
            self.llm_summarizer = LLMSummarizer(config.llm)
            self.messenger = FeishuMessenger(config.feishu)
            self.doc_writer = FeishuDocWriter(config.feishu)

    async def run(self) -> DailyReport:
        """
        执行完整的日报生成 Pipeline。

        Returns:
            生成的日报对象
        """
        start_time = time.monotonic()
        report_date = datetime.now().strftime("%Y-%m-%d")

        report = DailyReport(date=report_date)
        webhook_result: bool | None = None
        doc_result: bool | None = None

        webhook_enabled = self.use_mock or bool(self.config.feishu.webhook_url)
        doc_enabled = self.use_mock or bool(
            self.config.feishu.doc_id or self.config.feishu.doc_folder_token
        )

        try:
            logger.info("=" * 60)
            logger.info("🤖 Astribot Daily Report Pipeline 启动")
            logger.info(f"📅 日期: {report_date}")
            logger.info(f"🔧 模式: {'Mock' if self.use_mock else '生产'}")
            logger.info("=" * 60)

            # ========================================
            # Step 1: 读取飞书多维表格 → 开发者列表
            # ========================================
            logger.info("\n📋 Step 1/5: 读取飞书多维表格...")
            try:
                developers = await self.bitable_reader.fetch_developers()
                logger.info(f"   ✅ 获取到 {len(developers)} 位开发者")
            except Exception as e:
                error_msg = f"读取飞书多维表格失败: {e}"
                logger.error(f"   ❌ {error_msg}")
                report.errors.append(error_msg)
                report.status = ReportStatus.FAILED
                return report

            # ========================================
            # Step 2: GitLab Commit 采集
            # ========================================
            logger.info("\n🔍 Step 2/5: 采集 GitLab Commit 记录...")
            try:
                developer_commits = await self.gitlab_collector.collect_all(developers)
                report.developer_summaries = developer_commits
                report.compute_totals()
                logger.info(
                    f"   ✅ 采集完成: {report.total_developers} 位开发者, "
                    f"{report.total_commits} 条 commit"
                )
            except Exception as e:
                error_msg = f"GitLab commit 采集失败: {e}"
                logger.error(f"   ❌ {error_msg}")
                report.errors.append(error_msg)
                report.status = ReportStatus.FAILED
                return report

            # ========================================
            # Step 3: LLM 智能摘要
            # ========================================
            logger.info("\n🧠 Step 3/5: 生成 LLM 智能摘要...")
            try:
                markdown = await self.llm_summarizer.generate_report(
                    developer_commits, report_date
                )
                report.markdown_content = markdown
                logger.info(f"   ✅ 日报生成完成 ({len(markdown)} 字符)")
            except Exception as e:
                error_msg = f"LLM 摘要生成失败: {e}"
                logger.error(f"   ❌ {error_msg}")
                report.errors.append(error_msg)
                # 降级：使用简单模板
                report.markdown_content = self._fallback_report(developer_commits, report_date)
                report.status = ReportStatus.PARTIAL
                logger.info("   ⚠️  已使用降级模板生成日报")

            # ========================================
            # Step 4 & 5: 飞书群推送 + 文档写入（并行）
            # ========================================
            logger.info("\n📨 Step 4-5/5: 推送飞书群 + 写入飞书文档（并行执行）...")

            webhook_result, doc_result = await asyncio.gather(
                self._send_to_group(report, enabled=webhook_enabled),
                self._write_to_document(report, enabled=doc_enabled),
                return_exceptions=False,
            )

            # 汇总两个渠道的结果（None=未启用/已跳过）
            active_results = [r for r in (webhook_result, doc_result) if r is not None]
            if not active_results:
                report.status = ReportStatus.FAILED
                report.errors.append("未配置任何可用输出通道（飞书群或飞书文档）")
            elif not any(active_results):
                report.status = ReportStatus.FAILED
                report.errors.append("已启用的输出通道全部失败")
            elif any(r is False for r in active_results) and report.status == ReportStatus.SUCCESS:
                report.status = ReportStatus.PARTIAL

            # ========================================
            # 完成
            # ========================================
            elapsed = time.monotonic() - start_time
            logger.info("\n" + "=" * 60)
            logger.info("🏁 Pipeline 执行完成")
            logger.info(f"   状态: {report.status.value}")
            logger.info(f"   耗时: {elapsed:.2f}s")
            logger.info(
                "   输出: "
                f"飞书群={self._channel_state_icon(webhook_result)} | "
                f"飞书文档={self._channel_state_icon(doc_result)}"
            )
            if report.errors:
                logger.warning(f"   错误: {len(report.errors)} 个")
                for err in report.errors:
                    logger.warning(f"     - {err}")
            logger.info("=" * 60)

            return report
        finally:
            await self._close_resources()

    @staticmethod
    def _channel_state_icon(result: bool | None) -> str:
        if result is True:
            return "✅"
        if result is False:
            return "❌"
        return "⏭️"

    async def _close_resources(self) -> None:
        """统一释放外部资源（HTTP client / SDK client 等）。"""
        resources = [
            self.bitable_reader,
            self.gitlab_collector,
            self.llm_summarizer,
            self.messenger,
            self.doc_writer,
        ]

        for resource in resources:
            close_method = getattr(resource, "close", None)
            if callable(close_method):
                try:
                    result = close_method()
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:  # pragma: no cover
                    logger.debug(f"关闭资源失败({resource.__class__.__name__}): {e}")

    async def _send_to_group(self, report: DailyReport, enabled: bool) -> bool | None:
        """Step 4: 推送到飞书群"""
        if not enabled:
            logger.info("   ⏭️  [4/5] 未配置飞书群 Webhook，跳过推送")
            return None

        logger.info("   📨 [4/5] 推送到飞书群...")
        try:
            success = await self.messenger.send_webhook(report.markdown_content)
            if success:
                logger.info("   ✅ 飞书群推送成功")
            else:
                report.errors.append("飞书群推送返回失败状态")
                logger.warning("   ⚠️  飞书群推送返回失败状态")
            return success
        except Exception as e:
            error_msg = f"飞书群推送失败: {e}"
            logger.error(f"   ❌ {error_msg}")
            report.errors.append(error_msg)
            return False

    async def _write_to_document(self, report: DailyReport, enabled: bool) -> bool | None:
        """Step 5: 写入飞书文档（按日期归档）"""
        if not enabled:
            logger.info("   ⏭️  [5/5] 未配置飞书文档目标，跳过写入")
            return None

        logger.info("   📝 [5/5] 写入飞书文档...")
        try:
            success = await self.doc_writer.write_report(
                report.markdown_content, report.date
            )
            if success:
                logger.info("   ✅ 飞书文档写入成功")
            else:
                report.errors.append("飞书文档写入返回失败状态")
                logger.warning("   ⚠️  飞书文档写入返回失败状态")
            return success
        except Exception as e:
            error_msg = f"飞书文档写入失败: {e}"
            logger.error(f"   ❌ {error_msg}")
            report.errors.append(error_msg)
            return False

    @staticmethod
    def _fallback_report(
        developer_commits: list, report_date: str
    ) -> str:
        """降级模板（当 LLM 不可用时）"""
        lines = [
            f"## 📊 Astribot 团队日报 | {report_date}",
            "",
            "> ⚠️ LLM 摘要服务暂不可用，以下为原始数据汇总。",
            "",
        ]
        for dc in developer_commits:
            if dc.total_commits > 0:
                lines.append(f"### {dc.developer.name} ({dc.developer.component})")
                for c in dc.commits:
                    lines.append(f"- `{c.short_sha}` {c.message}")
                lines.append(f"> {dc.total_commits} commits | +{dc.total_additions}/-{dc.total_deletions}")
                lines.append("")
        return "\n".join(lines)
