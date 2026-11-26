"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

委托复核表单有跨模块的工作流语义，单独挂一个前缀固定的路由文件，
并在资源路由之前注册，避免被 /api/{module} 的动态路径抢先匹配。
"""
from __future__ import annotations

from app.routers import delegated_review, resource

ROUTERS = [delegated_review, resource]
