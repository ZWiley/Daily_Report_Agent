#!/usr/bin/env bash
# ============================================================
#  Astribot Daily Report Agent — 云服务器一键部署脚本
# ============================================================
#
#  使用方式（在云服务器上执行）:
#
#    方式 1: 本地代码已上传到服务器
#      cd /opt/astribot-daily-agent
#      bash deploy/deploy.sh
#
#    方式 2: 从 Git 拉取
#      bash <(curl -sL https://raw.githubusercontent.com/astribot/daily-agent/main/deploy/deploy.sh)
#
#  前置条件:
#    - 一台 Linux 云服务器（Ubuntu 20.04+ / CentOS 7+ / Debian 11+）
#    - root 或 sudo 权限
#    - 服务器能访问外网（飞书/GitLab/OpenAI API）
#
#  它会自动:
#    1. 检测并安装 Docker + Docker Compose
#    2. 创建项目目录和非 root 用户
#    3. 引导你配置 .env 环境变量
#    4. 构建 Docker 镜像
#    5. 启动定时调度服务
#    6. 验证服务健康状态
#
# ============================================================

set -euo pipefail

# ==================== 颜色输出 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
error()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }
info()   { echo -e "${BLUE}[i]${NC} $*"; }
header() { echo -e "\n${CYAN}${BOLD}═══ $* ═══${NC}\n"; }

# ==================== 配置 ====================
APP_NAME="astribot-daily-agent"
APP_DIR="/opt/${APP_NAME}"
APP_USER="astribot"
REPO_URL="${REPO_URL:-}"  # 可选：Git 仓库地址

# ==================== Banner ====================
echo -e "${CYAN}"
cat << 'BANNER'

   ╔══════════════════════════════════════════════╗
   ║                                              ║
   ║   🤖 Astribot Daily Agent — 一键部署脚本     ║
   ║                                              ║
   ║   Docker 容器化 · 定时调度 · 自动日报        ║
   ║                                              ║
   ╚══════════════════════════════════════════════╝

BANNER
echo -e "${NC}"

# ==================== Step 1: 环境检测 ====================
header "Step 1/6: 环境检测"

# 检测操作系统
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="${NAME:-Unknown}"
    OS_VERSION="${VERSION_ID:-Unknown}"
    log "操作系统: ${OS_NAME} ${OS_VERSION}"
else
    warn "无法检测操作系统，继续尝试..."
fi

# 检测架构
ARCH=$(uname -m)
log "系统架构: ${ARCH}"

# 检测内存
MEM_TOTAL=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo "unknown")
if [ "${MEM_TOTAL}" != "unknown" ]; then
    log "可用内存: ${MEM_TOTAL}MB"
    if [ "${MEM_TOTAL}" -lt 512 ]; then
        warn "内存较低 (<512MB)，建议至少 1GB"
    fi
fi

# ==================== Step 2: 安装 Docker ====================
header "Step 2/6: 检测/安装 Docker"

install_docker() {
    info "正在安装 Docker..."

    if command -v apt-get &>/dev/null; then
        # Debian/Ubuntu
        apt-get update -qq
        apt-get install -y -qq ca-certificates curl gnupg
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || true
        chmod a+r /etc/apt/keyrings/docker.gpg 2>/dev/null || true
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
        apt-get update -qq
        apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    elif command -v yum &>/dev/null; then
        # CentOS/RHEL
        yum install -y -q yum-utils
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        yum install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    else
        error "不支持的包管理器，请手动安装 Docker: https://docs.docker.com/engine/install/"
    fi

    systemctl enable docker
    systemctl start docker
    log "Docker 安装完成"
}

if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | grep -oP '\d+\.\d+\.\d+' || echo "unknown")
    log "Docker 已安装: v${DOCKER_VERSION}"
else
    install_docker
fi

# 检测 Docker Compose
if docker compose version &>/dev/null; then
    COMPOSE_VERSION=$(docker compose version --short 2>/dev/null || echo "unknown")
    log "Docker Compose 已安装: v${COMPOSE_VERSION}"
elif command -v docker-compose &>/dev/null; then
    log "Docker Compose (standalone) 已安装"
    # 创建别名
    alias docker_compose="docker-compose"
