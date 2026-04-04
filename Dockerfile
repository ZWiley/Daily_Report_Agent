# ============================================================
#  Astribot Daily Report Agent — Production Dockerfile
# ============================================================
#  多阶段构建 | 非root运行 | 安全加固 | ~120MB 最终镜像
#
#  构建:  docker build -t astribot-daily-agent:latest .
#  运行:  docker-compose up -d
# ============================================================

# ==================== Stage 1: 依赖安装 ====================
FROM python:3.12-slim AS builder

WORKDIR /build

# 只复制依赖清单（利用 Docker 层缓存，代码变更不会重装依赖）
COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ==================== Stage 2: 生产镜像 ====================
FROM python:3.12-slim AS production

# --- 元数据 ---
LABEL maintainer="Astribot Engineering <eng@astribot.com>"
LABEL description="Astribot Daily Report Automation Agent"
LABEL version="2.0.0"

# --- 系统环境 ---
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    # 默认调度：每天早上9点（周一至周五）
    SCHEDULE_CRON="0 9 * * 1-5" \
    # 默认生产模式（如需演示可显式设为 true）
    GITLAB_USE_MOCK=false

# 安装 tzdata 设置时区 + tini 做 PID 1 信号代理
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini tzdata && \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- 非 root 用户（安全加固）---
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# 从 builder 复制已安装的 Python 依赖
COPY --from=builder /install /usr/local

# 复制应用代码
COPY agent/ agent/
COPY pyproject.toml .

# 创建运行时目录并设置权限
RUN mkdir -p /app/data /app/logs /app/output /app/output/feishu_docs && \
    chown -R agent:agent /app

# --- 持久化卷 ---
VOLUME ["/app/data", "/app/logs", "/app/output"]

# --- 健康检查 ---
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from pathlib import Path; import os; p=Path('/app/data/scheduler.pid'); exit(0 if p.exists() and os.path.exists(f'/proc/{p.read_text().strip()}') else 1)" \
    || exit 1

# 切换到非 root 用户
USER agent

# 使用 tini 作为 PID 1（正确转发信号，避免僵尸进程）
ENTRYPOINT ["tini", "--"]

# 默认启动定时调度器
CMD ["python", "-m", "agent.main", "schedule"]
