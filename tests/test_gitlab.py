"""
GitLab 模块测试
================
"""

import pytest

from agent.gitlab.collector import MockGitLabCollector
from agent.models import Developer


@pytest.mark.asyncio
async def test_mock_gitlab_collector():
    """测试 Mock GitLab 采集器"""
    collector = MockGitLabCollector(hours_lookback=24)

    developers = [
        Developer(name="张伟", gitlab_username="zhang.wei", component="运动控制系统"),
        Developer(name="李娜", gitlab_username="li.na", component="视觉感知模块"),
    ]

    results = await collector.collect_all(developers)

    assert len(results) == 2
    for dc in results:
        assert dc.developer.name in ["张伟", "李娜"]
        assert dc.total_commits > 0
        assert len(dc.commits) > 0

        for commit in dc.commits:
            assert commit.sha
            assert len(commit.sha) > 0
            assert commit.message
            assert commit.project_name
            assert commit.additions >= 0


@pytest.mark.asyncio
async def test_mock_collector_all_components():
    """测试所有组件都能生成数据"""
    from agent.feishu.bitable import MockBitableReader

    reader = MockBitableReader()
    developers = await reader.fetch_developers()

    collector = MockGitLabCollector()
    results = await collector.collect_all(developers)

    assert len(results) == len(developers)
    for dc in results:
        assert dc.total_commits >= 1
