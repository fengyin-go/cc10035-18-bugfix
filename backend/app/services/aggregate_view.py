"""聚合视图领域服务：管理抽取结果的多维聚合视图。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


CHART_TYPES = ["bar", "line", "pie", "scatter", "heatmap", "table"]


class AggregateViewService(BaseModuleService):
    MODULE = "aggregate_view"
    ENTITY = "视图配置"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["视图名称", "统计维度", "聚合字段", "时间范围", "刷新间隔", "图表类型", "数据来源"]
    REQUIRED_FIELDS = ["视图名称", "统计维度", "聚合字段"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用视图": "草稿", "停用视图": "已启用"}
    NEGATIVE_ACTIONS = ["停用视图"]

    def aggregate_entity_distribution(self) -> dict[str, Any]:
        """消费实体抽取的分布数据，生成聚合视图。"""
        from app.services.entity_extract import EntityExtractService
        ees = EntityExtractService()
        dist = ees.compute_entity_distribution()
        return {
            "view_name": "实体类型分布",
            "chart_type": "pie",
            "data_source": "entity_extract",
            "dimensions": list(dist.keys()),
            "values": list(dist.values()),
            "total_entities": sum(dist.values()),
        }

    def aggregate_sentiment_trend(self) -> dict[str, Any]:
        """消费情感标注的分布数据；与有效样本口径保持一致。"""
        from app.services.sentiment_tag import SentimentTagService
        sts = SentimentTagService()
        summary = sts.compute_sentiment_summary()
        return {
            "view_name": "情感趋势",
            "chart_type": "bar",
            "data_source": "sentiment_tag",
            "sentiment_distribution": summary["sentiment_distribution"],
            "total_records": summary["valid_analyzed_records"],
            "effective_sample_count": summary["effective_sample_count"],
            "unanalyzed_blocks": len(summary["unanalyzed_valid_blocks"]),
            "excluded_records": summary["excluded_records"],
        }

    def aggregate_quality_metrics(self) -> dict[str, Any]:
        """消费质量检查的运行结果。"""
        from app.services.quality_check import QualityCheckService
        qcs = QualityCheckService()
        pipeline = qcs.run_quality_pipeline()
        return {
            "view_name": "质量指标聚合",
            "chart_type": "bar",
            "data_source": "quality_check",
            "total_checks": pipeline.get("total_checks", 0),
            "passed_checks": pipeline.get("passed_checks", 0),
            "pass_ratio": pipeline.get("pass_ratio", 0.0),
        }

    def build_dashboard(self) -> dict[str, Any]:
        """构建完整仪表盘：聚合所有模块的快照数据。"""
        panels = [
            self.aggregate_entity_distribution(),
            self.aggregate_sentiment_trend(),
            self.aggregate_quality_metrics(),
        ]
        return {
            "ok": True,
            "dashboard_name": "语义抽取运营看板",
            "panel_count": len(panels),
            "panels": panels,
            "generated_at": "2026-11-19T10:00:00Z",
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        try:
            base["dashboard"] = self.build_dashboard()
        except Exception:
            base["dashboard"] = {"ok": False}
        return base