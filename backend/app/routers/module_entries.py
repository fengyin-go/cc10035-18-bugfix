"""模块级入口别名：文本分块 / 情感标注 / 种子调度 直接挂到各自模块路径下。

通用资源路由是 /api/{module}/{entry_id}，固定子路径必须先注册才能命中，
所以这个路由要排在 resource 之前。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services.capture_pipeline import capture_pipeline


router = APIRouter(prefix="/api", tags=["模块入口"])


class SegmentPayload(BaseModel):
    url: str
    raw_html: str | None = None
    error: str | None = None
    http_status: int | None = None
    fetched_at: str | None = None
    task_id: int | None = None


class SegmentAnalyzePayload(BaseModel):
    block_ids: list[str] = Field(default_factory=list)
    url: str | None = None


# ------------------------------------------------------------------ 文本分块
@router.post("/text_segment/segment")
def segment(payload: SegmentPayload) -> dict[str, Any]:
    """按采集片段切分一次实际采集；空正文返回 0 块。"""
    return capture_pipeline.capture_page(
        payload.url, payload.raw_html,
        error=payload.error, http_status=payload.http_status,
        fetched_at=payload.fetched_at, task_id=payload.task_id,
    )


@router.get("/text_segment/dataset")
def dataset(
    url: str | None = Query(default=None),
    capture_id: int | None = Query(default=None),
    include_legacy: bool = Query(default=True),
) -> dict[str, Any]:
    """文本来源校验数据集。"""
    return capture_pipeline.text_source_dataset(
        url=url, capture_id=capture_id, include_legacy=include_legacy)


@router.get("/text_segment/source-check")
def source_check(url: str = Query(...)) -> dict[str, Any]:
    """按页面核对文本来源：最新采集版本、有效块和待补采状态。"""
    dataset = capture_pipeline.text_source_dataset(url=url)
    report = capture_pipeline.build_report(url)
    page = report["pages"][0] if report["pages"] else {}
    backfill_item = capture_pipeline.backfill_list()
    tracked = next(
        (item for item in backfill_item["items"] if item.get("URL") == url), None)
    return {
        "ok": True,
        "url": url,
        "latest_capture": dataset.get("latest_capture"),
        "latest_capture_included": dataset.get("latest_capture_included"),
        "valid_sample_count": dataset["valid_sample_count"],
        "stale_sample_count": dataset["stale_sample_count"],
        "capture_versions": dataset["capture_versions"],
        "version_source": page.get("version_source"),
        "empty_page_excluded": page.get("empty_page_excluded"),
        "freshness_conflict": page.get("freshness_conflict"),
        "backfill_status": tracked.get("状态") if tracked else None,
    }


# ------------------------------------------------------------------ 情感标注
@router.post("/sentiment_tag/analyze")
def analyze(payload: SegmentAnalyzePayload) -> dict[str, Any]:
    """情感标注入口：只接受文本来源校验数据集里的有效块。"""
    if payload.url:
        return capture_pipeline.analyze_page(payload.url)
    return capture_pipeline.analyze_blocks(payload.block_ids)


@router.get("/sentiment_tag/summary")
def summary() -> dict[str, Any]:
    return capture_pipeline.sentiment_summary()


@router.get("/sentiment_tag/consistency")
def consistency() -> dict[str, Any]:
    return capture_pipeline.consistency()


# ------------------------------------------------------------------ 种子调度
@router.get("/feed_scheduler/backfill")
def backfill(status: str | None = Query(default=None)) -> dict[str, Any]:
    return capture_pipeline.backfill_list(status)
