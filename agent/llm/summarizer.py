"""
LLM 智能摘要引擎
================
将开发者的 commit 记录通过大语言模型进行语义分析，
生成结构化、人类可读的团队每日进展日报。

设计思路：
  1. 精心设计的 System Prompt 确保输出格式一致
  2. 分批处理避免 Token 溢出
  3. 基于 OpenAI Python SDK 的 base_url 机制，
     兼容 OpenAI / DeepSeek / 智谱 / 通义千问 / Moonshot / Ollama 等所有兼容接口
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime

from openai import AsyncOpenAI

from agent.config import LLMConfig
from agent.models import DeveloperCommits

logger = logging.getLogger(__name__)

# ==========================================
# Prompt Engineering - 日报生成提示词
# ==========================================

SYSTEM_PROMPT = """你是 Astribot 的 AI 技术项目助理，负责将 Git commit 记录转化为简洁、专业的团队每日进展日报。

## 你的任务
根据提供的每位开发者的 commit 记录，生成一份 **Markdown 格式** 的团队日报。

## 输出格式要求

1. **开头**：用一句话概括当天团队整体进展（突出关键成果和数据）
2. **按开发者分组**：每个开发者一个章节，包含：
   - 开发者姓名
   - 按仓库列出该开发者的主要进展（用 1-2 句话提炼核心价值，不要逐条翻译 commit message）
   - 如果涉及多个仓库，分别说明每个仓库的进展
   - 关键数据：commit 数量、代码变更规模
3. **风险/关注点**：如果发现 fix/hotfix 类 commit 较多，或某仓库变更量异常大，简要提示
4. **结尾**：一句话的团队效能观察（可选）

## 风格要求
- 语言：中文
- 语气：专业但不刻板，适合在飞书群中阅读
- 重点突出 **业务价值** 而非技术细节
- 使用 emoji 让日报更有活力但不过度
- 不需要重复 commit SHA 或精确时间

## 示例输出结构
```markdown
## 📊 团队日报 | 2024-01-15

> 今日团队共 **X 位** 工程师活跃，提交 **Y 次** 代码变更，涉及 **Z 个** 仓库。

### 👤 张伟
**motor-control** — 步态算法持续优化，步态过渡平滑度提升
**robot-firmware** — 修复伺服电机 PID 参数漂移问题
> 📈 4 commits (2 repos) | +345 / -89 lines

### 👤 杨帆
**ai-inference** — 模型量化 INT8 部署，推理速度提升 2.3x
**knowledge-base** — 实现本地 RAG 检索，支持产品手册智能问答
> 📈 5 commits (2 repos) | +1200 / -200 lines

---
⚡ **关注点**：某仓库 fix 类提交较多，建议关注稳定性。
```"""

USER_PROMPT_TEMPLATE = """## 待分析的 Commit 数据

**日期**: {date}
**活跃开发者**: {active_count} / {total_count} 人
**总 Commit 数**: {total_commits}

---

{developer_sections}

---

请根据以上数据生成团队日报。"""


def format_developer_section(dc: DeveloperCommits) -> str:
    """将单个开发者的 commit 数据格式化为 LLM 输入（按仓库分组）"""
    if not dc.commits:
        return f"### {dc.developer.name}\n> 今日无提交记录\n"

    projects = dc.active_projects
    repo_label = f" ({len(projects)} repos)" if len(projects) > 1 else ""

    lines = [
        f"### {dc.developer.name}",
        f"GitLab: @{dc.developer.gitlab_username} | "
        f"Commits: {dc.total_commits}{repo_label} | "
        f"+{dc.total_additions}/-{dc.total_deletions} lines",
        "",
    ]

    by_project = dc.commits_by_project()
    for proj_name, proj_commits in by_project.items():
        lines.append(f"**[{proj_name}]**")
        for c in proj_commits:
            lines.append(f"- `[{c.short_sha}]` {c.message}")
        lines.append("")

    return "\n".join(lines)


class LLMSummarizer:
    """
    通用 LLM 摘要引擎
    =================
    基于 OpenAI Python SDK 的 ``base_url`` 机制，兼容所有提供
    OpenAI 兼容接口的模型服务，包括但不限于：

    - OpenAI (GPT-4o / GPT-4o-mini)
    - DeepSeek (deepseek-chat / deepseek-reasoner)
    - 智谱 (GLM-4-Flash / GLM-4-Plus)
    - 通义千问 (qwen-plus / qwen-turbo)
    - Moonshot (moonshot-v1-8k)
    - 豆包 / 百川 / MiniMax / 零一万物
    - 本地 Ollama (qwen2.5 / llama3 等)
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def close(self) -> None:
        """关闭底层异步客户端。"""
        close_method = getattr(self._client, "close", None)
        if callable(close_method):
            result = close_method()
            if inspect.isawaitable(result):
                await result

    async def generate_report(
        self, developer_commits: list[DeveloperCommits], report_date: str
    ) -> str:
        """
        生成团队日报。

        Args:
            developer_commits: 所有开发者的 commit 汇总
            report_date: 报告日期

        Returns:
            Markdown 格式的日报内容
        """
        # 构建 LLM 输入
        active = [dc for dc in developer_commits if dc.total_commits > 0]
        total_commits = sum(dc.total_commits for dc in developer_commits)

        developer_sections = "\n\n".join(
            format_developer_section(dc) for dc in developer_commits
        )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            date=report_date,
            active_count=len(active),
            total_count=len(developer_commits),
            total_commits=total_commits,
            developer_sections=developer_sections,
        )

        model_label = self.config.model
        if self.config.provider:
            model_label = f"{self.config.provider}/{self.config.model}"
        logger.info(f"🧠 正在调用 LLM ({model_label}) 生成日报摘要...")

        response = await self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

        content = response.choices[0].message.content or ""

        # Token 用量日志
        usage = response.usage
        if usage:
            logger.info(
                f"✅ 日报生成完成 "
                f"(prompt: {usage.prompt_tokens}, "
                f"completion: {usage.completion_tokens}, "
                f"total: {usage.total_tokens} tokens)"
            )
        else:
            logger.info("✅ 日报生成完成")

        return content


