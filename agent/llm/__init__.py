"""
LLM 摘要模块
=============
- Summarizer: 使用大语言模型对 commit 记录进行智能摘要
"""

from agent.llm.summarizer import LLMSummarizer, MockLLMSummarizer

__all__ = ["LLMSummarizer", "MockLLMSummarizer"]
