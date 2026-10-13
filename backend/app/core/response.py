# -*- coding: utf-8 -*-
"""
统一响应体模块（依据技术文档 5.2「统一响应体与鉴权失败语义」）

功能说明
--------
定义全站唯一的 REST 响应结构，并提供成功 / 失败两个构造助手：

    成功：{"code": 0,     "message": "ok", "data": {...}}
    失败：{"code": 2001,  "message": "不支持的文件格式，仅支持 xlsx、csv", "data": null}

所有接口返回值（含全局异常处理器的产出）都必须经本模块构造，使前端只需一套解包逻辑；
HTTP 状态码同时表达错误类别（见 core/errors.py 错误码表的 http_status 字段）。

异常说明
--------
本模块不抛异常，纯函数式构造。
"""

from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.errors import get_error

# ============================================================
# 统一响应体的「类型化信封」（供各路由以 response_model 声明）
# ============================================================
# 说明：
#   上面三个函数负责**运行时构造**响应体；下面这个泛型模型负责**静态描述**响应体结构。
#   若路由只返回 dict 而不声明 response_model，OpenAPI 文档会把 data 画成无类型的
#   object，前端无法据文档生成类型。使用 `Envelope[RegisterResponse]` 后，
#   /docs 能如实展示 {code, message, data:{...}} 的完整层级，接口契约一目了然。
_T = TypeVar("_T")


class Envelope(BaseModel, Generic[_T]):
    """统一响应体信封（技术文档 5.2）。

    字段：
        code (int): 0 表示成功；非 0 为业务错误码（见 errors.ERRORS）
        message (str): 提示文案，成功固定为 "ok"
        data (_T | None): 业务数据载荷；失败时为 None
    """

    code: int = 0
    message: str = "ok"
    data: _T | None = None


def success(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """构造成功响应体（成功码固定为 0）。

    参数：
        data (Any): 业务数据载荷，可为 dict / list / 标量；无数据时传 None
        message (str): 提示文案，默认 "ok"
    返回：
        dict：{"code": 0, "message": message, "data": data}
    """
    return {"code": 0, "message": message, "data": data}


def error(code: int, message: str | None = None) -> dict[str, Any]:
    """构造失败响应体（data 恒为 None）。

    参数：
        code (int): 业务错误码，未登记时回退 9000
        message (str | None): 自定义文案，为 None 时取错误码表默认文案
    返回：
        dict：{"code": code, "message": message, "data": None}
    """
    definition = get_error(code)
    return {"code": definition.code, "message": message or definition.message, "data": None}


def error_response(code: int, message: str | None = None) -> JSONResponse:
    """构造带 HTTP 状态码的失败响应对象（供异常处理器直接返回）。

    参数：
        code (int): 业务错误码
        message (str | None): 自定义文案
    返回：
        JSONResponse：HTTP 状态码取自错误码表，响应体为统一结构
    """
    definition = get_error(code)
    return JSONResponse(
        status_code=definition.http_status,
        content=error(code, message),
    )
