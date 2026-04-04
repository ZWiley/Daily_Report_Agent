# 🤖 Astribot 日报 Agent — 使用指南

> **读者**：产品 / 团队管理者 / 运维同学  
> **目标**：从零开始，把日报 Agent 跑起来，每天自动收到团队日报

---

## 📖 它能帮你做什么？

**你只需要维护一张飞书表格，Agent 自动帮你生成每日团队日报。**

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  📥 你的输入（唯一需要你做的事）                    │
│                                                     │
│  飞书多维表格 ── 一张「开发者名单」                 │
│  ┌──────────────┬────────────────┐                  │
│  │ 开发者姓名   │ GitLab 用户名  │  ← 只需两列     │
│  ├──────────────┼────────────────┤                  │
│  │ 张伟         │ zhang.wei      │                  │
│  │ 李娜         │ li.na          │                  │
│  │ ...          │ ...            │                  │
│  └──────────────┴────────────────┘                  │
│                                                     │
│  加人 = 加一行 · 减人 = 删一行 · 不用重启           │
│  仓库名从 GitLab 自动获取，不需要手动填             │
│                                                     │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼  Agent 每天 9:00 自动执行
┌─────────────────────────────────────────────────────┐
│                                                     │
│  🤖 Agent 自动完成 6 步                             │
│                                                     │
│  [1] 读飞书表格 → 拿到开发者列表                    │
│  [2] 采集 GitLab → 拉每个人过去 24h 的 commit       │
│  [3] AI 生成日报                                    │
│  [4] 质量自检 → 不合格自动重做（最多 2 次）         │
│  [5] 推送飞书群                                     │
│  [6] 归档飞书文档（可选）                           │
│                                                     │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│                                                     │
│  📤 你收到的输出 — 飞书群日报卡片                   │
│                                                     │
│  📊 Astribot 团队日报 | 2026-04-04                  │
│  > 今日 8 位工程师活跃，24 次提交，涉及 9 个仓库   │
│                                                     │
│  👤 张伟                                             │
│    motor-control — 步态算法优化，过渡更平滑          │
│    robot-firmware — 修复 IMU 陀螺仪零偏校准          │
│    📈 4 commits (2 repos) | +778 / -111 lines       │
│                                                     │
│  👤 李娜                                             │
│    ai-inference — 实现本地 RAG 检索                  │
│    vision-perception — 更新 TensorRT 到 10.0         │
│    📈 4 commits (2 repos) | +779 / -66 lines        │
│                                                     │
│  ...                                                │
│  ✅ 整体状态良好，各仓库进展顺利。                  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## 🛒 你需要准备什么？

一共 4 样东西。其中 ② 是**核心输入**，其他是一次性配置。

### ① 飞书应用（一次性，5 分钟）

