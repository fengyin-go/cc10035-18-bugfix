"""文本来源校验数据集（只读模块）：把采集链路的校验结果交付给列表与导出接口。

数据集由实际采集生成，不接受手工登记或状态流转。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.capture_pipeline import capture_pipeline


class TextSourceDatasetService(BaseModuleService):
    MODULE = "text_source_dataset"
    ENTITY = "文本块"
    OPERATION_TERM = "导出"
    ARTIFACT = "文本来源校验数据集"
    LIST_FIELDS = [
        "块ID", "来源页面", "采集版本", "块类型", "校验结论",
        "有效样本", "时效状态", "文本哈希", "文本内容",
    ]
    REQUIRED_FIELDS = []
    STATUS_ORDER = ["已校验", "历史引用", "校验失败"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []
    READ_ONLY = True
    READ_ONLY_REASON = "文本来源校验数据集由采集链路生成，不支持手工登记或状态流转"

    def _all_rows(self) -> list[dict[str, Any]]:
        rows = []
        for index, item in enumerate(capture_pipeline.text_source_rows(), start=1):
            rows.append({
                "id": index,
                "status": item["校验结论"],
                "pending": item["有效样本"] != "是",
                "abnormal": item["校验结论"] == "校验失败",
                **item,
            })
        return rows

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._all_rows()
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword:
            rows = [
                row for row in rows
                if keyword.casefold() in str(row.get("块ID", "")).casefold()
                or keyword.casefold() in str(row.get("文本内容", "")).casefold()
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        rows = self._all_rows()
        return rows[entry_id - 1] if 0 < entry_id <= len(rows) else None

    def domain_snapshot(self) -> dict[str, Any]:
        rows = self._all_rows()
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": self.ARTIFACT,
            "count": len(rows),
            "valid_sample_count": sum(1 for row in rows if row["有效样本"] == "是"),
            "legacy_reference_count": sum(
                1 for row in rows if row["校验结论"] == "历史引用"),
            "by_status": {
                status: sum(1 for row in rows if row["status"] == status)
                for status in self.STATUS_ORDER
            },
            "abnormal": sum(1 for row in rows if row["abnormal"]),
        }
