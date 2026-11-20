"""文本分块领域服务：按采集片段切分页面正文，并交付文本来源校验数据集。

关键约束：
- 只切分实际采集到的正文，没有正文时返回 0 个块，绝不生成示例内容；
- 每个块都能回溯到「采集ID + 采集版本 + 片段序号 + 文本哈希」；
- 旧版工作台产生的示例块不再参与有效样本统计，但历史标注引用仍可解析出处。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.capture_registry import (
    CAPTURE_EMPTY,
    CAPTURE_UNREACHABLE,
    CAPTURE_VALID,
    capture_registry,
)
from app.services.base import BaseModuleService
from app.store import store


BLOCK_TYPES = ["paragraph", "heading", "list_item", "table_cell", "code_block", "quote"]


def make_block_id(url: str, index: int, text_sha256: str) -> str:
    """块ID 由页面、片段位置和片段内容共同决定。

    不含版本号：同一份正文重新抓取时仍然落到同一个块上，
    历史标注不会因为多抓了一次就全部失效；内容变了才会产生新块。
    """
    return hashlib.md5(
        f"{url}|{index}|{text_sha256}".encode("utf-8")
    ).hexdigest()[:12]


class TextSegmentService(BaseModuleService):
    MODULE = "text_segment"
    ENTITY = "文本块"
    OPERATION_TERM = "入库"
    LIST_FIELDS = ["块ID", "来源页面", "采集版本", "块类型", "起始位置", "结束位置", "字符长度", "语言标识"]
    REQUIRED_FIELDS = ["块ID", "来源页面", "块类型"]
    STATUS_ORDER = ["已切分", "已标注", "已入库", "已丢弃"]
    ACTION_RULES = {"标注语义": "已切分", "确认入库": "已标注", "丢弃无用": "已切分"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------ 采集入湖
    def segment_page(
        self,
        page_url: str,
        raw_html: str = "",
        *,
        error: str | None = None,
        http_status: int | None = None,
        fetched_at: str | None = None,
        task_id: int | None = None,
        retry_of: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """用实际采集结果切分页面，并返回该次采集的文本来源数据。

        空正文、无法获取与有效文本会被区分为三种状态；只有有效文本会产生分块。
        """
        capture = capture_registry.record_capture(
            page_url,
            raw_html,
            error=error,
            http_status=http_status,
            fetched_at=fetched_at,
            task_id=task_id,
            retry_of=retry_of,
            note=note,
        )
        return self.ingest_capture(capture["id"])

    def ingest_capture(self, capture_id: int) -> dict[str, Any]:
        """把一次采集的正文片段登记为文本来源校验数据集中的块。"""
        capture = capture_registry.get(capture_id)
        if capture is None:
            return {"ok": False, "status": "未知采集", "reason": f"采集 {capture_id} 不存在",
                    "blocks": [], "block_count": 0}

        if capture["status"] != CAPTURE_VALID:
            reason = (
                "本次采集没有正文，未生成任何文本块"
                if capture["status"] == CAPTURE_EMPTY
                else f"本次采集无法获取：{capture.get('error') or '请求失败'}"
            )
            return {
                "ok": False,
                "status": capture["status"],
                "reason": reason,
                "capture_id": capture["id"],
                "capture_version": capture["version"],
                "page_url": capture["url"],
                "fetched_at": capture["fetched_at"],
                "block_count": 0,
                "total_chars": 0,
                "blocks": [],
            }

        blocks = [self._upsert_block(capture, fragment) for fragment in capture["fragments"]]
        return {
            "ok": True,
            "status": capture["status"],
            "capture_id": capture["id"],
            "capture_version": capture["version"],
            "page_url": capture["url"],
            "fetched_at": capture["fetched_at"],
            "content_hash": capture["content_hash"],
            "block_count": len(blocks),
            "total_chars": capture["char_length"],
            "blocks": blocks,
        }

    def _upsert_block(self, capture: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
        block_id = make_block_id(
            capture["url"], fragment["index"], fragment["text_sha256"])
        row = next(
            (r for r in store.rows(self.MODULE) if r.get("块ID") == block_id), None)
        values = {
            "块ID": block_id,
            "来源页面": capture["url"],
            "块类型": fragment["block_type"],
            "起始位置": str(fragment["start"]),
            "结束位置": str(fragment["end"]),
            "字符长度": str(fragment["char_length"]),
            "语言标识": capture.get("language") or "unknown",
            "采集ID": str(capture["id"]),
            "采集版本": str(capture["version"]),
            "采集状态": capture["status"],
            "抓取时间": capture["fetched_at"],
            "片段序号": str(fragment["index"]),
            "片段选择器": fragment["selector"],
            "文本内容": fragment["text"],
            "文本哈希": fragment["text_sha256"],
            "来源校验": "已校验",
            "样本类型": "实际采集",
        }
        if row is None:
            row = {
                "id": max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1,
                "status": "已切分",
                "pending": True,
                "abnormal": False,
            }
            store.rows(self.MODULE).append(row)
        row.update(values)
        return self._block_view(row)

    def _block_view(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "block_id": row.get("块ID"),
            "source_page": row.get("来源页面"),
            "block_type": row.get("块类型"),
            "start": row.get("起始位置"),
            "end": row.get("结束位置"),
            "char_length": row.get("字符长度"),
            "language": row.get("语言标识"),
            "capture_id": row.get("采集ID"),
            "capture_version": row.get("采集版本"),
            "fetched_at": row.get("抓取时间"),
            "fragment_index": row.get("片段序号"),
            "selector": row.get("片段选择器"),
            "text": row.get("文本内容", ""),
            "text_sha256": row.get("文本哈希"),
            "status": row.get("status"),
        }

    # ------------------------------------------------------------------ 出处解析
    def resolve_block(self, block_id: str) -> dict[str, Any]:
        """解析块出处并做来源校验，供情感标注和报告共同使用。"""
        row = next(
            (r for r in store.rows(self.MODULE) if r.get("块ID") == block_id), None)
        if row is None:
            return {
                "found": False,
                "kind": "missing",
                "block_id": block_id,
                "verdict": "来源缺失",
                "valid_sample": False,
                "reason": "文本块不存在，不能据此形成结论",
            }

        capture_id = str(row.get("采集ID") or "").strip()
        if not capture_id:
            return {
                "found": True,
                "kind": "legacy",
                "block_id": block_id,
                "row": row,
                "verdict": "历史引用",
                "valid_sample": False,
                "reason": "历史分块未绑定采集原文，仅保留出处引用，不进入有效样本",
            }

        capture = capture_registry.get(int(capture_id))
        if capture is None:
            return {
                "found": True,
                "kind": "legacy",
                "block_id": block_id,
                "row": row,
                "verdict": "来源缺失",
                "valid_sample": False,
                "reason": f"引用的采集 {capture_id} 已不存在，不能形成结论",
            }

        verification_error = self._verify_fragment(row, capture)
        if verification_error:
            return {
                "found": True,
                "kind": "dataset",
                "block_id": block_id,
                "row": row,
                "capture": capture,
                "verdict": "校验失败",
                "valid_sample": False,
                "reason": verification_error,
            }

        latest = capture_registry.latest(capture["url"])
        latest_usable = capture_registry.latest_usable(capture["url"])
        superseded_by = None
        stale_conflict = False
        if latest_usable is not None and latest_usable["id"] != capture["id"]:
            freshness = "过期版本"
            reason = (
                f"该块来自采集版本{capture['version']}，"
                f"已有更新的可用采集版本{latest_usable['version']}"
            )
            superseded_by = latest_usable["version"]
        elif latest is not None and latest["id"] != capture["id"]:
            freshness = "滞后可用版本"
            reason = (
                f"该块来自采集版本{capture['version']}；最新采集版本{latest['version']}为"
                f"{latest['status']}，分析沿用最近可用版本"
            )
            stale_conflict = True
        else:
            freshness = "最新版本"
            reason = "该块来自该页面最新一次有效采集"

        discarded = row.get("status") == "已丢弃"
        valid_sample = not discarded and superseded_by is None
        if discarded:
            reason = "该块已被标记丢弃，不进入有效样本"
        return {
            "found": True,
            "kind": "dataset",
            "block_id": block_id,
            "row": row,
            "capture": capture,
            "verdict": "已校验",
            "valid_sample": valid_sample,
            "freshness": freshness,
            "stale_conflict": stale_conflict,
            "superseded_by": superseded_by,
            "latest_capture_id": latest["id"] if latest else None,
            "latest_capture_version": latest["version"] if latest else None,
            "latest_capture_status": latest["status"] if latest else None,
            "latest_usable_capture_id": latest_usable["id"] if latest_usable else None,
            "latest_usable_capture_version": latest_usable["version"] if latest_usable else None,
            "reason": reason,
        }

    @staticmethod
    def _verify_fragment(row: dict[str, Any], capture: dict[str, Any]) -> str | None:
        if capture["status"] != CAPTURE_VALID:
            return f"来源采集状态为{capture['status']}，没有可分析正文"
        try:
            index = int(row.get("片段序号", ""))
            fragment = capture["fragments"][index]
        except (ValueError, TypeError, IndexError):
            return "片段序号与来源采集不一致，无法核对原文"
        text = str(row.get("文本内容", ""))
        if fragment.get("text") != text:
            return "块内容与来源采集原文不一致，不能形成结论"
        expected_hash = str(row.get("文本哈希", ""))
        if fragment.get("text_sha256") != expected_hash:
            return "文本哈希与来源采集不一致，不能形成结论"
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash:
            return "块内容哈希校验失败，不能形成结论"
        return None

    # ------------------------------------------------------------------ 数据集
    def build_source_dataset(
        self,
        *,
        url: str | None = None,
        capture_id: int | None = None,
        include_legacy: bool = True,
    ) -> dict[str, Any]:
        """交付文本来源校验数据集：块、出处、校验结论和有效样本口径。"""
        latest_capture = None
        if capture_id is not None:
            capture = capture_registry.get(capture_id)
            captures = [capture] if capture else []
            latest_capture = capture
        elif url:
            latest_capture = capture_registry.latest(url)
            latest_usable = capture_registry.latest_usable(url)
            captures = [latest_usable] if latest_usable else []
        else:
            latest_usable_ids = {
                capture["id"] for capture in
                (capture_registry.latest_usable(item["url"])
                 for item in capture_registry.captures())
                if capture is not None
            }
            captures = [c for c in capture_registry.captures()
                        if c["id"] in latest_usable_ids]

        block_ids: list[str] = []
        for capture in captures:
            if capture is None:
                continue
            for fragment in capture["fragments"]:
                block_ids.append(make_block_id(
                    capture["url"], fragment["index"], fragment["text_sha256"]))

        blocks = [self.resolve_block(block_id) for block_id in block_ids]
        items = []
        for resolution in blocks:
            if not resolution["found"]:
                continue
            row = resolution["row"]
            item = self._block_view(row)
            item.update({
                "verdict": resolution["verdict"],
                "valid_sample": resolution["valid_sample"],
                "freshness": resolution.get("freshness"),
                "stale_conflict": resolution.get("stale_conflict", False),
                "reason": resolution["reason"],
            })
            items.append(item)

        legacy_items = []
        if include_legacy:
            legacy_rows = [
                row for row in store.rows(self.MODULE)
                if not str(row.get("采集ID") or "").strip()
            ]
            if url:
                legacy_rows = [row for row in legacy_rows
                               if row.get("来源页面") == url]
            legacy_items = [
                self._block_view(row) | {
                    "verdict": "历史引用",
                    "valid_sample": False,
                    "reason": "历史分块未绑定采集原文，仅用于兼容既有标注引用",
                }
                for row in legacy_rows
            ]

        return {
            "ok": True,
            "url": url,
            "capture_id": capture_id,
            "capture_versions": [
                {
                    "capture_id": c["id"],
                    "version": c["version"],
                    "status": c["status"],
                    "fetched_at": c["fetched_at"],
                    "fragment_count": c["fragment_count"],
                    "char_length": c["char_length"],
                }
                for c in captures if c is not None
            ],
            # 该 URL 最近一次采集可能不是有效版本；数据集里要显式交代，
            # 否则页面上只有旧块、看不到「最新一次是空页」这件事。
            "latest_capture": (
                {
                    "capture_id": latest_capture["id"],
                    "version": latest_capture["version"],
                    "status": latest_capture["status"],
                    "fetched_at": latest_capture["fetched_at"],
                }
                if latest_capture is not None else None
            ),
            "latest_capture_included": bool(
                latest_capture is not None and latest_capture["status"] == CAPTURE_VALID
                and any(c is not None and c["id"] == latest_capture["id"] for c in captures)),
            "block_count": len(items),
            "valid_sample_count": sum(1 for item in items if item["valid_sample"]),
            "stale_sample_count": sum(
                1 for item in items
                if item["valid_sample"] and item.get("stale_conflict")),
            "blocks": items,
            "legacy_reference_count": len(legacy_items),
            "legacy_references": legacy_items,
        }

    def feed_sentiment_tag(self, block_ids: list[str]) -> dict[str, Any]:
        """将文本来源校验数据集里的真实块交付给情感标注。"""
        blocks = []
        rejected = []
        for block_id in block_ids:
            resolution = self.resolve_block(block_id)
            if resolution["kind"] == "dataset" and resolution["verdict"] == "已校验":
                row = resolution["row"]
                blocks.append({
                    "block_id": block_id,
                    "source_page": row.get("来源页面"),
                    "block_type": row.get("块类型"),
                    "text": row.get("文本内容", ""),
                    "text_sha256": row.get("文本哈希"),
                    "capture_id": row.get("采集ID"),
                    "capture_version": row.get("采集版本"),
                    "fetched_at": row.get("抓取时间"),
                    "valid_sample": resolution["valid_sample"],
                    "freshness": resolution.get("freshness"),
                })
            else:
                rejected.append({
                    "block_id": block_id,
                    "verdict": resolution["verdict"],
                    "reason": resolution["reason"],
                })
        return {
            "ok": True,
            "input_blocks": len(block_ids),
            "matched_blocks": len(blocks),
            "rejected_blocks": len(rejected),
            "blocks": blocks,
            "rejected": rejected,
        }

    def compute_segment_stats(self) -> dict[str, Any]:
        """分块统计：有效样本数与情感统计使用同一口径。"""
        rows = store.rows(self.MODULE)
        type_dist: dict[str, int] = {}
        total_chars = 0
        valid_samples = 0
        legacy = 0
        for row in rows:
            btype = row.get("块类型", "unknown")
            type_dist[btype] = type_dist.get(btype, 0) + 1
            try:
                total_chars += int(row.get("字符长度", "0"))
            except (ValueError, TypeError):
                pass
            if not str(row.get("采集ID") or "").strip():
                legacy += 1
                continue
            resolution = self.resolve_block(str(row.get("块ID")))
            if resolution.get("valid_sample"):
                valid_samples += 1
        return {
            "total_blocks": len(rows),
            "valid_sample_count": valid_samples,
            "legacy_block_count": legacy,
            "total_chars": total_chars,
            "avg_chars_per_block": round(total_chars / len(rows)) if rows else 0,
            "block_type_distribution": type_dist,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["segment_stats"] = self.compute_segment_stats()
        base["source_dataset"] = {
            "valid_sample_count": self.build_source_dataset()["valid_sample_count"],
        }
        return base
