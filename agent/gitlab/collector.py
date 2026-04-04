"""
GitLab Commit 采集器
====================
通过 GitLab REST API v4 获取指定开发者在过去 N 小时内的所有 commit 记录。

API 参考：
  https://docs.gitlab.com/ee/api/commits.html
  https://docs.gitlab.com/ee/api/events.html
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.config import GitLabConfig
from agent.models import CommitInfo, Developer, DeveloperCommits

logger = logging.getLogger(__name__)


class GitLabCollector:
    """GitLab Commit 采集器（真实 API）"""

    def __init__(self, config: GitLabConfig, hours_lookback: int = 24) -> None:
        self.config = config
        self.hours_lookback = hours_lookback
        self._client = httpx.AsyncClient(
            base_url=config.url.rstrip("/"),
            headers={"PRIVATE-TOKEN": config.private_token},
            timeout=30.0,
        )

    async def __aenter__(self) -> GitLabCollector:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if not self._client.is_closed:
            await self._client.aclose()

    async def _fetch_paginated_list(
        self,
        url: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """拉取 GitLab 列表接口的全部分页数据。"""
        page = 1
        all_items: list[dict[str, Any]] = []

        while True:
            resp = await self._client.get(
                url,
                params={**params, "page": page},
            )
            resp.raise_for_status()

            items = resp.json()
            if not isinstance(items, list):
                break

            all_items.extend(items)

            next_page = resp.headers.get("X-Next-Page", "").strip()
            if next_page:
                try:
                    page = int(next_page)
                    continue
                except ValueError:
                    logger.warning(f"⚠️  非法分页头 X-Next-Page={next_page!r}，将停止继续翻页")
                    break

            # 兼容未返回分页头的场景
            if len(items) < int(params.get("per_page", 100)):
                break

            page += 1

        return all_items

    async def collect_all(self, developers: list[Developer]) -> list[DeveloperCommits]:
        """
        并发采集所有开发者的 commit 记录。

        Args:
            developers: 开发者列表

        Returns:
            每位开发者的 commit 汇总
        """
        # 使用 Semaphore 限制并发数，避免打爆 GitLab
        sem = asyncio.Semaphore(5)

        async def _collect_with_limit(dev: Developer) -> DeveloperCommits:
            async with sem:
                return await self.collect_developer_commits(dev)

        tasks = [_collect_with_limit(dev) for dev in developers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        developer_commits: list[DeveloperCommits] = []
        for dev, result in zip(developers, results):
            if isinstance(result, Exception):
                error_msg = str(result)
                logger.error(f"❌ 采集 {dev.name} 的 commit 失败: {error_msg}")
                developer_commits.append(
                    DeveloperCommits(developer=dev, collection_error=error_msg)
                )
            else:
                developer_commits.append(result)

        total = sum(dc.total_commits for dc in developer_commits)
        active = sum(1 for dc in developer_commits if dc.total_commits > 0)
        logger.info(f"📊 采集完成: {active}/{len(developers)} 活跃开发者，共 {total} 条 commit")

        return developer_commits

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def collect_developer_commits(self, developer: Developer) -> DeveloperCommits:
        """采集单个开发者的 commit 记录"""
        since = datetime.now(timezone.utc) - timedelta(hours=self.hours_lookback)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Step 1: 获取用户参与的事件（push events）
        events_url = f"/api/v4/users/{developer.gitlab_username}/events"
        params = {
            "action": "pushed",
            "after": since_str,
            "per_page": 100,
        }

        events = await self._fetch_paginated_list(events_url, params)

        # Step 2: 从事件中提取 project IDs，然后获取详细 commit 信息
        project_ids: set[int] = set()
        for event in events:
            if pid := event.get("project_id"):
                project_ids.add(pid)

        # Step 3: 遍历每个 project 获取该用户的 commits
        all_commits: list[CommitInfo] = []
        for pid in project_ids:
            commits = await self._fetch_project_commits(pid, developer.gitlab_username, since_str)
            all_commits.extend(commits)

        # 按时间排序
        all_commits.sort(key=lambda c: c.authored_date, reverse=True)

        result = DeveloperCommits(developer=developer, commits=all_commits)
        result.compute_stats()

        if result.total_commits > 0:
            logger.info(
                f"  ✓ {developer.name}: {result.total_commits} commits, "
                f"+{result.total_additions}/-{result.total_deletions}"
            )

        return result

    async def _fetch_project_commits(
        self, project_id: int, username: str, since: str
    ) -> list[CommitInfo]:
        """获取指定项目中某用户的 commits"""
        url = f"/api/v4/projects/{project_id}/repository/commits"
        params = {
            "author": username,
            "since": since,
            "per_page": 100,
            "with_stats": "true",
        }

        try:
            commits_data = await self._fetch_paginated_list(url, params)
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"⚠️  拉取项目 commits 失败(project={project_id}, user={username}): "
                f"{e.response.status_code}"
            )
            return []

        # 获取项目信息
        proj_resp = await self._client.get(f"/api/v4/projects/{project_id}")
        proj_data = proj_resp.json() if proj_resp.status_code == 200 else {}

        commits: list[CommitInfo] = []

        for c in commits_data:
            stats = c.get("stats", {})
            commits.append(
                CommitInfo(
                    sha=c["id"],
                    short_sha=c["short_id"],
                    message=c["message"].strip(),
                    authored_date=datetime.fromisoformat(
                        c["authored_date"].replace("Z", "+00:00")
                    ),
                    project_name=proj_data.get("name", f"project-{project_id}"),
                    project_url=proj_data.get("web_url", ""),
                    web_url=c.get("web_url", ""),
                    additions=stats.get("additions", 0),
                    deletions=stats.get("deletions", 0),
                )
            )

        return commits


class MockGitLabCollector:
    """
    Mock GitLab Commit 采集器
    ========================
    生成逼真的模拟 commit 数据，模拟一人可跨多个仓库提交的真实场景。
    """

    # 仓库维度的 commit 模板（仓库名 → commit 列表）
    REPO_COMMIT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
        "motor-control": [
            {"msg": "feat(gait): 优化双足步态规划算法，步态过渡更平滑", "add": 245, "del": 89},
            {"msg": "fix(servo): 修复高速运动下伺服电机PID参数漂移问题", "add": 67, "del": 23},
            {"msg": "perf(trajectory): 轨迹插补算法性能提升40%，减少控制延迟", "add": 156, "del": 78},
            {"msg": "test(gait): 添加新步态模式的仿真对比测试", "add": 312, "del": 0},
        ],
        "robot-firmware": [
            {"msg": "feat(can): 完成 CAN-FD 总线驱动移植，支持 8Mbps 速率", "add": 567, "del": 123},
            {"msg": "fix(imu): 修复 IMU 数据融合中陀螺仪零偏校准异常", "add": 89, "del": 34},
            {"msg": "feat(power): 新增电池管理系统BMS协议解析，支持SOC精确估算", "add": 234, "del": 0},
        ],
        "vision-perception": [
            {"msg": "feat(detection): 集成 YOLOv9 目标检测模型，识别精度提升至 mAP 0.87", "add": 523, "del": 167},
            {"msg": "fix(depth): 修复双目深度估计在近距离场景下的噪声问题", "add": 89, "del": 34},
            {"msg": "feat(hand): 新增手部姿态估计模块，支持21个关键点实时追踪", "add": 678, "del": 0},
            {"msg": "chore(model): 更新 TensorRT 引擎到 10.0，推理延迟降低 15ms", "add": 45, "del": 32},
        ],
        "slam-navigation": [
            {"msg": "feat(lidar): 实现基于 3D LiDAR 的实时地图构建，支持动态障碍物过滤", "add": 789, "del": 234},
            {"msg": "fix(localization): 修复回环检测在相似场景下的误匹配问题", "add": 123, "del": 56},
            {"msg": "perf(mapping): 八叉树地图压缩算法优化，内存占用降低 60%", "add": 234, "del": 145},
        ],
        "web-dashboard": [
            {"msg": "feat(ui): 新增实时运动状态3D可视化面板", "add": 456, "del": 23},
            {"msg": "fix(dashboard): 修复多机器人视图切换时的WebSocket连接泄漏", "add": 34, "del": 12},
            {"msg": "style(theme): 适配暗色模式，优化数据图表配色方案", "add": 189, "del": 67},
            {"msg": "feat(alert): 实现智能告警聚合，同类告警自动折叠展示", "add": 267, "del": 45},
        ],
        "cloud-scheduler": [
            {"msg": "feat(scheduler): 实现多机器人协同任务调度引擎 v2", "add": 891, "del": 456},
            {"msg": "fix(grpc): 修复高并发场景下 gRPC stream 偶发断连问题", "add": 78, "del": 23},
            {"msg": "feat(api): 新增批量指令下发接口，支持最多32台机器人同步控制", "add": 345, "del": 0},
            {"msg": "docs(api): 更新 OpenAPI 文档，补充任务调度相关接口说明", "add": 156, "del": 89},
        ],
        "ai-inference": [
            {"msg": "feat(llm): 集成端侧大语言模型，支持自然语言指令理解", "add": 1023, "del": 234},
            {"msg": "perf(inference): 模型量化 INT8 部署，推理速度提升 2.3x", "add": 345, "del": 178},
            {"msg": "feat(rag): 实现本地知识库 RAG 检索，支持产品手册智能问答", "add": 567, "del": 0},
            {"msg": "fix(tokenizer): 修复中文分词器在特殊字符场景下的编码错误", "add": 45, "del": 12},
        ],
        "data-pipeline": [
            {"msg": "feat(pipeline): 新增传感器数据实时 ETL 管道，支持 10万条/秒吞吐", "add": 456, "del": 123},
            {"msg": "fix(kafka): 修复 Kafka consumer 在分区重平衡时的消息丢失问题", "add": 78, "del": 23},
            {"msg": "feat(monitor): 实现数据质量监控仪表板，异常数据自动标注", "add": 345, "del": 89},
            {"msg": "perf(storage): 优化时序数据库写入策略，批量写入延迟降低至 5ms", "add": 167, "del": 78},
        ],
        "robot-sdk": [
            {"msg": "feat(sdk): 新增 Python SDK 远程控制接口", "add": 320, "del": 0},
            {"msg": "docs(sdk): 补充 SDK 使用示例和 API 文档", "add": 180, "del": 45},
        ],
    }

    # 每位开发者负责的仓库（一人多仓库）
    DEVELOPER_REPOS: dict[str, list[str]] = {
        "zhang.wei": ["motor-control", "robot-firmware"],
        "li.na": ["vision-perception", "ai-inference"],
        "wang.qiang": ["slam-navigation", "robot-firmware"],
        "zhao.min": ["web-dashboard"],
        "chen.liang": ["cloud-scheduler", "data-pipeline"],
        "liu.yang": ["robot-firmware", "robot-sdk"],
        "yang.fan": ["ai-inference", "data-pipeline"],
        "wu.tong": ["data-pipeline", "cloud-scheduler"],
    }

    def __init__(self, hours_lookback: int = 24) -> None:
        self.hours_lookback = hours_lookback

    async def collect_all(self, developers: list[Developer]) -> list[DeveloperCommits]:
        """生成模拟的 commit 数据（一人可跨多仓库）"""
        results: list[DeveloperCommits] = []
        now = datetime.now(timezone.utc)

        for dev in developers:
            repos = self.DEVELOPER_REPOS.get(dev.gitlab_username, ["misc-project"])
            all_commits: list[CommitInfo] = []

            for repo_name in repos:
                templates = self.REPO_COMMIT_TEMPLATES.get(repo_name, [
                    {"msg": f"feat: 功能开发中", "add": 100, "del": 30},
                ])

                # 每个仓库随机 1~2 条 commit
                num_commits = random.randint(1, min(2, len(templates)))
                selected = random.sample(templates, num_commits)

                for tmpl in selected:
                    hours_ago = random.uniform(1, self.hours_lookback - 1)
                    commit_time = now - timedelta(hours=hours_ago)

                    sha = f"{random.randint(0, 0xFFFFFFFFFFFFFFFF):016x}" * 2 + f"{random.randint(0, 0xFFFF):04x}"
                    sha = sha[:40]

                    all_commits.append(
                        CommitInfo(
                            sha=sha,
                            short_sha=sha[:8],
                            message=tmpl["msg"],
                            authored_date=commit_time,
                            project_name=repo_name,
                            project_url=f"https://gitlab.astribot.com/astribot/{repo_name}",
                            web_url=f"https://gitlab.astribot.com/astribot/{repo_name}/-/commit/{sha[:8]}",
                            additions=tmpl["add"] + random.randint(-20, 20),
                            deletions=tmpl["del"] + random.randint(-10, 10),
                        )
                    )

            all_commits.sort(key=lambda c: c.authored_date, reverse=True)
            dc = DeveloperCommits(developer=dev, commits=all_commits)
            dc.compute_stats()
            results.append(dc)

        total = sum(dc.total_commits for dc in results)
        all_repos: set[str] = set()
        for dc in results:
            all_repos.update(dc.active_projects)
        logger.info(
            f"🎭 [Mock] 生成 {len(results)} 位开发者的模拟数据，"
            f"共 {total} 条 commit，涉及 {len(all_repos)} 个仓库"
        )

        return results
