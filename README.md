# 🤖 Astribot Daily Report Agent

> **声明式 Tool Harness + 质量门控制**  
> 一个面向交付的团队日报 Agent：读取飞书人员表、采集 GitLab 提交、用 AI 生成日报，并通过 execution plan 驱动推送到飞书。

---

## 它做什么

你维护一张飞书表格（开发者姓名 + GitLab 用户名），Agent 每天自动：

```
读取飞书表格 → 采集 GitLab commit → AI 生成日报 → 质量自检 → 推送飞书群
```

---

## 架构

```
Agent Core (agent_core.py)          ← 日报场景装配层：加载 spec、注册工具、组装 engine
  ├── agent.json                    ← Agent 声明：工具 / execution_plan / 护栏 / 运行约定
  ├── engine/                       ← 轻量 engine 内核
  │   ├── spec.py                   ← execution_plan / guardrails 解析
  │   ├── context.py                ← 运行时上下文与 $path 解析
  │   └── runtime.py                ← 内置 step 执行、trace 与日报默认 hook / fallback
  │
  ├── Tools (agent/tools/)          ← 7 个标准化工具
  │   ├── fetch_developers          ← 读飞书多维表格
  │   ├── collect_commits           ← 采集 GitLab 提交
  │   ├── generate_summary          ← LLM 生成日报
  │   ├── check_report_quality      ← 日报质量自评（不合格自动重做）
  │   ├── send_to_feishu_group      ← 飞书群推送
  │   ├── write_to_feishu_doc       ← 飞书文档归档
  │   └── query_history             ← 查询执行历史
  │
  ├── SDK Layer                     ← 底层服务封装
  │   ├── feishu/                   ← 飞书 API（表格/消息/文档）
  │   ├── gitlab/                   ← GitLab API（分页采集）
  │   └── llm/                      ← LLM API（10+ 模型兼容）
  │
  └── Infra
      ├── scheduler.py              ← 定时调度（Cron + 重试 + 补执行）
      ├── config.py                 ← 配置管理（.env + 环境变量）
      └── models.py                 ← 数据模型
```

---

## 快速开始

### 1. 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入飞书 / GitLab / LLM 配置
```

### 3. 运行

```bash
python3 -m agent.main demo                # 零配置演示（Mock 数据）
python3 -m agent.main run                 # 执行一次真实日报
python3 -m agent.main schedule            # 启动定时调度（每天 9:00）
python3 -m agent.main schedule --run-now  # 启动调度并立即执行一次
python3 -m agent.main status              # 查看调度器状态
python3 -m agent.main history             # 查看执行历史
```

### 4. Docker 部署

```bash
bash deploy/deploy.sh         # 一键部署（交互式引导配置）
# 或手动
docker compose build && docker compose up -d
```

---

## 配置说明

### 飞书

| 变量 | 必填 | 说明 |
|---|---|---|
| `FEISHU_APP_ID` | ✅ | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | ✅ | 飞书应用 Secret |
| `FEISHU_BITABLE_APP_TOKEN` | ✅ | 多维表格 App Token |
| `FEISHU_BITABLE_TABLE_ID` | ✅ | 数据表 Table ID |
| `FEISHU_WEBHOOK_URL` | 推荐 | 飞书群 Webhook |
| `FEISHU_DOC_ID` | 可选 | 追加模式文档 ID |
| `FEISHU_DOC_FOLDER_TOKEN` | 可选 | 新建模式文件夹 Token |

> 至少要配置一个输出通道：`FEISHU_WEBHOOK_URL` 或 `FEISHU_DOC_ID / FEISHU_DOC_FOLDER_TOKEN`。如果两个都不配，Agent 会明确失败，避免“生成了但没人收到”的假成功。

### GitLab

| 变量 | 必填 | 说明 |
|---|---|---|
| `GITLAB_URL` | ✅ | GitLab 地址 |
| `GITLAB_PRIVATE_TOKEN` | ✅ | GitLab Token（需 `read_api` 权限） |

### LLM（支持 10+ 模型）

**最简方式**——Provider 预设，只填 2 行：

```bash
LLM_PROVIDER=deepseek       # 或 zhipu / qwen / moonshot / ollama 等
LLM_API_KEY=sk-xxx
```

| Provider | 模型 | 价格参考 |
|---|---|---|
| `deepseek` | deepseek-chat | 🔥 ≈ ¥0.001/次 |
| `zhipu` | glm-4-flash | 免费额度大 |
| `qwen` | qwen-plus | ≈ ¥0.004/次 |
| `moonshot` | moonshot-v1-8k | ≈ ¥0.012/次 |
| `ollama` | qwen2.5:7b | 完全免费（本地） |
| `openai` | gpt-4o | ≈ ¥0.04/次 |

> 已有 `OPENAI_API_KEY` 环境变量无需改名，自动兼容。

### 调度

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SCHEDULE_CRON` | `0 9 * * 1-5` | 定时表达式 |
| `REPORT_HOURS_LOOKBACK` | `24` | 回溯小时数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

---

## 输入与输出

### 输入（飞书多维表格，只需两列）

| 开发者姓名 | GitLab 用户名 |
|---|---|
| 张伟 | zhang.wei |
| 李娜 | li.na |

加人删人直接改表格，不用重启。仓库名从 GitLab 自动获取。

### 输出（飞书群日报）

```
📊 Astribot 团队日报 | 2026-04-04
> 今日团队共 8 位工程师活跃，提交 24 次代码变更，涉及 9 个仓库。

👤 张伟
  motor-control — 步态算法优化，步态过渡更平滑
  robot-firmware — 修复 IMU 陀螺仪零偏校准异常
  📈 4 commits (2 repos) | +778 / -111 lines
```

---

## 目录结构

```
agent/
  agent.json                  # Agent 声明（工具 / execution_plan / 护栏 / 调度）
  agent_core.py               # 日报场景装配层
  engine/                     # 轻量 engine 内核
    __init__.py               # 内核导出入口
    spec.py                   # Agent Spec / execution_plan 解析
    context.py                # 运行时上下文与参数解析
    runtime.py                # 内置 step 执行、trace 与日报默认 hook / fallback
  main.py                     # CLI 入口
  scheduler.py                # 定时调度 + 执行历史
  config.py                   # 配置管理
  models.py                   # 数据模型
  pipeline.py                 # 兼容旧版 Pipeline
  tools/
    base.py                   # Tool 基类 + 注册表
    fetch_developers.py       # 读取飞书多维表格
    collect_commits.py        # GitLab 提交采集
    generate_summary.py       # LLM 日报生成
    check_quality.py          # 日报质量自评
    send_feishu_group.py      # 飞书群推送
    write_feishu_doc.py       # 飞书文档归档
    query_history.py          # 查询执行历史
  feishu/                     # 飞书 SDK
  gitlab/                     # GitLab SDK
  llm/                        # LLM SDK（多模型兼容）

deploy/                       # 部署脚本 + systemd / launchd 配置
docs/
  architecture.md             # 架构设计方案
  usage-guide.md              # 产品使用指南
tests/                        # 测试（35 cases）
```

---

## 测试

```bash
pip install pytest pytest-asyncio
python3 -m pytest tests -q        # 35 passed
```

---

## 文档

- [📖 产品使用指南](docs/usage-guide.md) — 零门槛，面向产品/管理者
- [🏗️ 架构设计方案](docs/architecture.md) — Harness 工程方案，面向开发者
- [🗣️ Prompts 记录](docs/prompts-log.md) — 我是怎么跟 AI 协作实现这个系统的

---

## License

MIT
