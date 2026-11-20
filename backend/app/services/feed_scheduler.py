"""种子调度领域服务：管理URL种子的批量爬取调度。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FeedSchedulerService(BaseModuleService):
    MODULE = "feed_scheduler"
    ENTITY = "调度任务"
    OPERATION_TERM = "抓取"
    LIST_FIELDS = ["任务名称", "URL种子", "抓取深度", "爬取频率", "最大页面数", "域名限制", "优先级"]
    REQUIRED_FIELDS = ["任务名称", "URL种子", "抓取深度"]
    STATUS_ORDER = ["待调度", "执行中", "已完成", "部分失败"]
    ACTION_RULES = {"开始抓取": "待调度", "完成任务": "执行中", "标记失败": "执行中"}
    NEGATIVE_ACTIONS = []

    def extract_urls_from_seed(self, seed_url: str, depth: int) -> dict[str, Any]:
        """从种子URL推导子页面列表，模拟爬取链路。"""
        urls = []
        seed_stripped = seed_url.rstrip("/")
        for d in range(1, depth + 1):
            for p in range(1, min(d * 3 + 1, 6)):
                urls.append(f"{seed_stripped}/page/{d}/{p}")
        return {
            "ok": True,
            "seed_url": seed_url,
            "depth": depth,
            "generated_url_count": len(urls),
            "urls": urls,
        }

    def feed_text_segmentation(self, task_id: int) -> dict[str, Any]:
        """调度 url 种子，把待抓页面交给文本分块模块。

        这里只推导出要抓哪些页面，不再预生成块类型和字符数：
        真实块必须等实际采集回来、按采集片段切分之后才有。
        """
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "reason": "调度任务不存在"}
        seed = task.get("URL种子", "")
        depth_str = task.get("抓取深度", "1")
        try:
            depth = int(depth_str)
        except (ValueError, TypeError):
            depth = 1

        url_result = self.extract_urls_from_seed(seed, min(depth, 3))
        pages = url_result["urls"]
        return {
            "ok": True,
            "task_id": task_id,
            "seed_url": seed,
            "total_pages": len(pages),
            "urls": pages,
            "pending_capture": True,
            "note": "块只有在实际采集并按片段切分后才会产生",
        }

    def compute_scheduler_stats(self) -> dict[str, Any]:
        """调度统计指标。"""
        rows = store.rows(self.MODULE)
        completed = sum(1 for r in rows if r.get("status") == "已完成")
        failed = sum(1 for r in rows if r.get("status") == "部分失败")
        in_progress = sum(1 for r in rows if r.get("status") == "执行中")
        total_depth = 0
        for r in rows:
            try:
                total_depth += int(r.get("抓取深度", "0"))
            except (ValueError, TypeError):
                pass
        return {
            "total_tasks": len(rows),
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "avg_depth": round(total_depth / len(rows), 1) if rows else 0.0,
            "success_ratio": round(completed / len(rows), 2) if rows else 0.0,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["scheduler_stats"] = self.compute_scheduler_stats()
        return base