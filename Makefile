# ============================================================
#  Astribot Daily Agent — Makefile
# ============================================================
#  make build    — 构建 Docker 镜像
#  make up       — 启动服务（后台）
#  make down     — 停止服务
#  make logs     — 查看实时日志
#  make status   — 查看调度器状态
#  make history  — 查看执行历史
#  make run      — 手动执行一次日报
#  make demo     — Mock 数据演示
#  make deploy   — 一键部署到当前服务器
#  make test     — 运行测试
# ============================================================

.PHONY: build up down logs status history run demo deploy test clean shell restart update

IMAGE_NAME := astribot-daily-agent
CONTAINER_NAME := astribot-daily-agent

# --- Docker 操作 ---

build:  ## 构建 Docker 镜像
	docker compose build

up:  ## 启动定时调度服务（后台运行）
	docker compose up -d
	@echo ""
	@echo "✅ 服务已启动！查看日志: make logs"

down:  ## 停止服务
	docker compose down

restart:  ## 重启服务
	docker compose restart

update:  ## 更新部署（重新构建并启动）
	docker compose up -d --build
	@echo ""
	@echo "✅ 已更新部署"

logs:  ## 查看实时日志
	docker logs -f $(CONTAINER_NAME)

shell:  ## 进入容器终端
	docker exec -it $(CONTAINER_NAME) bash

# --- Agent 操作（在容器内执行）---

status:  ## 查看调度器状态
	docker exec $(CONTAINER_NAME) python -m agent.main status

history:  ## 查看执行历史
	docker exec $(CONTAINER_NAME) python -m agent.main history

run:  ## 手动执行一次日报（生产模式）
	docker exec $(CONTAINER_NAME) python -m agent.main run

demo:  ## Mock 数据演示
	docker exec $(CONTAINER_NAME) python -m agent.main demo

# --- 部署 ---

deploy:  ## 一键部署到当前服务器
	bash deploy/deploy.sh

# --- 开发 ---

test:  ## 运行测试（本地）
	python -m pytest tests/ -v

clean:  ## 清理构建缓存和容器
	docker compose down -v --rmi local
	docker system prune -f

# --- 帮助 ---

help:  ## 显示帮助
	@echo "可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
