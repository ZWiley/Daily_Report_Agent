"""
LLM 摘要模块测试
=================
"""

import pytest

from agent.gitlab.collector import MockGitLabCollector
from agent.feishu.bitable import MockBitableReader
from agent.llm.summarizer import MockLLMSummarizer


@pytest.mark.asyncio
async def test_mock_llm_summarizer():
    """测试 Mock LLM 摘要生成"""
    # 准备数据
    reader = MockBitableReader()
    developers = await reader.fetch_developers()

    collector = MockGitLabCollector()
    dev_commits = await collector.collect_all(developers)

    # 生成摘要
    summarizer = MockLLMSummarizer()
    report = await summarizer.generate_report(dev_commits, "2024-01-15")

    # 验证
    assert len(report) > 200
    assert "Astribot" in report
    assert "2024-01-15" in report
    assert "##" in report  # 有标题
    assert any(dev.name in report for dev in developers)


@pytest.mark.asyncio
async def test_mock_llm_report_structure():
    """测试日报结构完整性"""
    reader = MockBitableReader()
    developers = await reader.fetch_developers()

    collector = MockGitLabCollector()
    dev_commits = await collector.collect_all(developers)

    summarizer = MockLLMSummarizer()
    report = await summarizer.generate_report(dev_commits, "2024-01-15")

    # 验证关键区块存在
    assert "团队日报" in report
    assert "commits" in report.lower() or "commit" in report.lower()
    # 应该包含分隔线
    assert "---" in report
