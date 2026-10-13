# -*- coding: utf-8 -*-
"""
路由层包：汇总并导出全站 API 路由（技术文档 3.1）。

规约：
    - 所有业务路由均挂载在 settings.api_prefix（默认 /api/v1）之下；
    - 各模块路由文件只做「参数校验 → 调用服务层 → 组装统一响应体」，不写业务逻辑；
    - 新增模块时在此处 include_router 一行即可。

M0 阶段仅挂载健康检查；后续里程碑按模块追加：
    M1 api/auth       M2 api/files      M3 api/tasks
    M4 api/reports    M5 api/sessions   M6 api/stats / api/admin
"""

from fastapi import APIRouter

from app.api import auth, health

# 全站 API 总路由（由 main.py 以 settings.api_prefix 为前缀挂载）
api_router = APIRouter()

# 健康检查（M0）——用于部署探活与 CI 冒烟
api_router.include_router(health.router, tags=["health"])

# 认证与权限（M1）——A1 注册 / A2 登录 / A3 登出 / A4 当前用户
api_router.include_router(auth.router)