else
    warn "Docker Compose 未安装，正在安装..."
    apt-get install -y -qq docker-compose-plugin 2>/dev/null || \
    yum install -y -q docker-compose-plugin 2>/dev/null || \
    error "Docker Compose 安装失败，请手动安装"
fi

# 确认 Docker 运行中
if ! docker info &>/dev/null; then
    systemctl start docker
    sleep 2
fi
log "Docker 服务运行正常"

# ==================== Step 3: 准备项目 ====================
header "Step 3/6: 准备项目目录"

# 创建应用用户（如果不存在）
if ! id -u ${APP_USER} &>/dev/null; then
    useradd -r -m -d ${APP_DIR} -s /bin/bash ${APP_USER}
    usermod -aG docker ${APP_USER}
    log "已创建用户: ${APP_USER}"
else
    log "用户 ${APP_USER} 已存在"
fi

# 如果当前不在项目目录，需要拉取代码
if [ ! -f "${APP_DIR}/Dockerfile" ]; then
    if [ -f "$(pwd)/Dockerfile" ]; then
        # 当前目录就是项目目录
        if [ "$(pwd)" != "${APP_DIR}" ]; then
            info "复制项目到 ${APP_DIR}..."
            mkdir -p ${APP_DIR}
            cp -r . ${APP_DIR}/
        fi
    elif [ -n "${REPO_URL}" ]; then
        info "从 Git 拉取代码..."
        command -v git &>/dev/null || apt-get install -y -qq git 2>/dev/null || yum install -y -q git 2>/dev/null
        git clone "${REPO_URL}" ${APP_DIR}
    else
        error "未找到项目代码。请将代码上传到 ${APP_DIR} 或设置 REPO_URL 环境变量"
    fi
fi

cd ${APP_DIR}
log "项目目录: ${APP_DIR}"
log "文件列表: $(ls -1 Dockerfile docker-compose.yml requirements.txt 2>/dev/null | tr '\n' ' ')"

# ==================== Step 4: 配置环境变量 ====================
header "Step 4/6: 配置环境变量"

if [ -f .env ]; then
    warn ".env 文件已存在"
    read -p "  是否重新配置? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "保留现有 .env 配置"
        SKIP_ENV=true
    fi
fi

if [ "${SKIP_ENV:-false}" != "true" ]; then
    cp .env.example .env

    echo -e "\n${BOLD}请配置以下环境变量（直接回车跳过保持默认值）:${NC}\n"

    # 交互式配置
    configure_env() {
        local key=$1
        local desc=$2
        local default=$3
        local current

        current=$(grep "^${key}=" .env 2>/dev/null | cut -d'=' -f2- | sed 's/^[ \t]*//')

        read -p "  ${desc} [${default:-无默认值}]: " value
        value="${value:-${default}}"

        if [ -n "${value}" ]; then
            sed -i "s|^${key}=.*|${key}=${value}|" .env
        fi
    }

    echo -e "${CYAN}--- 飞书配置 ---${NC}"
    configure_env "FEISHU_APP_ID" "飞书 App ID" ""
    configure_env "FEISHU_APP_SECRET" "飞书 App Secret" ""
    configure_env "FEISHU_BITABLE_APP_TOKEN" "多维表格 Token" ""
    configure_env "FEISHU_BITABLE_TABLE_ID" "数据表 ID" ""
    configure_env "FEISHU_WEBHOOK_URL" "飞书群 Webhook URL" ""
    configure_env "FEISHU_DOC_ID" "日报归档文档 ID (可选)" ""
    configure_env "FEISHU_DOC_FOLDER_TOKEN" "每日文档文件夹 Token (可选)" ""

    echo -e "\n${CYAN}--- GitLab 配置 ---${NC}"
    configure_env "GITLAB_URL" "GitLab 地址" "https://gitlab.astribot.com"
    configure_env "GITLAB_PRIVATE_TOKEN" "GitLab Token" ""
    configure_env "GITLAB_USE_MOCK" "使用 Mock 数据 (true/false)" "false"

    echo -e "\n${CYAN}--- LLM 配置 ---${NC}"
    configure_env "OPENAI_API_KEY" "OpenAI API Key" ""
    configure_env "OPENAI_BASE_URL" "API Base URL" "https://api.openai.com/v1"
    configure_env "OPENAI_MODEL" "模型名称" "gpt-4o"

    echo -e "\n${CYAN}--- 调度配置 ---${NC}"
    configure_env "SCHEDULE_CRON" "Cron 表达式" "0 9 * * 1-5"
    configure_env "REPORT_HOURS_LOOKBACK" "回溯小时数" "24"

    echo ""
    log ".env 配置完成"

    # 安全：限制 .env 文件权限
    chmod 600 .env
    log ".env 文件权限设置为 600（仅 owner 可读写）"
