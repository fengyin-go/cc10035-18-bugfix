"""采集→分块→情感分析整条链路的编排与报告。

这个服务不拥有业务数据，只把文本分块、情感标注和采集版本三个来源
按同一口径拼成可核查的视图：
- 文本来源校验数据集（哪些块真的来自某次采集）；
- 待补采清单（哪些页面最新采集为空/无法获取）；
- 每次情感分析实际用了哪个采集版本；
- 当前空页是否进入了有效情感分布。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.capture_registry import (
    CAPTURE_EMPTY,
    CAPTURE_UNREACHABLE,
    CAPTURE_VALID,
    capture_registry,
)
from app.services.sentiment_tag import SentimentTagService
from app.services.text_segment import TextSegmentService
from app.store import store


class CapturePipelineService:
    def __init__(self) -> None:
        self.segments = TextSegmentService()
        self.sentiment = SentimentTagService()

    # ---------------------------------------------------------------- 采集
    def capture_page(
        self,
        url: str,
        raw_html: str | None = None,
        *,
        error: str | None = None,
        http_status: int | None = None,
        fetched_at: str | None = None,
        task_id: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        ingested = self.segments.segment_page(
            url, raw_html or "",
            error=error, http_status=http_status,
            fetched_at=fetched_at, task_id=task_id, note=note,
        )
        capture = capture_registry.latest(url)
        ingested["capture"] = self._capture_view(capture)
        ingested["backfill"] = capture_registry.backfill_for(url)
        return ingested

    def retry_capture(
        self,
        capture_id: int,
        raw_html: str | None = None,
        *,
        error: str | None = None,
        http_status: int | None = None,
        fetched_at: str | None = None,
    ) -> dict[str, Any]:
        origin = capture_registry.get(capture_id)
        if origin is None:
            return {"ok": False, "reason": f"采集 {capture_id} 不存在"}
        retried = capture_registry.retry_capture(
            capture_id, raw_html=raw_html, error=error,
            http_status=http_status, fetched_at=fetched_at,
        )
        if retried is None:
            return {"ok": False, "reason": f"采集 {capture_id} 不存在"}
        ingested = self.segments.ingest_capture(retried["id"])
        ingested["capture"] = self._capture_view(retried)
        ingested["retry_of"] = {
            "capture_id": origin["id"],
            "version": origin["version"],
            "status": origin["status"],
        }
        ingested["backfill"] = capture_registry.backfill_for(retried["url"])
        return ingested

    def list_captures(self, url: str | None = None) -> dict[str, Any]:
        captures = capture_registry.captures(url)
        return {
            "ok": True,
            "url": url,
            "total": len(captures),
            "items": [self._capture_view(capture) for capture in captures],
        }

    @staticmethod
    def _capture_view(capture: dict[str, Any] | None) -> dict[str, Any] | None:
        if capture is None:
            return None
        return {
            "capture_id": capture["id"],
            "url": capture["url"],
            "version": capture["version"],
            "status": capture["status"],
            "fetched_at": capture["fetched_at"],
            "http_status": capture["http_status"],
            "error": capture["error"],
            "fragment_count": capture["fragment_count"],
            "char_length": capture["char_length"],
            "content_hash": capture["content_hash"],
            "text_sha256": capture["text_sha256"],
            "language": capture["language"],
            "retry_of": capture["retry_of"],
            "task_id": capture["task_id"],
            "note": capture["note"],
        }

    # ---------------------------------------------------------------- 数据集
    def text_source_dataset(
        self,
        *,
        url: str | None = None,
        capture_id: int | None = None,
        include_legacy: bool = True,
    ) -> dict[str, Any]:
        return self.segments.build_source_dataset(
            url=url, capture_id=capture_id, include_legacy=include_legacy)

    def text_source_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
        """把数据集摊平成行，便于导出 CSV 逐条核对。"""
        dataset = self.text_source_dataset(**kwargs)
        rows: list[dict[str, Any]] = []
        for item in dataset["blocks"]:
            rows.append({
                "范围": "实际采集",
                "块ID": item["block_id"],
                "来源页面": item["source_page"],
                "采集ID": item["capture_id"],
                "采集版本": item["capture_version"],
                "抓取时间": item["fetched_at"],
                "片段序号": item["fragment_index"],
                "片段选择器": item["selector"],
                "块类型": item["block_type"],
                "字符长度": item["char_length"],
                "文本哈希": item["text_sha256"],
                "校验结论": item["verdict"],
                "有效样本": "是" if item["valid_sample"] else "否",
                "时效状态": item.get("freshness") or "",
                "说明": item["reason"],
                "文本内容": item["text"],
            })
        for item in dataset.get("legacy_references", []):
            rows.append({
                "范围": "历史引用",
                "块ID": item["block_id"],
                "来源页面": item["source_page"],
                "采集ID": "",
                "采集版本": "",
                "抓取时间": "",
                "片段序号": item.get("fragment_index", ""),
                "片段选择器": item.get("selector", ""),
                "块类型": item["block_type"],
                "字符长度": item["char_length"],
                "文本哈希": item.get("text_sha256") or "",
                "校验结论": "历史引用",
                "有效样本": "否",
                "时效状态": "",
                "说明": item["reason"],
                "文本内容": item.get("text", ""),
            })
        return rows

    # ---------------------------------------------------------------- 待补采
    def backfill_list(self, status: str | None = None) -> dict[str, Any]:
        items = capture_registry.backfill_items(status)
        return {
            "ok": True,
            "total": len(items),
            "pending": sum(1 for item in items if item.get("状态") == "待补采"),
            "items": items,
        }

    def resolve_backfill(self, url: str) -> dict[str, Any]:
        """对缺测页发起一次人工补采，并核对最新状态。"""
        item = capture_registry.backfill_for(url)
        if item is None:
            return {"ok": False, "reason": f"{url} 不在待补采清单中"}
        latest = capture_registry.latest(url)
        return {
            "ok": True,
            "url": url,
            "backfill": item,
            "latest_capture": self._capture_view(latest),
            "usable": latest is not None and latest["status"] == CAPTURE_VALID,
        }

    # ---------------------------------------------------------------- 情感
    def analyze_blocks(self, block_ids: list[str]) -> dict[str, Any]:
        return self.sentiment.batch_analyze(block_ids)

    def analyze_page(self, url: str) -> dict[str, Any]:
        return self.sentiment.analyze_page(url)

    def sentiment_summary(self) -> dict[str, Any]:
        return self.sentiment.compute_sentiment_summary()

    def consistency(self) -> dict[str, Any]:
        """情感统计与分块清单的有效样本数量对账。

        分块清单与文本来源数据集必须给出同一个有效块数；
        情感统计只能统计这些有效块，尚未分析的有效块单独列为待分析，
        既不虚增也不让失效记录混进分布。
        """
        segment_stats = self.segments.compute_segment_stats()
        sentiment_summary = self.sentiment.compute_sentiment_summary()
        dataset = self.segments.build_source_dataset()
        block_valid = segment_stats["valid_sample_count"]
        dataset_valid = dataset["valid_sample_count"]
        analyzed_blocks = sentiment_summary["valid_analyzed_blocks"]
        analyzed_records = sentiment_summary["valid_analyzed_records"]
        distribution_total = sum(sentiment_summary["sentiment_distribution"].values())
        pending_blocks = max(block_valid - analyzed_blocks, 0)

        mismatches: list[str] = []
        if block_valid != dataset_valid:
            mismatches.append("分块清单与文本来源数据集的有效块数不一致")
        if analyzed_blocks + len(sentiment_summary["unanalyzed_valid_blocks"]) \
                != sentiment_summary["dataset_valid_block_count"]:
            mismatches.append("情感统计的有效块数与数据集有效块对不上")
        if analyzed_records != distribution_total:
            mismatches.append("情感分布合计与有效分析记录数不一致")
        return {
            "ok": not mismatches,
            "values": {
                "块清单有效样本": block_valid,
                "文本来源数据集有效块": dataset_valid,
                "情感统计有效块": analyzed_blocks,
                "情感统计有效记录": analyzed_records,
                "情感分布合计": distribution_total,
                "待分析有效块": pending_blocks,
            },
            "unanalyzed_valid_blocks": sentiment_summary["unanalyzed_valid_blocks"],
            "excluded_records": sentiment_summary["excluded_records"],
            "mismatches": mismatches,
            "note": "；".join(mismatches) if mismatches else (
                "有效样本口径一致" if pending_blocks == 0
                else f"有效样本口径一致，另有{pending_blocks}个有效块尚未分析"
            ),
        }

    # ---------------------------------------------------------------- 报告
    def build_report(self, url: str | None = None) -> dict[str, Any]:
        urls = [url] if url else sorted(
            {capture["url"] for capture in capture_registry.captures()})

        pages: list[dict[str, Any]] = []
        total_valid_blocks = 0
        total_valid_records = 0
        empty_pages: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []

        for page_url in urls:
            dataset = self.segments.build_source_dataset(url=page_url)
            latest = capture_registry.latest(page_url)
            latest_usable = capture_registry.latest_usable(page_url)
            used = dataset["capture_versions"][0] if dataset["capture_versions"] else None

            records = [
                row for row in store.rows(self.sentiment.MODULE)
                if row.get("来源页面") == page_url and row.get("块ID")
            ]
            valid_block_ids = {
                item["block_id"] for item in dataset["blocks"] if item["valid_sample"]}
            valid_records = [
                row for row in records if str(row.get("块ID")) in valid_block_ids]

            analyses = [{
                "记录ID": row.get("记录ID"),
                "块ID": row.get("块ID"),
                "采集ID": row.get("采集ID"),
                "采集版本": row.get("采集版本"),
                "抓取时间": row.get("抓取时间"),
                "时效状态": row.get("时效状态"),
                "时效冲突": row.get("时效冲突"),
                "有效样本": row.get("有效样本"),
                "情感倾向": row.get("情感倾向"),
                "文本片段": row.get("文本片段"),
            } for row in records]

            empty_excluded = bool(
                latest is not None and latest["status"] != CAPTURE_VALID)
            freshness_conflict = bool(
                latest is not None and latest_usable is not None
                and latest["id"] != latest_usable["id"])

            page = {
                "url": page_url,
                "latest_capture": self._capture_view(latest),
                "latest_usable_capture": self._capture_view(latest_usable),
                "capture_version_used": used["version"] if used else None,
                "capture_id_used": used["capture_id"] if used else None,
                "version_source": (
                    "最新采集" if used and latest and used["capture_id"] == latest["id"]
                    else ("最近可用采集" if used else "无可用采集")),
                # 现有分析记录实际各自用了哪个采集版本，报告要能逐条对上。
                "analysis_versions": sorted({
                    str(row.get("采集版本")) for row in records
                    if row.get("采集版本")}),
                "empty_page_excluded": empty_excluded,
                "empty_page_note": (
                    f"最新采集版本{latest['version']}为{latest['status']}，未进入有效情感分布"
                    if empty_excluded
                    else "最新采集为有效文本，已进入有效情感分布"),
                "freshness_conflict": freshness_conflict,
                "valid_block_count": dataset["valid_sample_count"],
                "valid_record_count": len(valid_records),
                "analyzed_record_count": len(records),
                "analysis_inputs": analyses,
            }
            pages.append(page)
            total_valid_blocks += page["valid_block_count"]
            total_valid_records += page["valid_record_count"]
            if empty_excluded:
                empty_pages.append({
                    "url": page_url,
                    "latest_capture_version": latest["version"],
                    "latest_capture_status": latest["status"],
                    "included_in_valid_distribution": False,
                    "latest_usable_capture_version": (
                        latest_usable["version"] if latest_usable else None),
                })
            if freshness_conflict:
                conflicts.append({
                    "url": page_url,
                    "latest_capture_version": latest["version"],
                    "latest_capture_status": latest["status"],
                    "usable_capture_version": latest_usable["version"],
                    "resolution": "分析沿用最近可用版本，并标记时效冲突",
                })

        summary = self.sentiment.compute_sentiment_summary()
        return {
            "ok": True,
            "generated_at": datetime.now(timezone.utc)
                .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "scope": url or "all",
            "pages": pages,
            "totals": {
                "page_count": len(pages),
                "valid_block_count": total_valid_blocks,
                "valid_analyzed_records": total_valid_records,
                "sentiment_valid_records": summary["valid_analyzed_records"],
                "empty_page_count": len(empty_pages),
                "freshness_conflict_count": len(conflicts),
            },
            "capture_versions_used": {
                page["url"]: page["capture_version_used"] for page in pages
                if page["capture_version_used"] is not None
            },
            "analysis_versions_used": {
                page["url"]: page["analysis_versions"] for page in pages
                if page["analysis_versions"]
            },
            "empty_pages": empty_pages,
            "freshness_conflicts": conflicts,
            "sentiment_distribution": {
                label: count
                for label, count in summary["sentiment_distribution"].items()
            },
            "consistency": self.consistency(),
            "conclusion": self._conclusion(pages, total_valid_blocks, total_valid_records),
        }

    @staticmethod
    def _conclusion(pages: list[dict[str, Any]], blocks: int, records: int) -> str:
        empty = [page for page in pages if page["empty_page_excluded"]]
        never_captured = [page for page in pages if page.get("latest_capture") is None]
        if not pages:
            return "没有采集记录，无法形成任何结论"
        if blocks == 0:
            parts = []
            if empty:
                parts.append(f"{len(empty)}个页面最新采集为空正文或无法获取")
            if never_captured:
                parts.append(f"{len(never_captured)}个页面尚无采集记录")
            return (
                f"{'、'.join(parts)}，情感分布为空；"
                "缺测页未凭示例内容形成结论"
            )
        text = f"基于{blocks}个有效文本块形成结论，有效分析记录{records}条"
        if empty:
            text += f"；{len(empty)}个页面最新采集为空正文或无法获取，未进入有效情感分布"
        if never_captured:
            text += f"；{len(never_captured)}个页面尚无采集记录"
        return text


capture_pipeline = CapturePipelineService()
