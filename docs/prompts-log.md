# 🗣️ Prompts 记录：我是怎么跟 AI 协作实现这个系统的

> 工具：CodeBuddy（IDE 内置 AI 编程助手）  
> 时间：2026-04-04 19:14 ~ 22:42，共约 3.5 小时  
> 模式：对话式迭代，人负责方向判断，AI 负责执行和验证

---

## 总体策略

我没有一次性把所有需求甩给 AI 让它"从零生成整个项目"。而是按**分层递进**的方式推进：

1. 先让 AI 搭骨架（数据模型、工具接口、配置管理）
2. 再填血肉（各 SDK 实现、Mock 数据、CLI 入口）
3. 然后加护栏（质量门、容错降级、执行追踪）
4. 最后做内核改造（声明式 execution plan、engine 模块化）
5. 收尾时做减法（合并过度拆分的模块，压缩不必要的抽象）

每一轮我只给方向，让 AI 自己规划任务、写代码、跑测试、修 bug，我来做"要不要继续"的判断。

---

## Round 1：项目初始化与骨架搭建

### 我的 Prompt

> 做一个团队日报自动化 Agent。读飞书多维表格拿开发者名单，调 GitLab API 拿 commit，用 LLM 生成 Markdown 日报，推送到飞书群和飞书文档。
>
> 要求：
> - 标准化 Tool 接口（BaseTool + ToolResult）
> - 支持 Mock 数据，demo 模式零配置可跑
> - 配置用 Pydantic Settings，支持 .env
> - 要有质量自检，日报不合格自动重做

### AI 做了什么

- 设计了 `Developer` / `CommitInfo` / `DeveloperCommits` / `DailyReport` 数据模型
- 实现了 `BaseTool` 抽象类 + `ToolResult` 统一返回 + `ToolRegistry` 注册表
- 搭了 7 个 Tool 的框架（fetch_developers / collect_commits / generate_summary / check_quality / send_feishu_group / write_feishu_doc / query_history）
- 每个涉及外部 API 的 Tool 都内置了 Mock 分支
- 写了 `AgentConfig` + `FeishuConfig` + `GitLabConfig` + `LLMConfig`
- 实现了 10 家 LLM Provider 预设 + OPENAI_ 前缀向后兼容
- 搭了 CLI 入口（run / schedule / demo / history / status）

### 我的判断

骨架 OK，工具接口统一，Mock 全覆盖。继续。

---

## Round 2：SDK 层实现

### 我的 Prompt

> 继续把 SDK 层实现了，飞书的 bitable / messenger / docwriter，GitLab 的 collector，LLM 的 summarizer。

### AI 做了什么

- `feishu/bitable.py`：飞书多维表格 API + Token 管理 + MockBitableReader
- `feishu/messenger.py`：飞书群 Webhook 推送 + MockFeishuMessenger
- `feishu/docwriter.py`：飞书文档追加/新建两种模式 + MockFeishuDocWriter
- `gitlab/collector.py`：GitLab commit 分页采集 + 按开发者归组 + 局部失败处理 + MockGitLabCollector
- `llm/summarizer.py`：OpenAI 兼容接口 + Prompt 工程 + MockLLMSummarizer

### 我的判断

SDK 实现合理，Mock 数据足够真实（8 个开发者、9 个仓库、不同代码量）。继续。

---

## Round 3：Agent Core 与主链路

### 我的 Prompt

> 现在把 Agent Core 写了，把这些 Tool 串起来。

### AI 做了什么

- 实现了 `DailyReportAgent`，按固定顺序调用 7 个 Tool
- 加了 `agent.json` 声明工具列表和护栏参数
- 实现了质量门循环（generate → check → 不合格重做 → 最多 2 次）
- 实现了 LLM 失败降级模板
- 实现了 `AgentTrace` 执行追踪
- 写了第一批测试

### 我的判断

主链路跑通了，但 Agent Core 是硬编码流程，`agent.json` 里的 guardrails 大部分是摆设。需要改造。

---

## Round 4：深改 Agent Core — 声明式执行引擎

### 我的 Prompt

> 继续深入改造 Agent Core

### AI 做了什么

- 给 `agent.json` 加了 `execution_plan`，把步骤声明从代码移到配置
- 重构 `DailyReportAgent` 为声明式执行引擎，支持 3 类步骤：`tool` / `summary_loop` / `output_group`
- 引入 `EngineContext`，步骤参数支持 `$path.to.value` 从上下文取值
- 加了 step hook 机制（`after_step`）
- 启用了之前"名义存在但实际没执行"的 `max_steps` 和 `blocked_tools` 护栏
- 补了 3 个回归测试（max_steps 超限、blocked_tools 拦截、fallback 降级）

