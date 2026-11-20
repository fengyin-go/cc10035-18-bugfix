"""待补采清单（只读模块）：最近一次采集为空正文或无法获取的页面。

清单由采集链路维护，情感分析结果回写在这里；模块本身不提供手工建单。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.capture_pipeline import capture_pipeline


class BackfillListService(BaseModuleService):
    MODULE = "backfill_list"
    ENTITY = "缺测页"
    OPERATION_TERM = "补采"
    LIST_FIELDS = [
        "URL", "状态", "原因", "最近采集版本", "最近采集状态",
        "上次可用采集版本", "最近分析采集版本", "最近分析时效", "更新时间",
    ]
    REQUIRED_FIELDS = []
    STATUS_ORDER = ["待补采", "已解除"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []
    READ_ONLY = True
    READ_ONLY_REASON = "待补采清单由采集链路自动登记，补采请调用采集重试接口"

    def _all_rows(self) -> list[dict[str, Any]]:
        rows = []
        for index, item in enumerate(capture_pipeline.backfill_list()["items"], start=1):
            rows.append({
                "id": index,
                "status": item.get("状态", ""),
                "pending": item.get("状态") == "待补采",
                "abnormal": item.get("状态") == "待补采",
                **{field: item.get(field, "") for field in self.LIST_FIELDS},
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
            rows = [row for row in rows
                    if keyword.casefold() in str(row.get("URL", "")).casefold()]
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
            "artifact": "待补采清单",
            "count": len(rows),
            "pending_count": sum(1 for row in rows if row["pending"]),
            "by_status": {
                status: sum(1 for row in rows if row["status"] == status)
                for status in self.STATUS_ORDER
            },
            "abnormal": sum(1 for row in rows if row["abnormal"]),
        }
