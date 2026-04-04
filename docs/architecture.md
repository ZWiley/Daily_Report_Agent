# 🏗️ 日报 Agent — 架构设计

> **核心公式**：`Agent = Model + Harness`  
> **工程范式**：Harness Engineering（2026）

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                  Daily Report Agent v2.0                      │
│                                                              │
│  agent.json ─── Agent 声明                                   │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Agent Core (agent_core.py)                 │ │
│  │                                                         │ │
│  │   for each step:                                        │ │
│  │     → 选择 Tool → safe_execute → 观测结果               │ │
│  │     → 质量门检查 → 不合格自动重做                       │ │
│  │     → 记录 Trace                                        │ │
│  │                                                         │ │
│  └────────────────────┬────────────────────────────────────┘ │
│                       │                                      │
│  ┌────────────────────▼────────────────────────────────────┐ │
│  │                 Tools (7 个标准化工具)                   │ │
│  │                                                         │ │
│  │  fetch_developers ── collect_commits ── generate_summary│ │
│  │  check_report_quality ── send_to_feishu_group           │ │
│  │  write_to_feishu_doc ── query_history                   │ │
│  │                                                         │ │
│  └──┬──────────────┬──────────────┬────────────────────────┘ │
│     │              │              │                           │
│  ┌──▼───┐    ┌─────▼────┐   ┌────▼───┐                      │
│  │feishu│    │  gitlab   │   │  llm   │     SDK Layer        │
│  └──────┘    └──────────┘   └────────┘                       │
│                                                              │
│  scheduler.py ─── 定时调度（Cron + 重试 + 补执行）           │
│  config.py ────── 配置管理（.env + Provider 预设）           │
│  models.py ────── 数据模型（Developer / Commit / Report）    │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Harness 组件实现状态

| Harness 组件 | 当前实现状态 | 位置 |
|---|---|---|
| **Agent 声明** | ✅ `agent.json` 保存工具列表、`execution_plan`、护栏参数和运行约定 | `agent/agent.json` |
| **装配层** | ✅ `agent_core.py` 负责加载 spec、注册工具并组装 engine | `agent/agent_core.py` |
| **Engine 内核** | ✅ `agent/engine/` 收敛为 spec、context、runtime 三个模块，保留当前日报场景必须能力 | `agent/engine/` |
| **标准化 Tool** | ✅ 7 个 Tool，统一 `BaseTool` + `ToolResult` 接口 | `agent/tools/` |
| **自验证** | ✅ `check_report_quality` 做规则校验；不合格自动重生摘要 | `agent/tools/check_quality.py` |
| **护栏** | ✅ `max_steps` / `max_retries` / `required_quality_score` / `blocked_tools` 已生效；`token_budget` 仍为预留字段 | `agent.json` + `agent/engine/runtime.py` |
| **执行追踪** | ✅ `AgentTrace` 记录每步 tool/stage_id/success/duration | `agent/engine/runtime.py` |
| **记忆** | ⚠️ 已有 `execution_history.json`，目前主要用于调度和历史查询，不参与规划 | `agent/scheduler.py` |
| **容错降级** | ✅ LLM 失败降级模板、GitLab 局部失败标记 partial、输出通道并行汇总判定 | `agent/engine/runtime.py` + 各 Tool |

---

## 3. Tool 标准接口

所有 Tool 继承 `BaseTool`，遵循统一协议：

```python
class BaseTool(ABC):
    name: str                    # 工具名（Agent 选择用）
    description: str             # 功能描述（LLM 可读）
    parameters: dict             # JSON Schema 参数定义

    async def execute(**kwargs) -> ToolResult    # 执行
    async def safe_execute(**kwargs) -> ToolResult  # 带计时 + 异常捕获
    def to_schema() -> dict      # 输出 OpenAI function-calling 格式

@dataclass
class ToolResult:
    success: bool
    data: Any
    error: str | None
    metadata: dict               # duration_ms 等
```

### 工具清单

