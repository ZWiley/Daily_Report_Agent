"""
飞书文档写入模块测试
====================
"""

from __future__ import annotations

import pytest
from pathlib import Path

from agent.config import FeishuConfig
from agent.feishu.docwriter import MockFeishuDocWriter, FeishuDocWriter


@pytest.mark.asyncio
async def test_mock_doc_writer_creates_files(tmp_path: Path):
    """测试 Mock 文档写入器创建本地文件"""
    writer = MockFeishuDocWriter()
    # 覆盖输出目录为 tmp
    writer.output_dir = tmp_path / "feishu_docs"
    writer.archive_file = writer.output_dir / "daily_archive.md"

    content = "## 📊 测试日报\n\n> 这是一条测试日报\n\n### 🦿 运动控制 — 张伟\n\n- 优化算法"
    success = await writer.write_report(content, "2026-04-04")

    assert success is True

    # 验证归档文档
    assert writer.archive_file.exists()
    archive_text = writer.archive_file.read_text(encoding="utf-8")
    assert "2026-04-04" in archive_text
    assert "测试日报" in archive_text
    assert "Astribot 团队日报归档" in archive_text

    # 验证每日文档
    daily_file = writer.output_dir / "2026-04" / "2026-04-04.md"
    assert daily_file.exists()
    daily_text = daily_file.read_text(encoding="utf-8")
    assert "2026-04-04" in daily_text
    assert "测试日报" in daily_text


@pytest.mark.asyncio
async def test_mock_doc_writer_appends_multiple_days(tmp_path: Path):
    """测试多天日报追加到同一归档文档"""
    writer = MockFeishuDocWriter()
    writer.output_dir = tmp_path / "feishu_docs"
    writer.archive_file = writer.output_dir / "daily_archive.md"

    await writer.write_report("## Day 1 日报\n\n内容A", "2026-04-01")
    await writer.write_report("## Day 2 日报\n\n内容B", "2026-04-02")
    await writer.write_report("## Day 3 日报\n\n内容C", "2026-04-03")

    archive_text = writer.archive_file.read_text(encoding="utf-8")

    # 所有 3 天的内容都在同一个归档文件中
    assert "2026-04-01" in archive_text
    assert "2026-04-02" in archive_text
    assert "2026-04-03" in archive_text
    assert "内容A" in archive_text
    assert "内容B" in archive_text
    assert "内容C" in archive_text

    # 每天也各自有独立文档
    assert (writer.output_dir / "2026-04" / "2026-04-01.md").exists()
    assert (writer.output_dir / "2026-04" / "2026-04-02.md").exists()
    assert (writer.output_dir / "2026-04" / "2026-04-03.md").exists()


@pytest.mark.asyncio
async def test_mock_doc_writer_month_directory(tmp_path: Path):
    """测试跨月文档归入不同月份目录"""
    writer = MockFeishuDocWriter()
    writer.output_dir = tmp_path / "feishu_docs"
    writer.archive_file = writer.output_dir / "daily_archive.md"

    await writer.write_report("3月日报", "2026-03-31")
    await writer.write_report("4月日报", "2026-04-01")

    assert (writer.output_dir / "2026-03" / "2026-03-31.md").exists()
    assert (writer.output_dir / "2026-04" / "2026-04-01.md").exists()


def test_build_document_blocks():
    """测试 Markdown → 飞书文档 Block 转换"""
    blocks = FeishuDocWriter._build_document_blocks(
        "## 标题\n\n> 引用文本\n\n- 列表项1\n- 列表项2\n\n---\n\n普通文本",
        "2026-04-04",
        include_header=True,
    )

    # 包含分隔线 + 日期标题 + 内容块
    assert len(blocks) > 5

    # 第一个应该是分隔线（日期分隔区）
    assert blocks[0]["block_type"] == 14  # divider

    # 第二个应该是日期标题
    assert blocks[1]["block_type"] == 4  # heading2
    heading_text = blocks[1]["heading2"]["elements"][0]["text_run"]["content"]
    assert "2026-04-04" in heading_text

    # 验证各种 block 类型都有出现
    block_types = [b["block_type"] for b in blocks]
    assert 4 in block_types    # heading2 (## 标题)
    assert 12 in block_types   # quote (> 引用)
    assert 13 in block_types   # bullet (- 列表)
    assert 14 in block_types   # divider (---)
    assert 2 in block_types    # text (普通文本)


def test_build_document_blocks_no_header():
    """测试不包含日期分隔头的块构建（每日新建模式）"""
    blocks = FeishuDocWriter._build_document_blocks(
        "## 标题\n\n内容",
        "2026-04-04",
        include_header=False,
    )

    # 不应该以分隔线开头
    assert blocks[0]["block_type"] != 14 or len(blocks) <= 3
    # 第一个应该是 heading2
    assert blocks[0]["block_type"] == 4


@pytest.mark.asyncio
async def test_create_daily_document_returns_none_when_content_write_fails() -> None:
    """创建成功但正文写入失败时，不应误报为文档写入成功。"""

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self._responses = [
                FakeResponse({
                    "code": 0,
                    "data": {"document": {"document_id": "doc_test_123"}},
                }),
                FakeResponse({"code": 999, "msg": "write failed"}),
            ]

        async def post(self, *args, **kwargs):
            return self._responses.pop(0)

    async def fake_auth_headers() -> dict:
        return {}

    async def fake_get_root_block(doc_id: str, headers: dict) -> str:
        return "doc_test_123"

    writer = FeishuDocWriter(FeishuConfig(doc_folder_token="fld_test"))
    writer._client = FakeClient()  # type: ignore[assignment]
    writer._auth_headers = fake_auth_headers  # type: ignore[method-assign]
    writer._get_document_root_block = fake_get_root_block  # type: ignore[method-assign]

    doc_id = await writer.create_daily_document("## 测试日报", "2026-04-04")

    assert doc_id is None
