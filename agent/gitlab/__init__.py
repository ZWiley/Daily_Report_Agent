"""
GitLab 集成模块
===============
- GitLabCollector: 通过 GitLab API 采集 commit 记录
- MockGitLabCollector: 生成逼真的模拟数据
"""

from agent.gitlab.collector import GitLabCollector, MockGitLabCollector

__all__ = ["GitLabCollector", "MockGitLabCollector"]
