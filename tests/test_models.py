"""
数据模型单元测试
================
"""

from datetime import datetime, timezone

from agent.models import (
    CommitInfo,
    DailyReport,
    Developer,
    DeveloperCommits,
    ReportStatus,
)


def test_developer_model():
    """测试 Developer 模型"""
    dev = Developer(
        name="张伟",
        gitlab_username="zhang.wei",
        component="运动控制系统",
        email="zhang.wei@astribot.com",
    )
    assert dev.name == "张伟"
    assert dev.gitlab_username == "zhang.wei"


def test_commit_info_model():
    """测试 CommitInfo 模型"""
    commit = CommitInfo(
        sha="abc123def456",
        short_sha="abc123de",
        message="feat: add new feature",
        authored_date=datetime.now(timezone.utc),
        project_name="test-project",
        additions=100,
        deletions=50,
    )
    assert commit.additions == 100
    assert commit.deletions == 50


def test_developer_commits_stats():
    """测试 DeveloperCommits 统计计算"""
    dev = Developer(name="Test", gitlab_username="test", component="TestComp")
    commits = [
        CommitInfo(
            sha=f"sha{i}",
            short_sha=f"sha{i}",
            message=f"commit {i}",
            authored_date=datetime.now(timezone.utc),
            project_name="proj",
            additions=100 * (i + 1),
            deletions=50 * (i + 1),
        )
        for i in range(3)
    ]

    dc = DeveloperCommits(developer=dev, commits=commits)
    dc.compute_stats()

    assert dc.total_commits == 3
    assert dc.total_additions == 100 + 200 + 300  # 600
    assert dc.total_deletions == 50 + 100 + 150  # 300


def test_daily_report_compute_totals():
    """测试 DailyReport 全局统计"""
    report = DailyReport(date="2024-01-15")

    dev1 = Developer(name="A", gitlab_username="a", component="CompA")
    dev2 = Developer(name="B", gitlab_username="b", component="CompB")

    dc1 = DeveloperCommits(developer=dev1, total_commits=5)
    dc2 = DeveloperCommits(developer=dev2, total_commits=3)

    report.developer_summaries = [dc1, dc2]
    report.compute_totals()

    assert report.total_developers == 2
    assert report.total_commits == 8


def test_report_status_enum():
    """测试报告状态枚举"""
    assert ReportStatus.SUCCESS.value == "success"
    assert ReportStatus.PARTIAL.value == "partial"
    assert ReportStatus.FAILED.value == "failed"
