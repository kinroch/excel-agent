# -*- coding: utf-8 -*-
"""
M0 环境与骨架 · 冒烟测试

覆盖的验收点
------------
1. 【AC】建表脚本可重复执行：init_database() 连续执行两次均成功，且表/索引数量稳定；
2. 统一响应体结构正确（技术文档 5.2）；
3. 错误码表与业务异常的 HTTP 语义正确（技术文档附录 A）；
4. 应用可启动且 /api/v1/health 可访问（"可运行空壳"判据）；
5. 未知路由返回统一响应体而非框架默认格式。

运行方式
--------
    cd backend
    ../../.venv/Scripts/python.exe -m pytest -q
"""

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.errors import ERRORS, BizError
from app.core.response import error, success
from app.infra.db import engine, init_database
from app.main import app

# 数据库设计文档 V1.0.2 第 5 章基线数量
EXPECTED_TABLE_COUNT = 13
EXPECTED_INDEX_COUNT = 21


def _count(sql: str) -> int:
    """执行计数查询。

    参数：
        sql (str): 返回单个整数的 SQL
    返回：
        int: 计数结果
    """
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar_one())


# ============================================================
# 一、建表脚本幂等性（M0 核心 AC）
# ============================================================
def test_init_database_is_idempotent() -> None:
    """建表脚本可重复执行：连续执行两次均不抛异常，且对象数量不变。"""
    init_database()
    init_database()  # 第二次执行不应报错（CREATE ... IF NOT EXISTS）

    table_count = _count(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    index_count = _count(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )

    assert table_count == EXPECTED_TABLE_COUNT, f"业务表数量应为 {EXPECTED_TABLE_COUNT}，实际 {table_count}"
    assert index_count == EXPECTED_INDEX_COUNT, f"显式索引数量应为 {EXPECTED_INDEX_COUNT}，实际 {index_count}"


def test_key_constraints_applied() -> None:
    """关键落地约束生效：WAL 模式、外键开关、表达式唯一索引存在。"""
    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar_one()
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar_one()

    # WAL 为库级持久设置；外键为连接级设置（由 connect 事件打开）
    assert str(journal_mode).lower() == "wal"
    assert int(foreign_keys) == 1

    # daily_statistics 采用 IFNULL 表达式唯一索引（数据库设计文档设计决策）
    with engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name='uk_stat_dim'")
        ).scalar_one()
    assert "IFNULL" in ddl.upper()


# ============================================================
# 二、统一响应体与错误码
# ============================================================
def test_response_envelope_shape() -> None:
    """统一响应体结构：成功 code=0，失败 data 恒为 None。"""
    ok_body = success(data={"a": 1})
    assert ok_body == {"code": 0, "message": "ok", "data": {"a": 1}}

    err_body = error(2001)
    assert err_body["code"] == 2001
    assert err_body["data"] is None
    assert err_body["message"] == ERRORS[2001].message


def test_error_codes_match_spec() -> None:
    """错误码表的 HTTP 语义与附录 A 一致。"""
    assert ERRORS[1003].http_status == 401  # 账号或密码错误
    assert ERRORS[1004].http_status == 403  # 账号已停用
    assert ERRORS[1403].http_status == 403  # 无权限访问
    assert ERRORS[2004].http_status == 422  # 文件解析失败
    assert ERRORS[3001].http_status == 404  # 会话不存在
    assert ERRORS[3003].http_status == 429  # 已有进行中任务
    assert ERRORS[9001].http_status == 502  # 大模型服务不可用


def test_biz_error_carries_status_and_message() -> None:
    """业务异常可携带覆盖文案，且状态码由错误码表推导。"""
    exc = BizError(2004, "第 3 个工作表为空")
    assert exc.code == 2004
    assert exc.http_status == 422
    assert exc.message == "第 3 个工作表为空"


# ============================================================
# 三、应用可启动与健康检查（可运行空壳判据）
# ============================================================
def test_health_endpoint_ok() -> None:
    """健康检查接口返回统一响应体，且数据库连通为 true。"""
    with TestClient(app) as client:  # with 语法触发 lifespan（启动期建库）
        resp = client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "ok"
    assert body["data"]["database"] is True
    assert "X-Trace-Id" in resp.headers  # trace_id 中间件生效


def test_root_endpoint_ok() -> None:
    """根路径返回服务标识，便于人工确认服务已启动。"""
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_unknown_route_uses_unified_envelope() -> None:
    """未知路由返回统一响应体（而非框架默认 {"detail": ...}）。"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/not-exist")

    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 9000
    assert body["data"] is None
