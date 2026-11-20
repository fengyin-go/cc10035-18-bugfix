"""采集版本仓库：保存每次抓取的原文、正文状态和片段出处。

同一个 URL 的每次抓取都是一个独立版本，历史版本不会被覆盖：
- 有效文本：能解析出至少一个正文片段；
- 空正文：请求成功但页面没有可分析正文；
- 无法获取：网络错误、超时或非 2xx 响应。

这里只登记事实，不做任何补全。空正文和无法获取都不会生成片段，
只有有效文本版本才会进入文本来源校验数据集。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Any

from app.html_segments import build_capture_text, detect_language, extract_fragments


CAPTURE_VALID = "有效文本"
CAPTURE_EMPTY = "空正文"
CAPTURE_UNREACHABLE = "无法获取"

BACKFILL_PENDING = "待补采"
BACKFILL_RESOLVED = "已解除"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CaptureRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._captures: list[dict[str, Any]] = []
        self._backfill: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    # ---------------------------------------------------------------- 采集版本
    def reset(self) -> None:
        """清空采集与补采记录：服务内部不调用，留给测试和本地演示复位。"""
        with self._lock:
            self._captures.clear()
            self._backfill.clear()
            self._next_id = 1

    def captures(self, url: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._captures)
        if url is not None:
            rows = [row for row in rows if row["url"] == url]
        return [dict(row) for row in rows]

    def get(self, capture_id: int) -> dict[str, Any] | None:
        with self._lock:
            for row in self._captures:
                if row["id"] == capture_id:
                    return dict(row)
        return None

    def latest(self, url: str) -> dict[str, Any] | None:
        rows = self.captures(url)
        return rows[-1] if rows else None

    def latest_usable(self, url: str) -> dict[str, Any] | None:
        rows = [row for row in self.captures(url) if row["status"] == CAPTURE_VALID]
        return rows[-1] if rows else None

    def record_capture(
        self,
        url: str,
        raw_html: str | None = None,
        *,
        error: str | None = None,
        http_status: int | None = None,
        fetched_at: str | None = None,
        task_id: int | None = None,
        retry_of: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """登记一次实际采集结果，并根据实际内容判定状态。"""
        url = (url or "").strip()
        if not url:
            raise ValueError("采集 URL 不能为空")

        with self._lock:
            version = sum(1 for row in self._captures if row["url"] == url) + 1

            fragments = []
            full_text = ""
            if error:
                status = CAPTURE_UNREACHABLE
            elif http_status is not None and not 200 <= http_status < 300:
                status = CAPTURE_UNREACHABLE
                error = error or f"HTTP {http_status}"
            else:
                fragments = extract_fragments(raw_html)
                full_text, fragments = build_capture_text(fragments)
                status = CAPTURE_VALID if fragments else CAPTURE_EMPTY

            content_hash = None
            if raw_html is not None:
                content_hash = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()

            row = {
                "id": self._next_id,
                "url": url,
                "version": version,
                "status": status,
                "fetched_at": fetched_at or _now(),
                "http_status": http_status,
                "error": error,
                "raw_html": raw_html,
                "text": full_text,
                "fragments": fragments,
                "content_hash": content_hash,
                "text_sha256": (
                    hashlib.sha256(full_text.encode("utf-8")).hexdigest()
                    if full_text else None
                ),
                "char_length": len(full_text),
                "fragment_count": len(fragments),
                "language": detect_language(full_text) if full_text else None,
                "task_id": task_id,
                "retry_of": retry_of,
                "note": note,
            }
            self._next_id += 1
            self._captures.append(row)
            self._refresh_backfill_for_capture(row)
            return dict(row)

    def retry_capture(
        self,
        capture_id: int,
        *,
        raw_html: str | None = None,
        error: str | None = None,
        http_status: int | None = None,
        fetched_at: str | None = None,
    ) -> dict[str, Any] | None:
        """对失败/空正文的采集做重试，保留与原采集的关联。"""
        origin = self.get(capture_id)
        if origin is None:
            return None
        row = self.record_capture(
            origin["url"],
            raw_html,
            error=error,
            http_status=http_status,
            fetched_at=fetched_at,
            task_id=origin.get("task_id"),
            retry_of=capture_id,
            note=f"重试采集版本{origin['version']}",
        )
        with self._lock:
            origin_row = next(r for r in self._captures if r["id"] == capture_id)
            origin_row.setdefault("retried_by", []).append(row["id"])
        return row

    # ---------------------------------------------------------------- 待补采清单
    def _refresh_backfill_for_capture(self, capture: dict[str, Any]) -> None:
        url = capture["url"]
        current = self._backfill.get(url)
        if capture["status"] == CAPTURE_VALID:
            if current and current.get("状态") == BACKFILL_PENDING:
                current["状态"] = BACKFILL_RESOLVED
                current["待补采"] = False
                current["解除采集ID"] = capture["id"]
                current["解除采集版本"] = capture["version"]
                current["更新时间"] = capture["fetched_at"]
                current.setdefault("历史", []).append({
                    "事件": "补采成功",
                    "采集ID": capture["id"],
                    "采集版本": capture["version"],
                    "时间": capture["fetched_at"],
                })
            return

        reason = (
            "最新采集返回空正文，没有可分析文本"
            if capture["status"] == CAPTURE_EMPTY
            else f"最新采集无法获取：{capture.get('error') or '请求失败'}"
        )
        if current is None:
            current = {
                "URL": url,
                "状态": BACKFILL_PENDING,
                "原因": reason,
                "首次发现": capture["fetched_at"],
                "历史": [],
                "分析记录": [],
            }
            self._backfill[url] = current
        else:
            current["状态"] = BACKFILL_PENDING
            current["待补采"] = True
            current["原因"] = reason
        current["最近采集ID"] = capture["id"]
        current["最近采集版本"] = capture["version"]
        current["最近采集状态"] = capture["status"]
        current["更新时间"] = capture["fetched_at"]
        current["待补采"] = True
        usable = self.latest_usable(url)
        if usable is not None and usable["id"] != capture["id"]:
            current["上次可用采集ID"] = usable["id"]
            current["上次可用采集版本"] = usable["version"]
            current["上次可用抓取时间"] = usable["fetched_at"]
        current.setdefault("历史", []).append({
            "事件": "发现缺测页",
            "采集ID": capture["id"],
            "采集版本": capture["version"],
            "采集状态": capture["status"],
            "原因": reason,
            "时间": capture["fetched_at"],
        })

    def backfill_items(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(item) for item in self._backfill.values()]
        if status:
            items = [item for item in items if item.get("状态") == status]
        return sorted(items, key=lambda item: item.get("更新时间", ""))

    def backfill_for(self, url: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._backfill.get(url)
            return dict(item) if item else None

    def write_back_analysis(self, url: str, record: dict[str, Any]) -> dict[str, Any]:
        """把一次情感分析的实际输入和执行结论回写到待补采清单。

        只更新已存在的跟踪项；没有缺测记录的页面不会凭空出现在补采清单里。
        """
        with self._lock:
            item = self._backfill.get(url)
            if item is None:
                return {}
            records = item.setdefault("分析记录", [])
            # 同一块重复分析时以最新一次为准，避免清单里滚动同一段的旧结论。
            records[:] = [r for r in records if r.get("块ID") != record.get("块ID")]
            records.append(record)
            item["最近分析记录ID"] = record.get("记录ID")
            item["最近分析块ID"] = record.get("块ID")
            item["最近分析采集版本"] = record.get("采集版本")
            item["最近分析时效"] = record.get("时效状态")
            item["更新时间"] = record.get("分析时间") or _now()
            return dict(item)


capture_registry = CaptureRegistry()