| 工具 | 功能 | 输入 | 输出 |
|---|---|---|---|
| `fetch_developers` | 读飞书多维表格 | — | `list[Developer]` |
| `collect_commits` | 采集 GitLab 提交 | `developers` | `list[DeveloperCommits]` |
| `generate_summary` | LLM 生成日报 | `developer_commits`, `date` | Markdown 字符串 |
| `check_report_quality` | 质量自评 | `report_content`, 预期数据量 | `{passed, score, issues}` |
| `send_to_feishu_group` | 飞书群推送 | `markdown_content` | sent / skipped |
| `write_to_feishu_doc` | 飞书文档归档 | `markdown_content`, `date` | written / skipped |
| `query_history` | 查询执行历史 | `last_n` | 历史记录 + 成功率 |

---

## 4. Agent Loop 执行流程

```text
execution_plan
  1. fetch_developers
     └─ store -> $developers

  2. collect_commits
     └─ params: developers=$developers
     └─ store -> $developer_commits
     └─ after_step: sync_commit_collection

  3. generate_report (summary_loop)
     └─ generate_summary(developer_commits=$developer_commits, report_date=$report_date)
     └─ check_report_quality(report_content=$markdown_content,
                             expected_developers=$active_developers,
                             expected_commits=$report.total_commits)
     └─ 未通过时重试；耗尽后 fallback_action -> use_fallback_report

  4. deliver_report (output_group)
     ├─ send_to_feishu_group(markdown_content=$markdown_content)
     └─ write_to_feishu_doc(markdown_content=$markdown_content, report_date=$report_date)

Done -> 汇总输出状态 + 写入 Trace
```

声明式执行引擎新增了 4 个关键能力：
- **上下文解析**：`EngineContext` 支持 `$path.to.value` 从运行上下文中取值
- **步骤钩子**：`after_step` 可把 Tool 结果同步回报告对象或做聚合判定
- **输出组**：多个输出通道可并行执行，再统一计算 success / partial / failed
- **可插拔内核**：`AgentEngine` 支持外部注册新的 step executor、hook 和 fallback action

每一步执行通过 `safe_execute` 包装：
- ⏱️ 自动计时（`duration_ms`）
- 🛡️ 异常捕获（不会崩溃，返回 `ToolResult(success=False)`）
- 📊 记录到 `AgentTrace`（可回放调试）

---

## 5. 质量门（Quality Gate）

`check_report_quality` 执行以下检查（不消耗额外 token）：

| 检查项 | 规则 | 扣分 |
|---|---|---|
| 内容长度 | ≥ 100 字符 | -2 |
| 标题 | 包含"日报"或"Daily" | -2 |
| 活跃开发者章节 | `###` 数量应覆盖全部活跃开发者 | -2 |
| 统计行存在 | 包含开发者级 `> 📈 X commits` 统计行 | -2 |
| commit 一致性 | 统计行汇总值应等于预期 commit 总数 | -2 |

满分 10，合格线 6（可在 `agent.json` 中调整 `required_quality_score`）。

不合格时 Agent 自动重新调用 `generate_summary`，最多重试 2 次。

---

## 6. 护栏参数（agent.json）

```json
{
  "guardrails": {
    "max_steps": 12,              // 当前已生效：超过上限会中止执行
    "max_retries": 2,             // 当前已生效：摘要生成最大重试次数
    "token_budget": 20000,        // 预留字段：未来可做预算控制
    "required_quality_score": 6,  // 当前已生效：质量门最低分
    "blocked_tools": []           // 当前已生效：命中的工具会被执行前拦截
  }
}
```

---

## 7. 执行追踪（Trace）

每次运行生成结构化 Trace：

```
[1] fetch_developers    ✅ (0ms)
[2] collect_commits     ✅ (1ms)
[3] generate_summary    ✅ (0ms)
[4] check_report_quality ✅ (0ms) — 质量评分: 10/10
[5] send_to_feishu_group ✅ (0ms)
[6] write_to_feishu_doc  ✅ (0ms)

🏁 Agent 执行完成 | 步骤: 6 | 耗时: 0.01s | 状态: success
```

