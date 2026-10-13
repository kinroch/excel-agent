# -*- coding: utf-8 -*-
"""
健康检查路由（M0 环境与骨架）。

功能说明
--------
提供服务存活与依赖连通性探测接口，用于：
    1. 部署探活（Nginx / systemd / 负载均衡健康检查）；
    2. CI 冒烟测试（进程可启动、响应体结构正确）；
    3. 运维排障时快速确认数据库可连通。

接口清单
--------
    GET /api/v1/health   返回服务版本、数据库连通状态

异常说明
--------
本接口不抛业务异常：数据库不可连通时通过 data.database = false 反馈，
HTTP 状态仍为 200（探活语义），由运维侧按字段判定。
"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.response import success
from app.infra.db import check_database

router = APIRouter()


@router.get("/health", summary="服务健康检查", description="返回服务元信息与数据库连通状态")
def health_check() -> dict:
    """健康检查接口。

    参数：无
    返回：
        dict: 统一响应体，data 含 {status, version, database}
    """
    return success(
        data={
            "status": "ok",
            "version": settings.app_version,
            "database": check_database(),
        }
    )
