"""
飞书多维表格读取器
==================
读取 Bitable 中的开发者信息表，获取姓名、GitLab 用户名、负责组件等字段。

飞书 API 文档参考：
  https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.config import FeishuConfig
from agent.models import Developer

logger = logging.getLogger(__name__)


class BitableReader:
    """飞书多维表格读取器"""

    # 飞书开放平台 API 端点
    TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    BITABLE_RECORDS_URL = (
        "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    )

    # 表格字段映射（飞书多维表格字段名 → 模型属性）
    FIELD_MAP = {
        "开发者姓名": "name",
        "GitLab 用户名": "gitlab_username",
        "负责的组件": "component",    # 可选：备注标签，日报中仓库名从 GitLab 自动读取
        "邮箱": "email",              # 可选
    }

    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._tenant_token: str | None = None
        self._client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> BitableReader:
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
        logger.info("✅ 飞书 Tenant Token 获取成功")
        return self._tenant_token

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def fetch_developers(self) -> list[Developer]:
        """
        从飞书多维表格读取所有开发者信息。

        Returns:
            开发者列表
        """
        token = await self._get_tenant_token()
        url = self.BITABLE_RECORDS_URL.format(
            app_token=self.config.bitable_app_token,
            table_id=self.config.bitable_table_id,
        )

        developers: list[Developer] = []
        page_token: str | None = None
        page_count = 0

        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token

            resp = await self._client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise RuntimeError(f"读取多维表格失败: {data.get('msg', 'unknown error')}")

            items = data.get("data", {}).get("items", [])
            page_count += 1

            for item in items:
                fields = item.get("fields", {})
                dev = self._parse_developer(fields)
                if dev:
                    developers.append(dev)

            # 分页处理
            has_more = data.get("data", {}).get("has_more", False)
            page_token = data.get("data", {}).get("page_token")

            if not has_more:
                break

        logger.info(f"📋 从飞书读取到 {len(developers)} 位开发者（{page_count} 页）")
        return developers

    def _parse_developer(self, fields: dict[str, Any]) -> Developer | None:
        """解析单条记录为 Developer 模型"""
        try:
            # 处理飞书字段值（可能是文本、数组等格式）
            parsed = {}
            for feishu_field, model_field in self.FIELD_MAP.items():
                value = fields.get(feishu_field, "")
                # 飞书有时返回列表格式的文本字段
                if isinstance(value, list):
                    value = value[0].get("text", "") if value else ""
                elif isinstance(value, dict):
                    value = value.get("text", str(value))
                parsed[model_field] = str(value).strip()

            if not parsed.get("name") or not parsed.get("gitlab_username"):
                logger.warning(f"⚠️  跳过无效记录（缺少姓名或 GitLab 用户名）: {fields}")
                return None

            return Developer(**parsed)

        except Exception as e:
            logger.warning(f"⚠️  解析开发者记录失败: {e}, fields={fields}")
            return None


class MockBitableReader:
    """Mock 飞书多维表格读取器（用于开发和演示）"""

    # 模拟 Astribot 团队成员数据
    MOCK_DEVELOPERS = [
        Developer(
            name="张伟",
            gitlab_username="zhang.wei",
            email="zhang.wei@astribot.com",
        ),
        Developer(
            name="李娜",
            gitlab_username="li.na",
            email="li.na@astribot.com",
        ),
        Developer(
            name="王强",
            gitlab_username="wang.qiang",
            email="wang.qiang@astribot.com",
        ),
        Developer(
            name="赵敏",
            gitlab_username="zhao.min",
            email="zhao.min@astribot.com",
        ),
        Developer(
            name="陈亮",
            gitlab_username="chen.liang",
            email="chen.liang@astribot.com",
        ),
        Developer(
            name="刘洋",
            gitlab_username="liu.yang",
            email="liu.yang@astribot.com",
        ),
        Developer(
            name="杨帆",
            gitlab_username="yang.fan",
            email="yang.fan@astribot.com",
        ),
        Developer(
            name="吴桐",
            gitlab_username="wu.tong",
            email="wu.tong@astribot.com",
        ),
    ]

    async def fetch_developers(self) -> list[Developer]:
        """返回模拟的开发者数据"""
        logger.info(f"🎭 [Mock] 返回 {len(self.MOCK_DEVELOPERS)} 位模拟开发者")
        return self.MOCK_DEVELOPERS
