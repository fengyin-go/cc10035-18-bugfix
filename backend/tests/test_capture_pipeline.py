"""采集→分块→情感分析链路的回归测试。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
（pytest 也能直接收集这些用例。）
"""
from __future__ import annotations

import unittest

from app.capture_registry import CAPTURE_EMPTY, CAPTURE_UNREACHABLE, CAPTURE_VALID, capture_registry
from app.services.capture_pipeline import capture_pipeline
from app.services.sentiment_tag import SentimentTagService
from app.services.text_segment import TextSegmentService
from app.store import store


URL = "https://shop.example.com/item/42"
HTML_WITH_TEXT = """<html><head><title>详情</title><script>track()</script></head><body>
<h1>便携咖啡机 使用体验</h1>
<p>这款咖啡机做工稳定，整体体验令人满意。</p>
<ul><li>优点：清洗方便</li><li>缺点：体积略大</li></ul>
</body></html>"""


class CapturePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        capture_registry.reset()
        self.segments = TextSegmentService()
        self.sentiment = SentimentTagService()
        self.pipeline = capture_pipeline

    # ---------------------------------------------------------- 分块来源
    def test_empty_html_produces_no_blocks(self) -> None:
        result = self.segments.segment_page(URL, "")
        self.assertEqual(result["status"], CAPTURE_EMPTY)
        self.assertEqual(result["block_count"], 0)
        self.assertEqual(result["blocks"], [])
        self.assertEqual(
            [r for r in store.rows("text_segment") if r.get("来源页面") == URL], [])

    def test_script_and_title_are_not_body_text(self) -> None:
        result = self.segments.segment_page(URL, HTML_WITH_TEXT)
        texts = [block["text"] for block in result["blocks"]]
        self.assertEqual(len(texts), 4)
        self.assertIn("便携咖啡机 使用体验", texts)
        self.assertIn("这款咖啡机做工稳定，整体体验令人满意。", texts)
        for text in texts:
            self.assertNotIn("track()", text)
            self.assertNotIn("详情", text)

    def test_blocks_are_contiguous_real_fragments(self) -> None:
        result = self.segments.segment_page(URL, HTML_WITH_TEXT)
        capture = capture_registry.latest(URL)
        self.assertIsNotNone(capture)
        source_text = capture["text"]
        for block in result["blocks"]:
            start, end = int(block["start"]), int(block["end"])
            self.assertEqual(source_text[start:end], block["text"])
            self.assertEqual(len(block["text"]), int(block["char_length"]))

    def test_repeated_capture_of_same_text_reuses_block_ids(self) -> None:
        first = self.segments.segment_page(URL, HTML_WITH_TEXT)
        second = self.segments.segment_page(URL, HTML_WITH_TEXT)
        self.assertEqual(
            [b["block_id"] for b in first["blocks"]],
            [b["block_id"] for b in second["blocks"]],
        )

    # ---------------------------------------------------------- 三态区分
    def test_retry_distinguishes_empty_unreachable_and_valid(self) -> None:
        empty = self.pipeline.capture_page(URL, "<html><body></body></html>")
        self.assertEqual(empty["status"], CAPTURE_EMPTY)
        self.assertEqual(empty["block_count"], 0)

        unreachable = self.pipeline.capture_page(
            "https://shop.example.com/item/99", None, error="DNS 解析失败")
        self.assertEqual(unreachable["status"], CAPTURE_UNREACHABLE)
        self.assertEqual(unreachable["block_count"], 0)

        retried = self.pipeline.retry_capture(
            unreachable["capture"]["capture_id"], "<p>重试成功，拿到正文。</p>")
        self.assertEqual(retried["status"], CAPTURE_VALID)
        self.assertEqual(retried["capture"]["version"], 2)
        self.assertEqual(retried["retry_of"]["status"], CAPTURE_UNREACHABLE)
        self.assertEqual(retried["block_count"], 1)

    def test_empty_capture_enters_backfill_and_refill_resolves_it(self) -> None:
        self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        empty = self.pipeline.capture_page(URL, "<html><body></body></html>")
        backfill = self.pipeline.backfill_list()
        self.assertEqual(backfill["pending"], 1)
        self.assertEqual(backfill["items"][0]["状态"], "待补采")
        self.assertEqual(backfill["items"][0]["上次可用采集版本"], 1)

        refill = self.pipeline.retry_capture(
            empty["capture"]["capture_id"], "<p>补采到了新的正文。</p>")
        self.assertEqual(refill["status"], CAPTURE_VALID)
        self.assertEqual(self.pipeline.backfill_list()["pending"], 0)
        self.assertEqual(refill["backfill"]["状态"], "已解除")

    # ---------------------------------------------------------- 时效
    def test_analysis_after_empty_capture_marks_freshness_conflict(self) -> None:
        first = self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        block_ids = [block["block_id"] for block in first["blocks"]]
        self.pipeline.analyze_blocks(block_ids)

        self.pipeline.capture_page(URL, "<html><body> </body></html>")
        page = self.pipeline.analyze_page(URL)

        self.assertEqual(page["capture_version_used"], 1)
        self.assertEqual(page["latest_capture_version"], 2)
        self.assertEqual(page["latest_capture_status"], CAPTURE_EMPTY)
        self.assertTrue(page["empty_page_excluded"])
        self.assertTrue(page["freshness_conflict"])
        self.assertTrue(all(r["时效冲突"] for r in page["results"]))
        self.assertTrue(all(r["采集版本"] == "1" for r in page["results"]))

    def test_report_states_version_used_and_empty_exclusion(self) -> None:
        first = self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        self.pipeline.analyze_blocks([b["block_id"] for b in first["blocks"]])
        self.pipeline.capture_page(URL, "")

        report = self.pipeline.build_report(URL)
        page = report["pages"][0]
        self.assertEqual(page["capture_version_used"], 1)
        self.assertEqual(page["version_source"], "最近可用采集")
        self.assertTrue(page["empty_page_excluded"])
        self.assertEqual(report["capture_versions_used"], {URL: 1})
        self.assertEqual(len(report["empty_pages"]), 1)
        self.assertFalse(report["empty_pages"][0]["included_in_valid_distribution"])
        self.assertIn("未进入有效情感分布", report["conclusion"])

    # ---------------------------------------------------------- 数量对账
    def test_consistency_between_segment_and_sentiment_samples(self) -> None:
        first = self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        block_ids = [b["block_id"] for b in first["blocks"]]
        self.pipeline.analyze_blocks(block_ids)

        check = self.pipeline.consistency()
        self.assertTrue(check["ok"])
        self.assertEqual(check["values"]["块清单有效样本"], 4)
        self.assertEqual(check["values"]["情感统计有效记录"], 4)
        self.assertEqual(check["values"]["待分析有效块"], 0)

    def test_unanalyzed_valid_blocks_do_not_inflate_sentiment(self) -> None:
        self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        check = self.pipeline.consistency()
        self.assertTrue(check["ok"])
        self.assertEqual(check["values"]["块清单有效样本"], 4)
        self.assertEqual(check["values"]["情感统计有效记录"], 0)
        self.assertEqual(check["values"]["待分析有效块"], 4)

    # ---------------------------------------------------------- 空页结论
    def test_empty_page_never_yields_sentiment_conclusion(self) -> None:
        empty = self.pipeline.capture_page(URL, "<html><body></body></html>")
        self.assertEqual(empty["block_count"], 0)
        page = self.pipeline.analyze_page(URL)
        self.assertEqual(page["total_analyzed"], 0)
        self.assertEqual(page["sentiment_distribution"],
                         {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0})
        report = self.pipeline.build_report(URL)
        self.assertEqual(report["totals"]["valid_analyzed_records"], 0)
        self.assertIn("情感分布为空", report["conclusion"])
        self.assertIn("未凭示例内容形成结论", report["conclusion"])

    def test_example_block_is_rejected_by_sentiment(self) -> None:
        result = self.sentiment.analyze_sentiment("text_segment_样例_fake")
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["reason"])

    # ---------------------------------------------------------- 历史引用
    def test_legacy_block_resolves_but_is_not_valid_sample(self) -> None:
        resolution = self.segments.resolve_block("legacy-1a2b3c4d")
        self.assertTrue(resolution["found"])
        self.assertEqual(resolution["kind"], "legacy")
        self.assertEqual(resolution["verdict"], "历史引用")
        self.assertFalse(resolution["valid_sample"])

        result = self.sentiment.analyze_sentiment("legacy-1a2b3c4d")
        self.assertFalse(result["ok"])
        self.assertTrue(result["legacy_reference"])

        dataset = self.segments.build_source_dataset()
        legacy_ids = {item["block_id"] for item in dataset["legacy_references"]}
        self.assertIn("legacy-1a2b3c4d", legacy_ids)
        self.assertEqual(dataset["valid_sample_count"], 0)

    def test_legacy_reference_can_be_filtered_by_url(self) -> None:
        dataset = self.segments.build_source_dataset(
            url="https://example.com/products/widget-a")
        self.assertEqual(
            [item["block_id"] for item in dataset["legacy_references"]],
            ["legacy-1a2b3c4d"],
        )

    def test_tampered_block_fails_source_verification(self) -> None:
        first = self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        block_id = first["blocks"][0]["block_id"]
        row = next(r for r in store.rows("text_segment") if r["块ID"] == block_id)
        row["文本内容"] = "被篡改的文本"

        result = self.sentiment.analyze_sentiment(block_id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "校验失败")

    def test_discarded_block_is_excluded(self) -> None:
        first = self.pipeline.capture_page(URL, HTML_WITH_TEXT)
        block_id = first["blocks"][0]["block_id"]
        self.pipeline.analyze_blocks([block_id])
        row = next(r for r in store.rows("text_segment") if r["块ID"] == block_id)
        row["status"] = "已丢弃"

        check = self.pipeline.consistency()
        self.assertTrue(check["ok"])
        self.assertEqual(check["values"]["块清单有效样本"], 3)
        self.assertEqual(check["values"]["情感统计有效记录"], 0)


if __name__ == "__main__":
    unittest.main()
