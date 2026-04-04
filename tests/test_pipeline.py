"""
Pipeline 集成测试
==================
使用 Mock 模式验证整个 Pipeline 流程。
"""

from __future__ import annotations

import pytest

from agent.config import AgentConfig
from agent.models import ReportStatus
from agent.pipeline import DailyReportPipeline


@pytest.mark.asyncio
async def test_pipeline_mock_mode():
    """测试 Mock 模式下的完整 Pipeline"""
    config = AgentConfig()
    pipeline = DailyReportPipeline(config, use_mock=True)

    report = await pipeline.run()

    # 基本断言
    assert report.status == ReportStatus.SUCCESS
    assert report.total_developers > 0
    assert report.total_commits > 0
    assert len(report.markdown_content) > 100
    assert "Astribot" in report.markdown_content
    assert report.date is not None
    assert len(report.errors) == 0


@pytest.mark.asyncio
async def test_pipeline_generates_markdown():
    """测试生成的 Markdown 包含必要内容"""
    config = AgentConfig()
    pipeline = DailyReportPipeline(config, use_mock=True)

    report = await pipeline.run()
    md = report.markdown_content

    # 验证 Markdown 内容结构
    assert "##" in md  # 有标题
    assert "commit" in md.lower() or "commits" in md.lower()  # 提到 commit
    assert any(
        name in md for name in ["张伟", "李娜", "王强", "赵敏"]
    )  # 包含开发者名字


@pytest.mark.asyncio
async def test_pipeline_developer_data():
    """测试开发者数据完整性"""
    config = AgentConfig()
    pipeline = DailyReportPipeline(config, use_mock=True)

    report = await pipeline.run()

    for dc in report.developer_summaries:
        assert dc.developer.name
        assert dc.developer.gitlab_username
        assert isinstance(dc.developer.component, str)  # 可选字段
        assert dc.total_commits >= 0

        for commit in dc.commits:
            assert commit.sha
            assert commit.message
            assert commit.project_name