class MockLLMSummarizer:
    """
    Mock LLM 摘要引擎
    =================
    不依赖真实 API，直接使用模板生成日报。
    用于开发调试和演示。
    """

    async def generate_report(
        self, developer_commits: list[DeveloperCommits], report_date: str
    ) -> str:
        """生成模板化的日报（按人+仓库分组）"""
        active = [dc for dc in developer_commits if dc.total_commits > 0]
        total_commits = sum(dc.total_commits for dc in developer_commits)
        total_additions = sum(dc.total_additions for dc in developer_commits)
        total_deletions = sum(dc.total_deletions for dc in developer_commits)

        # 统计涉及的仓库总数
        all_repos: set[str] = set()
        for dc in active:
            all_repos.update(dc.active_projects)

        lines = [
            f"## 📊 Astribot 团队日报 | {report_date}",
            "",
            f"> 今日团队共 **{len(active)} 位** 工程师活跃，"
            f"提交 **{total_commits} 次** 代码变更 "
            f"(**+{total_additions}** / **-{total_deletions}** 行)，"
            f"涉及 **{len(all_repos)} 个** 仓库。",
            "",
            "---",
            "",
        ]

        for dc in active:
            by_project = dc.commits_by_project()
            repo_count = len(by_project)
            repo_suffix = f" ({repo_count} repos)" if repo_count > 1 else ""

            lines.append(f"### 👤 {dc.developer.name}")
            lines.append("")

            for proj_name, proj_commits in by_project.items():
                # 提取每个仓库的 commit 摘要
                highlights = []
                for c in proj_commits:
                    msg = c.message
                    if ":" in msg:
                        desc = msg.split(":", 1)[1].strip()
                    else:
                        desc = msg
                    highlights.append(desc)

                summary = "；".join(highlights)
                lines.append(f"**{proj_name}** — {summary}")

            lines.append("")
            lines.append(
                f"> 📈 {dc.total_commits} commits{repo_suffix} | "
                f"+{dc.total_additions} / -{dc.total_deletions} lines"
            )
            lines.append("")

        # 风险提示
        fix_heavy = [
            dc for dc in active
            if any("fix" in c.message.lower() for c in dc.commits)
            and sum(1 for c in dc.commits if "fix" in c.message.lower()) >= 2
        ]

        lines.append("---")
        lines.append("")

        if fix_heavy:
            repos_with_fixes: list[str] = []
            for dc in fix_heavy:
                for proj, commits in dc.commits_by_project().items():
                    if sum(1 for c in commits if "fix" in c.message.lower()) >= 1:
                        repos_with_fixes.append(proj)
            lines.append(f"⚡ **关注点**：{'、'.join(set(repos_with_fixes))} 仓库有较多 bugfix 提交，建议关注稳定性。")
        else:
            lines.append("✅ **整体状态良好**：各仓库开发进展顺利，无异常风险点。")

        lines.append("")
        lines.append("---")
        lines.append(f"*🤖 由 Astribot Daily Agent 自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

        return "\n".join(lines)
