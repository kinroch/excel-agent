# -*- coding: utf-8 -*-
"""
路由层公共依赖（技术文档 3.1 api/deps.py）。

职责：集中定义 FastAPI 依赖项，供各路由函数以 Depends(...) 复用。

M0 阶段已提供：
    - get_db：数据库会话依赖（转导出 infra.db.get_db，供路由层统一引用）

后续里程碑追加（M1 起）：
    - get_current_user  ：解析 JWT 得到当前用户（401/1003 语义）
    - require_admin     ：要求 role=admin，否则 1403 无权限（AC-4.1-4）
    - get_pagination    ：统一分页参数（page/page_size，技术文档 5.1）
"""

from app.infra.db import get_db as get_db  # noqa: F401  —— 转导出，统一依赖入口
