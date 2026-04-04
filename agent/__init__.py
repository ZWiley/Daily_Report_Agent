"""
Astribot Daily Report Agent (Harness Architecture)
===================================================
Agent = Model + Harness

架构：
    agent.json        → Agent 声明（工具/护栏/调度）
    agent_core.py     → Agent Loop（工具调用/自验证/容错）
    tools/            → 标准化工具层（7 个 Tool）
    pipeline.py       → 兼容旧版 Pipeline 入口

    feishu/           → 飞书 SDK
    gitlab/           → GitLab SDK
    llm/              → LLM SDK
"""

__version__ = "2.0.0"
