"""采集链路接口：采集版本、文本来源校验数据集、待补采清单和情感分析。

这些路径必须先于通用资源路由注册，否则 /api/{module}/{entry_id} 会抢先匹配。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.capture_pipeline import capture_pipeline


router = APIRouter(prefix="/api/capture", tags=["采集链路"])


class CapturePayload(BaseModel):
    url: str
    raw_html: str | None = None
    error: str | None = None
    http_status: int | None = None
    fetched_at: str | None = None
    task_id: int | None = None


class RetryPayload(BaseModel):
    capture_id: int
    raw_html: str | None = None
    error: str | None = None
    http_status: int | None = None
    fetched_at: str | None = None


class AnalyzePayload(BaseModel):
    block_ids: list[str] = Field(default_factory=list)
    url: str | None = None


class ResolvePayload(BaseModel):
    url: str


@router.post("/pages")
def capture_page(payload: CapturePayload) -> dict[str, Any]:
    """登记一次实际采集并按采集片段切分，返回该次采集的文本来源数据。"""
    return capture_pipeline.capture_page(
        payload.url,
        payload.raw_html,
        error=payload.error,
        http_status=payload.http_status,
        fetched_at=payload.fetched_at,
        task_id=payload.task_id,
    )


@router.get("/pages")
def list_captures(url: str | None = Query(default=None)) -> dict[str, Any]:
    return capture_pipeline.list_captures(url)


@router.post("/retry")
def retry_capture(payload: RetryPayload) -> dict[str, Any]:
    """采集中断后重试：空正文、无法获取和有效文本会被区分登记。"""
    return capture_pipeline.retry_capture(
        payload.capture_id,
        payload.raw_html,
        error=payload.error,
        http_status=payload.http_status,
        fetched_at=payload.fetched_at,
    )


@router.get("/text-source")
def text_source_dataset(
    url: str | None = Query(default=None),
    capture_id: int | None = Query(default=None),
    include_legacy: bool = Query(default=True),
) -> dict[str, Any]:
    """交付文本来源校验数据集。"""
    return capture_pipeline.text_source_dataset(
        url=url, capture_id=capture_id, include_legacy=include_legacy)


@router.get("/text-source/export")
def export_text_source(
    url: str | None = Query(default=None),
    capture_id: int | None = Query(default=None),
    include_legacy: bool = Query(default=True),
) -> StreamingResponse:
    rows = capture_pipeline.text_source_rows(
        url=url, capture_id=capture_id, include_legacy=include_legacy)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    columns = [
        "范围", "块ID", "来源页面", "采集ID", "采集版本", "抓取时间", "片段序号",
        "片段选择器", "块类型", "字符长度", "文本哈希", "校验结论", "有效样本",
        "时效状态", "说明", "文本内容",
    ]
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="text_source_dataset.csv"'},
    )


@router.get("/backfill")
def backfill_list(status: str | None = Query(default=None)) -> dict[str, Any]:
    """待补采清单：最近一次采集为空正文或无法获取的页面。"""
    return capture_pipeline.backfill_list(status)


@router.post("/backfill/resolve")
def resolve_backfill(payload: ResolvePayload) -> dict[str, Any]:
    return capture_pipeline.resolve_backfill(payload.url)


@router.post("/sentiment/analyze")
def analyze(payload: AnalyzePayload) -> dict[str, Any]:
    """情感分析只能消费文本来源校验数据集中的真实块。"""
    if payload.url:
        return capture_pipeline.analyze_page(payload.url)
    return capture_pipeline.analyze_blocks(payload.block_ids)


@router.get("/sentiment/summary")
def sentiment_summary() -> dict[str, Any]:
    return capture_pipeline.sentiment_summary()


@router.get("/consistency")
def consistency() -> dict[str, Any]:
    """对账：情感统计与分块清单的有效样本数量。"""
    return capture_pipeline.consistency()


@router.get("/report")
def report(url: str | None = Query(default=None)) -> dict[str, Any]:
    """采集版本使用情况与空页是否进入有效情感分布的交付报告。"""
    return capture_pipeline.build_report(url)