| 你要做的 | 结果 |
|---|---|
| [飞书开放平台](https://open.feishu.cn) → 创建企业自建应用 | 拿到 `App ID` 和 `App Secret` |
| 开通权限：`bitable:record:read`、`docx:document:write`、`drive:file:write` | 应用能读表格、写文档 |
| 在「可用范围」中添加包含表格的群/用户 | 应用能访问到你的表格 |

### ② 飞书多维表格 — 开发者名单（⭐ 核心输入）

在飞书中新建一个**多维表格**，**只需要两列**：

| 字段名 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `开发者姓名` | ✅ | 中文姓名 | 张伟 |
| `GitLab 用户名` | ✅ | GitLab 登录名 | zhang.wei |
| `负责的组件` | 可选 | 备注标签（仓库名从 GitLab 自动读取） | 运动控制 |
| `邮箱` | 可选 | 邮箱 | zhang.wei@astribot.com |

从浏览器地址栏拿到两个值：

```
https://astribot.feishu.cn/base/bascnXXXXXXXXXX?table=tblXXXXXXXXXX
                                   ↑ App Token           ↑ Table ID
```

> ⚠️ 字段名必须完全一致（含空格）。`GitLab 用户名` 中间有空格。

### ③ 飞书群机器人（推荐）

**群设置** → **群机器人** → **自定义机器人** → 拿到 Webhook URL

> 至少要有一个输出通道：
> - 最常见：配置飞书群机器人（`FEISHU_WEBHOOK_URL`）
> - 或者：配置飞书文档归档（`FEISHU_DOC_ID` 或 `FEISHU_DOC_FOLDER_TOKEN`）
>
> 如果一个都不配，Agent 会明确报错失败，避免“日报其实没人收到”的假成功。

### ④ GitLab Token（必须）

**Settings** → **Access Tokens** → 勾选 `read_api`

---

## 🚀 部署方式

### 方式一：Docker 一键部署（推荐）

```bash
bash deploy/deploy.sh
```

脚本会交互式引导你填入所有配置，自动构建 + 启动。

### 方式二：手动 Docker

```bash
cp .env.example .env && vim .env
docker compose build && docker compose up -d
docker logs -f astribot-daily-agent
```

### 方式三：直接运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && vim .env
python3 -m agent.main schedule
```

---

## 🎮 日常操作

| 操作 | Docker | 直接运行 |
|---|---|---|
| **立即执行一次** | `make run` | `python3 -m agent.main run` |
| **演示（不连外部服务）** | `make demo` | `python3 -m agent.main demo` |
| **查看状态** | `make status` | `python3 -m agent.main status` |
| **查看历史** | `make history` | `python3 -m agent.main history` |
| **查看日志** | `make logs` | `logs/` 目录 |
| **重启** | `make restart` | 停掉后重新 `schedule` |
| **更新部署** | `make update` | 拉代码后重启 |

---

## 🔧 AI 模型选择

Agent 兼容所有 OpenAI 接口的模型。最简方式——只填 2 行：

```bash
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-你的key
```

| Provider | 价格 | 说明 |
|---|---|---|
| `deepseek` | 🔥 ≈ ¥0.001/次 | 性价比最高 |
| `zhipu` | 免费额度 | 适合试用 |
| `qwen` | ≈ ¥0.004/次 | 通义千问 |
| `moonshot` | ≈ ¥0.012/次 | Kimi |
| `doubao` | ≈ ¥0.001/次 | 字节豆包 |
| `ollama` | 完全免费 | 本地部署 |
| `openai` | ≈ ¥0.04/次 | 效果最好但最贵 |

省 token：`LLM_MAX_TOKENS=2048`（默认 4096，日报通常 1500 就够）

已有 `OPENAI_API_KEY` 不用改名，自动兼容。

---

## 🧪 首次验证

```bash
# 第 1 步：零配置演示
python3 -m agent.main demo

# 第 2 步：真实执行
python3 -m agent.main run

# 第 3 步：确认调度正常
python3 -m agent.main status
```

Demo 输出示例：

```
🤖 Daily Report Agent 启动 (Harness v2)
📅 日期: 2026-04-04
🛠️  工具: fetch_developers, collect_commits, generate_summary, ...

[1] fetch_developers    ✅ (0ms)
[2] collect_commits     ✅ (1ms)
[3] generate_summary    ✅ (0ms)
[4] check_report_quality ✅ — 质量评分: 10/10 ✅ 通过
[5] send_to_feishu_group ✅ (0ms)
[6] write_to_feishu_doc  ✅ (0ms)

🏁 Agent 执行完成 | 步骤: 6 | 耗时: 0.01s | 状态: success
```

---

## 👥 人员变动

回到飞书多维表格直接改：

| 场景 | 操作 | 要重启吗 |
|---|---|---|
| 新人入职 | 加一行 | ❌ |
| 有人离职 | 删一行 | ❌ |
| 换 GitLab 账号 | 改用户名 | ❌ |
| 临时不跟踪 | 清空 GitLab 用户名 | ❌ |

Agent 每次执行都重新读取表格，改完自动生效。

---

## ❓ 常见问题

### 飞书群没收到日报？

1. `make status` 看调度器是否在运行
2. `make history` 看最近执行结果
3. `make logs` 看详细错误
4. 常见原因：Webhook 过期、飞书应用权限不够、GitLab Token 过期

### 某个开发者提交数为 0？

- 确认表格中 `GitLab 用户名` 与 GitLab 上的 username 完全一致
- 确认 GitLab Token 有权访问该开发者参与的项目

### 想改发送时间？

改 `.env` 中的 `SCHEDULE_CRON`，重启服务：

```bash
SCHEDULE_CRON=0 18 * * 1-5    # 改成每天下午 6 点
```

### 日报质量不好怎么办？

Agent 内置质量自检，不合格会自动重新生成。如需调整标准，修改 `agent/agent.json`：

```json
"required_quality_score": 8    // 提高到 8 分（默认 6）
```

### 可以同时启动多个 Agent 吗？

不会。有 PID 锁保护，第二个实例会被拒绝。

### 服务器重启后会恢复吗？

- Docker 部署：会（`restart: unless-stopped`）
- systemd / launchd：会（已配置开机自启）
- 终端直接运行：不会，需手动重启

### Cron 表达式填错了？

不会崩溃，自动回退 `0 9 * * 1-5` 并打印警告。

---

## 🆘 遇到问题

1. 看日志：`make logs`
2. 看本文档「常见问题」
3. 联系开发团队，附上 `make status` + `make history` + 最近日志
