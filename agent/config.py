"""
配置管理模块
============
基于 Pydantic Settings 的类型安全配置，支持 .env 文件和环境变量。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FeishuConfig(BaseSettings):
    """飞书相关配置"""

    model_config = SettingsConfigDict(
        env_prefix="FEISHU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_id: str = Field(default="", description="飞书应用 App ID")
    app_secret: str = Field(default="", description="飞书应用 App Secret")
    bitable_app_token: str = Field(default="", description="多维表格 App Token")
    bitable_table_id: str = Field(default="", description="数据表 Table ID")
    webhook_url: str = Field(default="", description="飞书群机器人 Webhook URL")

    # 飞书文档写入配置
    doc_id: str = Field(default="", description="日报归档文档 ID（追加模式）")
    doc_folder_token: str = Field(default="", description="每日文档存放的文件夹 Token（新建模式）")


class GitLabConfig(BaseSettings):
    """GitLab 相关配置"""

    model_config = SettingsConfigDict(
        env_prefix="GITLAB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = Field(default="https://gitlab.astribot.com", description="GitLab 实例地址")
    private_token: str = Field(default="", description="GitLab Private Token")
    use_mock: bool = Field(default=False, description="是否使用 Mock 数据（开发模式）")


class LLMConfig(BaseSettings):
    """LLM 相关配置（兼容 OpenAI 及所有 OpenAI 兼容接口的国产/开源模型）"""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(default="", description="模型 API Key")
    base_url: str = Field(default="https://api.openai.com/v1", description="API Base URL")
    model: str = Field(default="gpt-4o", description="模型名称")

    # 省 token 策略
    max_tokens: int = Field(default=4096, description="最大输出 token 数")
    temperature: float = Field(default=0.3, description="生成温度（0-1，越低越稳定）")

    # 模型预设（可选 —— 快捷方式，覆盖上面的字段）
    provider: str = Field(
        default="",
        description=(
            "模型供应商预设（可选）。填写后自动设置 base_url/model 默认值。"
            "支持: openai / deepseek / zhipu / qwen / moonshot / doubao / baichuan / minimax / yi / ollama"
        ),
    )


# ---------- 向后兼容：同时读取 OPENAI_ 前缀的环境变量 ----------
# 许多用户已有 OPENAI_API_KEY 等环境变量，需要无缝兼容

class _LegacyOpenAIConfig(BaseSettings):
    """读取 OPENAI_ 前缀的旧配置（仅做向后兼容）"""

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(default="")
    base_url: str = Field(default="")
    model: str = Field(default="")


# ---------- 模型供应商预设表 ----------

_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-4k",
    },
    "baichuan": {
        "base_url": "https://api.baichuan-ai.com/v1",
        "model": "Baichuan4",
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "model": "abab6.5s-chat",
    },
    "yi": {
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model": "yi-large",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
    },
}


def _resolve_llm_config() -> LLMConfig:
    """解析最终 LLM 配置：LLM_ 前缀优先 > OPENAI_ 兼容 > provider 预设 > 默认值。"""
    cfg = LLMConfig()
    legacy = _LegacyOpenAIConfig()

    # 向后兼容：如果新 LLM_ 变量未设置，回退到 OPENAI_ 变量
    if not cfg.api_key and legacy.api_key:
        cfg.api_key = legacy.api_key
    if cfg.base_url == "https://api.openai.com/v1" and legacy.base_url:
        cfg.base_url = legacy.base_url
    if cfg.model == "gpt-4o" and legacy.model:
        cfg.model = legacy.model

    # provider 预设：仅当对应字段还是默认值时才应用
    if cfg.provider:
        preset = _PROVIDER_PRESETS.get(cfg.provider.lower().strip(), {})
        if preset:
            if cfg.base_url == "https://api.openai.com/v1" and preset.get("base_url"):
                cfg.base_url = preset["base_url"]
            if cfg.model == "gpt-4o" and preset.get("model"):
                cfg.model = preset["model"]

    return cfg


class AgentConfig(BaseSettings):
    """Agent 全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 子配置
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig)
    llm: LLMConfig = Field(default_factory=_resolve_llm_config)

    # Agent 行为配置
    report_hours_lookback: int = Field(default=24, description="回溯小时数")
    schedule_cron: str = Field(default="0 9 * * 1-5", description="Cron 调度表达式")
    log_level: str = Field(default="INFO", description="日志级别")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level 必须是 {valid} 之一")
        return upper


@lru_cache(maxsize=1)
def get_config() -> AgentConfig:
    """获取全局配置单例（带缓存）"""
    return AgentConfig()
