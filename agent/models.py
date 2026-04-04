"""
数据模型定义
============
所有模块共享的数据结构，确保类型安全和数据验证。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Developer(BaseModel):
    """开发者信息（来自飞书多维表格）"""

    name: str = Field(description="开发者姓名")
    gitlab_username: str = Field(description="GitLab 用户名")
    component: str = Field(default="", description="负责的组件/备注（可选，日报中仓库名自动读取）")
    email: str = Field(default="", description="邮箱（可选）")


class CommitInfo(BaseModel):
    """单条 Commit 信息"""

    sha: str = Field(description="Commit SHA")
    short_sha: str = Field(description="短 SHA（前8位）")
    message: str = Field(description="Commit Message")
    authored_date: datetime = Field(description="提交时间")
    project_name: str = Field(description="项目/仓库名称")
    project_url: str = Field(default="", description="项目 URL")
    web_url: str = Field(default="", description="Commit Web URL")
    additions: int = Field(default=0, description="新增行数")
    deletions: int = Field(default=0, description="删除行数")


class DeveloperCommits(BaseModel):
    """单个开发者的 Commit 汇总"""

    developer: Developer
    commits: list[CommitInfo] = Field(default_factory=list)
    total_commits: int = Field(default=0)
    total_additions: int = Field(default=0)
    total_deletions: int = Field(default=0)
    collection_error: str = Field(default="", description="采集异常信息；为空表示采集成功")

    def compute_stats(self) -> None:
        """计算统计信息"""
        self.total_commits = len(self.commits)
        self.total_additions = sum(c.additions for c in self.commits)
        self.total_deletions = sum(c.deletions for c in self.commits)

    @property
    def active_projects(self) -> list[str]:
        """当日涉及的仓库名列表（去重、保持顺序）"""
        seen: set[str] = set()
        projects: list[str] = []
        for c in self.commits:
            if c.project_name not in seen:
                seen.add(c.project_name)
                projects.append(c.project_name)
        return projects

    def commits_by_project(self) -> dict[str, list["CommitInfo"]]:
        """按仓库分组返回 commit 列表。"""
        grouped: dict[str, list[CommitInfo]] = {}
        for c in self.commits:
            grouped.setdefault(c.project_name, []).append(c)
        return grouped


class ReportStatus(str, Enum):
    """日报状态"""

    SUCCESS = "success"
    PARTIAL = "partial"  # 部分数据获取失败
    FAILED = "failed"


class DailyReport(BaseModel):
    """每日报告"""

    date: str = Field(description="报告日期 (YYYY-MM-DD)")
    generated_at: datetime = Field(default_factory=datetime.now)
    status: ReportStatus = Field(default=ReportStatus.SUCCESS)
    developer_summaries: list[DeveloperCommits] = Field(default_factory=list)
    markdown_content: str = Field(default="", description="LLM 生成的 Markdown 日报")
    total_developers: int = Field(default=0)
    total_commits: int = Field(default=0)
    errors: list[str] = Field(default_factory=list, description="采集过程中的错误")

    def compute_totals(self) -> None:
        """计算全局统计"""
        self.total_developers = len(self.developer_summaries)
        self.total_commits = sum(ds.total_commits for ds in self.developer_summaries)