### 我的判断

从"写死流程"变成了"配置驱动"，不错。但代码都堆在一个文件里，继续拆。

---

## Round 5：拆模块 — 可扩展 engine 内核

### 我的 Prompt

> 做成真正可扩展的 engine 内核

### AI 做了什么

- 拆出 `agent/engine/` 包，包含 5 个模块：
  - `spec.py` — execution_plan 解析
  - `context.py` — 运行时上下文
  - `runtime.py` — AgentEngine 运行时
  - `executors.py` — 内置 step executor
  - `hooks.py` — 日报默认 hooks/fallback
- `agent_core.py` 收敛为装配层
- 暴露了 `register_executor` / `register_hook` / `register_fallback` 扩展接口
- 新增 `test_engine.py` 测试内核扩展能力

### 我的判断

拆得太碎了，5 个文件跳来跳去读起来累。当前只有日报一个场景，没必要为"未来可能"做这么多抽象。

---

## Round 6：减法重构 — 越简单越好

### 我的 Prompt

> 不要过度设计，越简单越好

### AI 理解后的回应

> 保留声明式 execution_plan，保留一个薄的执行器，只服务当前日报场景。不继续加 when/if/else、分支路由、子流程嵌套、planner。

### 我的 Prompt

> 是的

### AI 做了什么

- 把 `executors.py` 和 `hooks.py` 合并回 `runtime.py`，engine 从 5 个文件收成 3 个
- 内置 step executor 改为开箱即用（不需要手动注册）
- 删掉装配层上多余的转发接口（`register_executor` 等）
- 收窄 `__init__.py` 的公共导出面
- 更新文档，去掉"可扩展引擎"的说法，改为"轻量 engine 内核"

### 我的判断

这才对。代码更集中，模块更少，读起来更容易。够用就行。

---

## Round 7：需求对齐 + 架构思考

### 我的 Prompt

> （贴了完整需求目标和交付要求）

### AI 做了什么

- 全面梳理项目状态，确认 4 个功能点全部覆盖
- 跑了一遍 `demo` 验证零配置可运行
- 在 `docs/architecture.md` 补写了"40 人 → 400 人扩展瓶颈"章节（434 字）
  - 瓶颈一：GitLab API 采集速度 → 按仓库批量拉取 + 令牌桶
  - 瓶颈二：LLM 上下文溢出 → Map-Reduce 分层摘要
  - 瓶颈三：输出通道扇出 → 按团队路由
  - 瓶颈四：运行可观测性 → Trace 结构化指标

---

## Round 8：整理提交

### 我的 Prompt

> 整理下代码，准备提交 git

### AI 做了什么

- 检查 `.gitignore` 覆盖（output / data / __pycache__ / .DS_Store / .env）
- 确认没有编译缓存或运行时产物混入
- 最终全量测试 35 passed
- `git add -A` + `git commit`，57 文件，7324 行

---

## 关键决策节点

| 时间 | 我的判断 | 效果 |
|---|---|---|
| Round 3 后 | "agent.json 里的 guardrails 是摆设，需要改造" | 推动了声明式执行引擎改造 |
| Round 5 后 | "拆得太碎了，5 个文件跳来跳去" | 推动了减法重构 |
| Round 6 | "不要过度设计，越简单越好" | 阻止了往"通用 workflow 引擎"方向走 |
| Round 7 | 贴完整需求，让 AI 自查覆盖度 | 补齐了架构思考文档 |

---

## 协作模式总结

### 我负责的

- **方向判断**：什么时候该加、什么时候该减、什么时候该停
- **质量把关**：每轮改完看代码结构是否合理、抽象是否过度
- **需求对齐**：确保最终产物覆盖交付要求

### AI 负责的

- **任务拆解**：每轮自动创建 todo list 并逐步执行
- **代码实现**：从数据模型到 SDK 到执行引擎到测试
- **自动验证**：每轮改完跑 `pytest` + `compileall`，有报错自己修
- **文档同步**：代码改了同步更新 README 和 architecture.md

### 核心原则

> **人做判断，AI 做执行。人说停就停，不让 AI 自己往下加能力。**

这不是"默写代码的能力"，是"知道什么时候该让 AI 做什么、什么时候该喊停"的能力。
