"""
飞书模块测试
=============
"""

import pytest

from agent.feishu.bitable import MockBitableReader
from agent.feishu.messenger import MockFeishuMessenger


@pytest.mark.asyncio
async def test_mock_bitable_reader():
    """测试 Mock 飞书表格读取"""
    reader = MockBitableReader()
    developers = await reader.fetch_developers()

    assert len(developers) > 0
    for dev in developers:
        assert dev.name
        assert dev.gitlab_username
        assert isinstance(dev.component, str)  # 可选字段，可以为空


@pytest.mark.asyncio
async def test_mock_messenger():
    """测试 Mock 飞书推送"""
    messenger = MockFeishuMessenger()
    success = await messenger.send_webhook(
        "## 测试日报\n\n这是一条测试消息",
        title="测试",
    )
    assert success is True
