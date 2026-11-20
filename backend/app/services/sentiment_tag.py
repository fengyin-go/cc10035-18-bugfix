"""情感标注领域服务：消费文本来源校验数据集中的真实块做情感分析。

规则：
- 只分析文本来源校验数据集中通过校验的块，示例块和历史块不进入结论；
- 同样的文本永远得到同样的情感结论，分析记录写回数据仓库；
- 每次分析都记录实际使用的采集版本与时效状态，并回写到待补采清单；
- 情感统计只统计有效样本，与分块清单的有效样本数保持同一口径。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.capture_registry import CAPTURE_VALID, capture_registry
from app.services.base import BaseModuleService
from app.services.text_segment import TextSegmentService
from app.store import store


SENTIMENT_LABELS = ["positive", "negative", "neutral", "mixed"]
ASPECT_SENTIMENTS = {
    "价格": ["positive", "negative", "neutral"],
    "质量": ["positive", "negative", "neutral"],
    "服务": ["positive", "negative", "neutral"],
    "交付": ["positive", "negative", "neutral"],
}
POSITIVE_HINTS = ("好", "优秀", "满意", "推荐", "值得", "稳定", "顺畅", "喜欢")
NEGATIVE_HINTS = ("差", "糟糕", "失望", "问题", "故障", "投诉", "卡顿", "贵")


def _stable_hash(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


class SentimentTagService(BaseModuleService):
    MODULE = "sentiment_tag"
    ENTITY = "情感记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录ID", "文本片段", "情感倾向", "强烈程度", "方面词", "方面情感", "标注模型"]
    REQUIRED_FIELDS = ["记录ID", "文本片段", "情感倾向"]
    STATUS_ORDER = ["已标注", "已复核", "已修正", "已忽略"]
    ACTION_RULES = {"复核标注": "已标注", "修正标注": "已标注", "忽略标注": "已标注"}
    NEGATIVE_ACTIONS = ["忽略标注"]

    def __init__(self) -> None:
        self._segments = TextSegmentService()

    def _infer_label(self, text: str) -> str:
        positives = sum(1 for word in POSITIVE_HINTS if word in text)
        negatives = sum(1 for word in NEGATIVE_HINTS if word in text)
        if positives and negatives:
            return "mixed"
        if positives:
            return "positive"
        if negatives:
            return "negative"
        # 没有明显情感词时用文本哈希稳定落点，保证同一文本结论可复现。
        return SENTIMENT_LABELS[_stable_hash(text) % len(SENTIMENT_LABELS)]

    def analyze_sentiment(self, text_segment_block_id: str) -> dict[str, Any]:
        """分析单个文本块的情感，只接受来源校验通过的真实块。"""
        resolution = self._segments.resolve_block(text_segment_block_id)
        if not resolution["found"]:
            return {"ok": False, "block_id": text_segment_block_id,
                    "reason": resolution["reason"], "verdict": resolution["verdict"]}

        if resolution["kind"] == "legacy":
            # 历史标注仍可回看出处，但不会再产生新的情感结论。
            return {
                "ok": False,
                "block_id": text_segment_block_id,
                "verdict": resolution["verdict"],
                "reason": resolution["reason"],
                "legacy_reference": True,
            }

        if resolution["verdict"] != "已校验":
            return {"ok": False, "block_id": text_segment_block_id,
                    "verdict": resolution["verdict"], "reason": resolution["reason"]}

        row = resolution["row"]
        text = str(row.get("文本内容", ""))
        if not text.strip():
            return {"ok": False, "block_id": text_segment_block_id,
                    "verdict": "空正文", "reason": "块内没有实际文本，不能形成情感结论"}

        result = self._build_record(text_segment_block_id, row, resolution)
        self._persist_result(result)
        return result

    def _build_record(
        self,
        block_id: str,
        row: dict[str, Any],
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        text = str(row.get("文本内容", ""))
        text_hash = _stable_hash(text)
        sentiment = self._infer_label(text)
        intensity = ["低", "中", "高", "极高"][text_hash % 4]
        aspects = list(ASPECT_SENTIMENTS.keys())
        aspect = next((word for word in aspects if word in text), aspects[text_hash % len(aspects)])
        aspect_sent = ASPECT_SENTIMENTS[aspect][text_hash % 3]
        record_id = f"sent-{block_id}"
        freshness = resolution.get("freshness", "最新版本")

        usable = resolution.get("valid_sample", True)
        if resolution.get("superseded_by"):
            usable = False
        record = {
            "ok": True,
            "记录ID": record_id,
            "块ID": block_id,
            "文本片段": text[:200],
            "情感倾向": sentiment,
            "强烈程度": intensity,
            "方面词": aspect,
            "方面情感": aspect_sent,
            "标注模型": "sentiment-analyzer-v3",
            "来源页面": row.get("来源页面"),
            "采集ID": row.get("采集ID"),
            "采集版本": row.get("采集版本"),
            "抓取时间": row.get("抓取时间"),
            "文本哈希": row.get("文本哈希"),
            "时效状态": freshness,
            "时效冲突": bool(resolution.get("stale_conflict")),
            "有效样本": bool(usable),
            "最新采集ID": resolution.get("latest_capture_id"),
            "最新采集版本": resolution.get("latest_capture_version"),
            "最新采集状态": resolution.get("latest_capture_status"),
            "上次可用采集版本": resolution.get("latest_usable_capture_version"),
            "分析时间": row.get("抓取时间"),
            "时效说明": resolution.get("reason"),
        }
        return record

    def _persist_result(self, record: dict[str, Any]) -> None:
        rows = store.rows(self.MODULE)
        row = next((r for r in rows if r.get("记录ID") == record["记录ID"]), None)
        if row is None:
            row = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "status": "已标注",
                "pending": True,
                "abnormal": False,
            }
            rows.append(row)
        row.update({
            "记录ID": record["记录ID"],
            "文本片段": record["文本片段"],
            "情感倾向": record["情感倾向"],
            "强烈程度": record["强烈程度"],
            "方面词": record["方面词"],
            "方面情感": record["方面情感"],
            "标注模型": record["标注模型"],
            "块ID": record["块ID"],
            "来源页面": record["来源页面"],
            "采集ID": record["采集ID"],
            "采集版本": record["采集版本"],
            "抓取时间": record["抓取时间"],
            "文本哈希": record["文本哈希"],
            "时效状态": record["时效状态"],
            "时效冲突": record["时效冲突"],
            "有效样本": record["有效样本"],
            "最新采集ID": record["最新采集ID"],
            "最新采集版本": record["最新采集版本"],
            "最新采集状态": record["最新采集状态"],
            "分析时间": record["分析时间"],
        })
        if not record["有效样本"]:
            row["abnormal"] = True

        # 分析结果回写到待补采清单，登记实际输入和时效结论。
        capture_registry.write_back_analysis(record["来源页面"], {
            "记录ID": record["记录ID"],
            "块ID": record["块ID"],
            "采集ID": record["采集ID"],
            "采集版本": record["采集版本"],
            "时效状态": record["时效状态"],
            "时效冲突": record["时效冲突"],
            "有效样本": record["有效样本"],
            "最新采集状态": record["最新采集状态"],
            "情感倾向": record["情感倾向"],
            "分析时间": record["分析时间"],
        })

    def batch_analyze(self, text_block_ids: list[str]) -> dict[str, Any]:
        """批量情感分析：只对有真实正文的块产出结论。"""
        results = [self.analyze_sentiment(bid) for bid in text_block_ids]
        ok_results = [r for r in results if r.get("ok")]
        rejected = [r for r in results if not r.get("ok")]
        return {
            "ok": True,
            "total_input": len(text_block_ids),
            "total_analyzed": len(ok_results),
            "rejected": len(rejected),
            "rejected_detail": rejected,
            "sentiment_distribution": {
                label: sum(1 for r in ok_results if r.get("情感倾向") == label)
                for label in SENTIMENT_LABELS
            },
            "results": ok_results,
        }

    def analyze_page(self, page_url: str) -> dict[str, Any]:
        """按页面分析：优先使用最新可用采集版本，并显式标记时效冲突。"""
        dataset = self._segments.build_source_dataset(url=page_url)
        block_ids = [item["block_id"] for item in dataset["blocks"]]
        result = self.batch_analyze(block_ids)
        latest = capture_registry.latest(page_url)
        latest_usable = capture_registry.latest_usable(page_url)
        backfill = capture_registry.backfill_for(page_url)
        result.update({
            "page_url": page_url,
            "dataset_valid_sample_count": dataset["valid_sample_count"],
            "dataset_block_count": dataset["block_count"],
            "capture_version_used": (
                dataset["capture_versions"][0]["version"]
                if dataset["capture_versions"] else None),
            "capture_id_used": (
                dataset["capture_versions"][0]["capture_id"]
                if dataset["capture_versions"] else None),
            "latest_capture_version": latest["version"] if latest else None,
            "latest_capture_status": latest["status"] if latest else None,
            "latest_usable_capture_version": (
                latest_usable["version"] if latest_usable else None),
            "empty_page_excluded": bool(
                latest is not None and latest["status"] != CAPTURE_VALID),
            "freshness_conflict": bool(
                latest is not None and latest_usable is not None
                and latest["id"] != latest_usable["id"]),
            "freshness_note": self._freshness_note(latest, latest_usable),
            "backfill_status": backfill.get("状态") if backfill else None,
        })
        return result

    @staticmethod
    def _freshness_note(latest: dict[str, Any] | None,
                        latest_usable: dict[str, Any] | None) -> str:
        if latest is None:
            return "该页面还没有采集记录"
        if latest["status"] == CAPTURE_VALID:
            return f"分析使用最新采集版本{latest['version']}，无时效冲突"
        if latest_usable is not None:
            return (
                f"最新采集版本{latest['version']}为{latest['status']}，"
                f"分析沿用最近可用版本{latest_usable['version']}；"
                f"该版本已不是最新采集，时效状态标记为冲突"
            )
        return (
            f"最新采集版本{latest['version']}为{latest['status']}，"
            f"该页面没有可用文本，不进入有效情感分布"
        )

    def compute_sentiment_summary(self) -> dict[str, Any]:
        """情感分布摘要：只统计来自有效块的分析记录。"""
        rows = store.rows(self.MODULE)
        dataset = self._segments.build_source_dataset()
        valid_block_ids = {item["block_id"] for item in dataset["blocks"] if item["valid_sample"]}

        dist: dict[str, int] = {}
        intensity_dist: dict[str, int] = {}
        valid_rows = []
        for row in rows:
            block_id = str(row.get("块ID") or "")
            if not block_id or block_id not in valid_block_ids:
                continue
            valid_rows.append(row)
            sent = row.get("情感倾向", "未知")
            dist[sent] = dist.get(sent, 0) + 1
            intensity_dist[row.get("强烈程度", "未知")] = \
                intensity_dist.get(row.get("强烈程度", "未知"), 0) + 1
        analyzed_blocks = {str(row.get("块ID")) for row in valid_rows}
        return {
            "total_records": len(rows),
            # 有效样本口径：与分块清单 / 文本来源数据集完全一致的那批块。
            "effective_sample_count": len(valid_block_ids),
            "valid_analyzed_records": len(valid_rows),
            "valid_analyzed_blocks": len(analyzed_blocks),
            "dataset_valid_block_count": len(valid_block_ids),
            "unanalyzed_valid_blocks": sorted(valid_block_ids - analyzed_blocks),
            "excluded_records": len(rows) - len(valid_rows),
            "sentiment_distribution": dist,
            "intensity_distribution": intensity_dist,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["sentiment_summary"] = self.compute_sentiment_summary()
        return base