fi

# ==================== Step 5: 构建镜像 & 启动服务 ====================
header "Step 5/6: 构建镜像 & 启动服务"

# 停止旧容器（如果存在）
if docker ps -a --format '{{.Names}}' | grep -q "^${APP_NAME}$"; then
    info "停止旧容器..."
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
fi

# 构建镜像
info "正在构建 Docker 镜像（首次可能需要 2-3 分钟）..."
if docker compose build --no-cache 2>&1; then
    log "Docker 镜像构建成功"
else
    # 降级尝试 docker-compose
    docker-compose build --no-cache 2>&1 || error "镜像构建失败"
    log "Docker 镜像构建成功 (docker-compose)"
fi

# 启动服务
info "启动定时调度服务..."
if docker compose up -d 2>&1; then
    log "服务启动成功"
else
    docker-compose up -d 2>&1 || error "服务启动失败"
    log "服务启动成功 (docker-compose)"
fi

# ==================== Step 6: 验证 ====================
header "Step 6/6: 验证部署"

# 等待容器启动
info "等待服务就绪..."
sleep 5

# 检查容器状态
CONTAINER_STATUS=$(docker inspect --format='{{.State.Status}}' ${APP_NAME} 2>/dev/null || echo "not_found")

if [ "${CONTAINER_STATUS}" = "running" ]; then
    log "容器状态: 运行中 ✅"
else
    error "容器状态异常: ${CONTAINER_STATUS}，请检查 docker logs ${APP_NAME}"
fi

# 检查健康状态
HEALTH=$(docker inspect --format='{{.State.Health.Status}}' ${APP_NAME} 2>/dev/null || echo "none")
if [ "${HEALTH}" = "healthy" ]; then
    log "健康检查: 通过 ✅"
elif [ "${HEALTH}" = "starting" ]; then
    info "健康检查: 启动中（30秒后自动完成）"
else
    info "健康检查: ${HEALTH}（服务可能仍在初始化）"
fi

# 显示容器信息
echo ""
docker ps --filter "name=${APP_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Size}}"

# ==================== 部署完成 ====================
echo ""
echo -e "${GREEN}${BOLD}"
cat << 'DONE'
  ╔══════════════════════════════════════════════╗
  ║                                              ║
  ║   ✅ 部署成功！日报 Agent 已开始运行         ║
  ║                                              ║
  ╚══════════════════════════════════════════════╝
DONE
echo -e "${NC}"

CRON_VAL=$(grep "^SCHEDULE_CRON" .env 2>/dev/null | cut -d'=' -f2- || echo "0 9 * * 1-5")
echo -e "  ${BOLD}调度计划${NC}: ${CRON_VAL}"
echo -e "  ${BOLD}日报时间${NC}: 每天上午 9:00（周一至周五）"
echo ""
echo -e "  ${BOLD}常用命令${NC}:"
echo -e "    查看日志:    ${CYAN}docker logs -f ${APP_NAME}${NC}"
echo -e "    查看状态:    ${CYAN}docker exec ${APP_NAME} python -m agent.main status${NC}"
echo -e "    查看历史:    ${CYAN}docker exec ${APP_NAME} python -m agent.main history${NC}"
echo -e "    手动执行:    ${CYAN}docker exec ${APP_NAME} python -m agent.main run${NC}"
echo -e "    立即 Demo:   ${CYAN}docker exec ${APP_NAME} python -m agent.main demo${NC}"
echo -e "    重启服务:    ${CYAN}docker compose restart${NC}"
echo -e "    停止服务:    ${CYAN}docker compose down${NC}"
echo -e "    更新部署:    ${CYAN}docker compose up -d --build${NC}"
echo ""
