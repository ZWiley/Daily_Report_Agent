"""
飞书文档写入器
==============
将日报写入飞书文档（Docs），实现按天归档的持久化日报记录。

设计策略：
  - 使用「一个文档 = 一周/一月」的归档模式，每天的日报作为文档中的一个区块
  - 每次写入时：查找或创建目标文档 → 在文档末尾追加当天日报区块
  - 支持两种模式：
    1. 单文档追加模式：每天在同一个文档末尾追加（适合周报/月报汇总）
    2. 每日新建模式：每天创建一个独立文档到指定文件夹

飞书文档 API 参考：
  - 创建文档: https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/create
  - 创建块: https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block-children/create
  - 获取块: https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/list
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.config import FeishuConfig

logger = logging.getLogger(__name__)


class FeishuDocWriter:
    """
    飞书文档写入器
    ==============
    将日报内容写入飞书文档，按日期分块归档。
    """

    # 飞书开放平台 API 端点
    TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    DOC_CREATE_URL = "https://open.feishu.cn/open-apis/docx/v1/documents"
    DOC_BLOCKS_URL = "https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}/children"
    DOC_RAW_CONTENT_URL = "https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/raw_content"
    DOC_BLOCKS_LIST_URL = "https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks"
    FOLDER_CREATE_URL = "https://open.feishu.cn/open-apis/drive/v1/files/create_folder"

    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._tenant_token: str | None = None
        self._client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> FeishuDocWriter:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if not self._client.is_closed:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _get_tenant_token(self) -> str:
        """获取飞书 Tenant Access Token"""
        if self._tenant_token:
            return self._tenant_token

        resp = await self._client.post(
            self.TOKEN_URL,
            json={
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 Token 失败: {data.get('msg', 'unknown error')}")

        self._tenant_token = data["tenant_access_token"]
        return self._tenant_token

    async def _auth_headers(self) -> dict[str, str]:
        """构建鉴权请求头"""
        token = await self._get_tenant_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    # ========================================
    # 策略 1: 写入指定文档（追加模式）
    # ========================================

    async def append_to_document(
        self,
        markdown_content: str,
        report_date: str,
        document_id: str | None = None,
    ) -> bool:
        """
        将日报追加到飞书文档末尾（按日期分块）。

        如果 document_id 为空，使用配置中的 doc_id。
        文档中每天的日报以分隔线 + 日期标题分隔，形成时间线归档。

        Args:
            markdown_content: Markdown 格式的日报
            report_date: 日报日期 (YYYY-MM-DD)
            document_id: 目标文档 ID（可选，优先使用配置）

        Returns:
            是否写入成功
        """
        doc_id = document_id or self.config.doc_id
        if not doc_id:
            logger.warning("⚠️  未配置飞书文档 ID (FEISHU_DOC_ID)，跳过文档写入")
            return False

        headers = await self._auth_headers()

        try:
            # Step 1: 获取文档根 block ID
            root_block_id = await self._get_document_root_block(doc_id, headers)

            # Step 2: 构建日报内容块（飞书文档 Block 格式）
            blocks = self._build_document_blocks(markdown_content, report_date)

            # Step 3: 追加到文档末尾
            url = self.DOC_BLOCKS_URL.format(document_id=doc_id, block_id=root_block_id)

            resp = await self._client.post(
                url,
                headers=headers,
                json={"children": blocks},
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("code") == 0:
                logger.info(f"📝 日报已写入飞书文档 (doc_id={doc_id})")
                return True
            else:
                logger.error(f"❌ 写入飞书文档失败: {result.get('msg', 'unknown')}")
                return False

        except Exception as e:
            logger.error(f"❌ 写入飞书文档异常: {e}")
            return False

    # ========================================
    # 策略 2: 每天新建文档
    # ========================================

    async def create_daily_document(
        self,
        markdown_content: str,
        report_date: str,
        folder_token: str | None = None,
    ) -> str | None:
        """
        每天创建一份独立的飞书文档。

        Args:
            markdown_content: Markdown 格式的日报
            report_date: 日报日期 (YYYY-MM-DD)
            folder_token: 目标文件夹 Token

        Returns:
            创建的文档 ID，失败返回 None
        """
        folder = folder_token or self.config.doc_folder_token
        if not folder:
            logger.warning("⚠️  未配置飞书文件夹 Token (FEISHU_DOC_FOLDER_TOKEN)，跳过文档创建")
            return None

        headers = await self._auth_headers()

        try:
            # Step 1: 创建新文档
            title = f"🤖 Astribot 团队日报 | {report_date}"
            create_resp = await self._client.post(
                self.DOC_CREATE_URL,
                headers=headers,
                json={
                    "title": title,
                    "folder_token": folder,
                },
            )
            create_resp.raise_for_status()
            create_data = create_resp.json()

            if create_data.get("code") != 0:
                logger.error(f"❌ 创建飞书文档失败: {create_data.get('msg')}")
                return None

            doc_id = create_data["data"]["document"]["document_id"]
            logger.info(f"📄 已创建飞书文档: {title} (id={doc_id})")

            # Step 2: 获取根 block
            root_block_id = await self._get_document_root_block(doc_id, headers)

            # Step 3: 写入日报内容
            blocks = self._build_document_blocks(markdown_content, report_date, include_header=False)

            url = self.DOC_BLOCKS_URL.format(document_id=doc_id, block_id=root_block_id)
            write_resp = await self._client.post(
                url,
                headers=headers,
                json={"children": blocks},
            )
            write_resp.raise_for_status()
            write_data = write_resp.json()

            if write_data.get("code") == 0:
                doc_url = f"https://astribot.feishu.cn/docx/{doc_id}"
                logger.info(f"📝 日报内容写入成功: {doc_url}")
                return doc_id
            else:
                logger.error(
                    f"❌ 写入文档内容失败(doc_id={doc_id}): {write_data.get('msg')}"
                )
                return None

        except Exception as e:
            logger.error(f"❌ 创建每日文档异常: {e}")
            return None

    # ========================================
    # 统一入口
    # ========================================

    async def write_report(self, markdown_content: str, report_date: str) -> bool:
        """
        写入日报到飞书文档 — 统一入口。

        策略：
          - 如果配置了 doc_id → 追加到该文档
          - 如果配置了 doc_folder_token → 每天新建文档
          - 两者都配置 → 两个都执行
          - 都没配置 → 跳过

        Args:
            markdown_content: Markdown 日报内容
            report_date: 日报日期

        Returns:
            是否至少有一个写入成功
        """
        results: list[bool] = []

        # 策略 1: 追加到已有文档
        if self.config.doc_id:
            success = await self.append_to_document(markdown_content, report_date)
            results.append(success)

        # 策略 2: 每天新建文档
        if self.config.doc_folder_token:
            doc_id = await self.create_daily_document(markdown_content, report_date)
            results.append(doc_id is not None)

        if not results:
            logger.warning("⚠️  未配置飞书文档写入（FEISHU_DOC_ID 或 FEISHU_DOC_FOLDER_TOKEN）")
            return False

        return any(results)

    # ========================================
    # 内部方法
    # ========================================

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _get_document_root_block(self, doc_id: str, headers: dict[str, str]) -> str:
        """获取文档的根 Block ID（文档本身就是根 block）"""
        url = self.DOC_BLOCKS_LIST_URL.format(document_id=doc_id)
        resp = await self._client.get(url, headers=headers, params={"page_size": 1})
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"获取文档 Block 列表失败: {data.get('msg')}")

        # 文档根 block_id 就是 document_id
        return doc_id

    @staticmethod
    def _build_document_blocks(
        markdown_content: str,
        report_date: str,
        include_header: bool = True,
    ) -> list[dict[str, Any]]:
        """
        将 Markdown 日报转换为飞书文档 Block 结构。

        飞书文档使用 Block 模型，每个 Block 对应一个内容元素。
        我们把 Markdown 逐行解析为对应的 Block 类型。

        Block 类型参考：
          - 2: text (正文)
          - 3: heading1
          - 4: heading2
          - 5: heading3
          - 14: divider (分隔线)
          - 12: quote (引用)
          - 13: bullet (无序列表)
        """
        blocks: list[dict[str, Any]] = []

        # 添加日期分隔区（追加模式下用于区分每天的日报）
        if include_header:
            # 分隔线
            blocks.append({"block_type": 14, "divider": {}})

            # 日期标题 (Heading 2)
            blocks.append({
                "block_type": 4,
                "heading2": {
                    "elements": [{
                        "text_run": {
                            "content": f"📅 {report_date} 日报",
                            "text_element_style": {"bold": True},
                        }
                    }],
                    "style": {},
                },
            })

        # 逐行解析 Markdown
        lines = markdown_content.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()

            # 空行跳过
            if not line:
                i += 1
                continue

            # --- 分隔线
            if line.strip() == "---":
                blocks.append({"block_type": 14, "divider": {}})
                i += 1
                continue

            # ## 二级标题
            if line.startswith("## "):
                text = line[3:].strip()
                blocks.append({
                    "block_type": 4,
                    "heading2": {
                        "elements": [{"text_run": {"content": text, "text_element_style": {}}}],
                        "style": {},
                    },
                })
                i += 1
                continue

            # ### 三级标题
            if line.startswith("### "):
                text = line[4:].strip()
                blocks.append({
                    "block_type": 5,
                    "heading3": {
                        "elements": [{"text_run": {"content": text, "text_element_style": {}}}],
                        "style": {},
                    },
                })
                i += 1
                continue

            # > 引用块
            if line.startswith("> "):
                text = line[2:].strip()
                # 合并连续引用行
                while i + 1 < len(lines) and lines[i + 1].startswith("> "):
                    i += 1
                    text += "\n" + lines[i][2:].strip()
                blocks.append({
                    "block_type": 12,
                    "quote": {
                        "elements": [{"text_run": {"content": text, "text_element_style": {}}}],
                        "style": {},
                    },
                })
                i += 1
                continue

            # - 无序列表项
            if line.startswith("- ") or line.startswith("  - "):
                text = line.lstrip(" -").strip()
                blocks.append({
                    "block_type": 13,
                    "bullet": {
                        "elements": [{"text_run": {"content": text, "text_element_style": {}}}],
                        "style": {},
                    },
                })
                i += 1
                continue

            # *斜体注释行*（通常是底部署名）
            if line.startswith("*") and line.endswith("*"):
                text = line.strip("*").strip()
                blocks.append({
                    "block_type": 2,
                    "text": {
                        "elements": [{
                            "text_run": {
                                "content": text,
                                "text_element_style": {"italic": True},
                            }
                        }],
                        "style": {},
                    },
                })
                i += 1
                continue

            # 普通文本
            blocks.append({
                "block_type": 2,
                "text": {
                    "elements": [{"text_run": {"content": line, "text_element_style": {}}}],
                    "style": {},
                },
            })
            i += 1

        return blocks


class MockFeishuDocWriter:
    """
    Mock 飞书文档写入器
    ===================
    将日报写入本地文件系统，模拟飞书文档的归档效果。
    按日期创建目录结构，方便预览。
    """

    def __init__(self) -> None:
        from pathlib import Path
        self.output_dir = Path("output/feishu_docs")
        self.archive_file = self.output_dir / "daily_archive.md"

    async def write_report(self, markdown_content: str, report_date: str) -> bool:
        """模拟写入飞书文档 — 同时生成归档文档和每日文档"""
        from pathlib import Path

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # ========================================
        # 模拟策略 1: 追加到归档文档（按时间线）
        # ========================================
        archive_header = ""
        if not self.archive_file.exists():
            archive_header = (
                "# 📚 Astribot 团队日报归档\n\n"
                "> 本文档自动汇总每日团队进展，由 Astribot Daily Agent 生成。\n\n"
            )

        separator = f"\n\n{'='*60}\n\n"
        date_header = f"## 📅 {report_date}\n\n"

        with open(self.archive_file, "a", encoding="utf-8") as f:
            if archive_header:
                f.write(archive_header)
            f.write(separator)
            f.write(date_header)
            f.write(markdown_content)
            f.write("\n")

        logger.info(f"📝 [Mock] 日报已追加到归档文档: {self.archive_file}")

        # ========================================
        # 模拟策略 2: 每天新建独立文档
        # ========================================
        # 按 年/月 目录归档
        date_obj = datetime.strptime(report_date, "%Y-%m-%d")
        month_dir = self.output_dir / date_obj.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)

        daily_file = month_dir / f"{report_date}.md"
        daily_content = (
            f"# 🤖 Astribot 团队日报 | {report_date}\n\n"
            f"{markdown_content}\n"
        )
        daily_file.write_text(daily_content, encoding="utf-8")

        logger.info(f"📄 [Mock] 每日文档已创建: {daily_file}")

        # 输出预览
        print(f"\n{'─'*60}")
        print(f"  📝 飞书文档写入预览")
        print(f"{'─'*60}")
        print(f"  📚 归档文档: {self.archive_file}")
        print(f"  📄 每日文档: {daily_file}")
        print(f"  📅 日期分类: {date_obj.strftime('%Y年%m月')}/")
        print(f"{'─'*60}\n")

        return True
