# -*- coding: utf-8 -*-
"""
FastAPI 应用入口（依据技术文档 3.1 main.py 职责）

功能说明
--------
组装并导出 ASGI 应用 `app`，职责包括：

1. **创建实例**：配置应用元信息与 OpenAPI 文档地址；
2. **注册中间件**：trace_id 追踪（10.3 日志排障）、CORS（开发期跨域）；
3. **注册全局异常处理器**：业务异常 / 参数校验异常 / 框架 HTTP 异常 / 未捕获异常，
   全部转换为统一响应体 {code, message, data}（技术文档 5.2）；
4. **挂载路由**：以 settings.api_prefix 为前缀挂载 api_router；
5. **生命周期**：启动时初始化日志、文件目录与数据库（幂等建表）；停止时记录日志。

启动方式（开发）
----------------
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

异常说明
--------
- 启动期 init_database() 失败会阻断进程启动（快速失败），避免服务在无表状态下运行；
- 运行时未捕获异常统一返回 9000 系统内部错误，服务端记录完整堆栈，不向客户端泄露。
"""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import api_router
from app.core.config import settings
from app.core.errors import BizError
from app.core.logging import get_logger, set_trace_id, setup_logging
from app.core.response import error_response, success
from app.infra import storage
from app.infra.db import init_database
from app.services import file_service

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期管理：启动初始化 → 运行 → 停止收尾。

    参数：
        _app (FastAPI): 应用实例（本函数内未直接使用）
    产出：
        None：yield 前后分别执行启动与停止逻辑
    """
    # ---- 启动阶段 ----
    setup_logging()
    logger.info("服务启动中：%s v%s", settings.app_name, settings.app_version)
    storage.ensure_dirs()  # 确保 uploads/ exports/ 存在
    init_database()  # 幂等建表（M0 验收 AC：建表脚本可重复执行）

    # 崩溃恢复（M2，技术文档 3.6）：上次进程中断时残留的 pending/parsing 记录
    # 已无对应后台任务，前端会永远轮询不到终态，故启动时统一置为 failed。
    # 该动作不阻断启动（内部已吞异常），失败时仅记录日志。
    recovered = file_service.fail_stale_parsing()
    if recovered:
        logger.warning("启动期崩溃恢复：已把 %d 条中断的解析记录置为失败", recovered)

    logger.info("服务启动完成，接口前缀：%s", settings.api_prefix)

    yield

    # ---- 停止阶段 ----
    logger.info("服务已停止")


# 创建 FastAPI 实例
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="通过自然语言对话完成 Excel 数据的读取、分析、统计与可视化。",
    docs_url=f"{settings.api_prefix}/docs",
    openapi_url=f"{settings.api_prefix}/openapi.json",
    lifespan=lifespan,
)


# ============================================================
# 中间件注册
# ============================================================
@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """trace_id 追踪中间件：为每个请求绑定唯一追踪标识。

    参数：
        request (Request): 当前请求
        call_next: 下游处理链
    返回：
        Response: 下游响应，附加 X-Trace-Id 响应头
    说明：
        - 优先透传调用方传入的 X-Trace-Id（便于跨服务串联）；
        - 未传时服务端生成 8 位短标识；
        - trace_id 写入 ContextVar，本次请求内所有日志自动携带（10.3）。
    """
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:8]
    set_trace_id(trace_id)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


# 跨域配置（开发期前端 Vite 独立端口访问后端时使用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 全局异常处理器（统一响应体出口）
# ============================================================
@app.exception_handler(BizError)
async def handle_biz_error(_request: Request, exc: BizError):
    """处理业务异常：按错误码表推导 HTTP 状态与文案。

    参数：
        _request (Request): 当前请求（未使用）
        exc (BizError): 业务异常
    返回：
        JSONResponse: 统一失败响应体
    """
    return error_response(exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError):
    """处理请求参数校验失败。

    参数：
        _request (Request): 当前请求
        exc (RequestValidationError): Pydantic 校验异常
    返回：
        JSONResponse: HTTP 400 + 统一失败响应体（附首个字段的错误说明）
    说明：
        附录 A 未单列「参数校验」错误码，此处沿用 9000 段的语义，
        但 HTTP 状态按技术文档 5.2 表格取 400（参数错误）。
    """
    from fastapi.responses import JSONResponse

    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", []))
    detail = first.get("msg", "参数校验失败")
    return JSONResponse(
        status_code=400,
        content={"code": 9000, "message": f"参数错误[{location}]：{detail}", "data": None},
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(_request: Request, exc: StarletteHTTPException):
    """处理框架级 HTTP 异常（如路由不存在、方法不允许）。

    参数：
        _request (Request): 当前请求
        exc (StarletteHTTPException): 框架异常，含 status_code 与 detail
    返回：
        JSONResponse: 保留原始 HTTP 状态，响应体统一为 {code:9000, message, data:None}
    """
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status_code,
        content={"code": 9000, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(Exception)
async def handle_uncaught_exception(_request: Request, exc: Exception):
    """兜底处理未捕获异常：记录堆栈，对外返回 9000 可读提示。

    参数：
        _request (Request): 当前请求
        exc (Exception): 未捕获异常
    返回：
        JSONResponse: HTTP 500 + 统一失败响应体
    说明：
        异常细节只进服务端日志，不回传客户端，避免信息泄露（NFR-S2）。
    """
    logger.exception("未捕获异常：%s", exc)
    return error_response(9000)


# ============================================================
# 路由挂载
# ============================================================
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    """根路径：返回服务标识与接口文档地址（便于人工快速确认服务已启动）。

    返回：
        dict: 统一响应体，data 含服务名、版本与文档地址
    """
    return success(
        data={
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": f"{settings.api_prefix}/docs",
        }
    )