---

## 8. 多模型兼容

LLM 层基于 OpenAI SDK 的 `base_url` 机制，内置 10 家 Provider 预设：

```python
_PROVIDER_PRESETS = {
    "deepseek":  {"base_url": "https://api.deepseek.com/v1",        "model": "deepseek-chat"},
    "zhipu":     {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "qwen":      {"base_url": "https://dashscope.aliyuncs.com/...", "model": "qwen-plus"},
    "moonshot":  {"base_url": "https://api.moonshot.cn/v1",         "model": "moonshot-v1-8k"},
    "ollama":    {"base_url": "http://localhost:11434/v1",          "model": "qwen2.5:7b"},
    ...
}
```

用户只需 `LLM_PROVIDER=deepseek` + `LLM_API_KEY=xxx`，其余自动配好。

---

## 9. 稳定性机制

| 机制 | 实现 |
|---|---|
| 单实例 PID 锁 | 启动前校验进程存活，防止重复调度 |
| Cron 解析兜底 | 非法表达式自动回退 `0 9 * * 1-5` |
| 启动补执行 | 检测当天是否已执行，过了时间点自动补 |
| GitLab 分页 | 全量翻页采集，避免活跃用户数据截断 |
| GitLab 局部失败可见化 | 单个开发者采集异常会标记 `partial`，不再静默吞掉 |
| 执行历史原子写 | tmp + replace，降低 JSON 损坏风险 |
| 按 run_id 更新 | 避免重试场景下历史记录错位 |
| 输出通道三态 | 成功 / 失败 / 跳过（未配置）；无任何通道时显式失败 |
| LLM 降级 | 生成失败自动使用模板，不中断推送 |
| 质量自检 | 不合格自动重做，最多 2 次 |

---

## 10. 扩展方式

**新增能力 = 新增一个 Tool 文件**，无需修改 Agent Core：

```python
# agent/tools/send_email.py
class SendEmailTool(BaseTool):
    name = "send_email"
    description = "通过邮件发送日报"
    ...
```

注册到 `agent_core.py` 的 `_build_tool_registry` 即可。

适合未来扩展：周报生成、代码评审摘要、告警通知等。

---

## 11. 架构思考：40 人 → 400 人会遇到什么瓶颈？

### 瓶颈一：GitLab API 采集速度

当前逐人串行采集，40 人约 30 秒。400 人线性膨胀到 5 分钟以上，且触发 Rate Limit。

**解法**：改为按 Group/Project 维度批量拉取 Events API，按作者归组。并发度受限时引入令牌桶排队。让 AI 根据历史采集耗时自动调整并发窗口与回退策略。

### 瓶颈二：LLM 上下文溢出

400 人的 commit 原始数据可能超过 50K tokens，超出上下文窗口，摘要质量下降。

**解法**：分层摘要（Map-Reduce）——AI 先按团队生成子摘要，再汇总全局日报。天然支持"按团队推送不同版本"。质量门拆为子摘要校验 + 全局一致性校验。

### 瓶颈三：输出通道扇出

400 人意味着 10+ 团队，各有飞书群和关注仓库。单一推送变成 N 路扇出。

**解法**：飞书表格加一列"所属团队"，execution_plan 按 team_tag 路由输出通道，Agent 自动按团队拆分推送。

### 瓶颈四：运行可观测性

400 人时需要知道哪个团队采集慢、哪个子摘要质量低、哪个通道失败。

**解法**：当前 AgentTrace 已记录每步 tool/duration/success，扩展为结构化指标推送到监控系统即可。AI 基于历史 Trace 自动识别"连续 N 天超时的仓库"并建议优化。

### 总结

核心策略是**分治**：采集按仓库并行、摘要按团队分层、推送按通道扇出。AI 的角色从"生成摘要"扩展到"自适应调参 + 异常归因 + 质量分层校验"。当前 execution_plan + hook 机制天然支持拆分为子流程，不需要推倒重来。
