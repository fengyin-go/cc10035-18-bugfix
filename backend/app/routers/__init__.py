"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import capture, module_entries, resource

# 固定子路径必须先于通用 /api/{module} 注册，
# 否则 /api/capture/text-source 会被当成模块 capture 的详情请求。
ROUTERS = [capture, module_entries, resource]
