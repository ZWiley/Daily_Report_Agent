"""
配置加载测试
============
验证 README 中“复制 .env 后直接运行”的主路径确实可用。
"""

from __future__ import annotations

from agent.config import AgentConfig


def test_nested_settings_can_load_from_dotenv(tmp_path, monkeypatch) -> None:
    """嵌套的 Feishu/GitLab/LLM 配置都应能直接从 .env 读取。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID=cli_test_app",
                "FEISHU_APP_SECRET=secret_test",
                "FEISHU_BITABLE_APP_TOKEN=bascn_test",
                "FEISHU_BITABLE_TABLE_ID=tbl_test",
                "GITLAB_PRIVATE_TOKEN=glpat_test",
                "LLM_PROVIDER=deepseek",
                "LLM_API_KEY=sk_test",
                "REPORT_HOURS_LOOKBACK=48",
                "SCHEDULE_CRON=0 18 * * 1-5",
                "LOG_LEVEL=DEBUG",
            ]
        ),
        encoding="utf-8",
    )

    for name in [
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_BITABLE_APP_TOKEN",
        "FEISHU_BITABLE_TABLE_ID",
        "GITLAB_PRIVATE_TOKEN",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "REPORT_HOURS_LOOKBACK",
        "SCHEDULE_CRON",
        "LOG_LEVEL",
    ]:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.chdir(tmp_path)
    config = AgentConfig()

    assert config.feishu.app_id == "cli_test_app"
    assert config.feishu.app_secret == "secret_test"
    assert config.feishu.bitable_app_token == "bascn_test"
    assert config.feishu.bitable_table_id == "tbl_test"
    assert config.gitlab.private_token == "glpat_test"
    assert config.llm.provider == "deepseek"
    assert config.llm.api_key == "sk_test"
    assert config.llm.base_url == "https://api.deepseek.com/v1"
    assert config.llm.model == "deepseek-chat"
    assert config.report_hours_lookback == 48
    assert config.schedule_cron == "0 18 * * 1-5"
    assert config.log_level == "DEBUG"
