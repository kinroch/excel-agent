# -*- coding: utf-8 -*-
"""
认证路由层（A1~A4，技术文档 5.4「认证模块（/auth）」）

功能说明
--------
对外暴露四个接口，**只做三件事**：参数校验（Pydantic）→ 调用服务层 → 组装统一响应体。
不含任何业务规则（如密码强度、防枚举、限流判定），全部下沉至 `services/auth_service`。

接口清单
--------
    A1  POST /api/v1/auth/register   注册（公开）
    A2  POST /api/v1/auth/login      登录（公开，双端共用、按 role 分流）
    A3  POST /api/v1/auth/logout     登出（需登录）
    A4  GET  /api/v1/auth/me         当前用户信息（需登录）

鉴权与公开性
------------
技术文档 5.1：**公开接口仅 POST /auth/register、POST /auth/login**，其余一律需 Bearer 令牌。
因此 A2 额外注入客户端 IP（限流维度），A3/A4 挂载登录依赖；
未来所有业务接口同样必须挂载登录依赖，这是「数据隔离」的第一道闸门（技术文档 2.2）。

异常说明
--------
本层不抛业务异常，统一由 services 层抛 BizError，经 main.py 全局处理器转为
{code, message, data} 响应体，HTTP 状态码由错误码表决定。
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_current_claims, get_current_user, get_db
from app.core.response import Envelope, success
from app.core.security import TokenClaims
from app.infra.models import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
    UserBrief,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================
# A1 注册（公开）
# ============================================================
@router.post(
    "/register",
    response_model=Envelope[RegisterResponse],
    status_code=status.HTTP_201_CREATED,
    summary="A1 用户注册",
    description=(
        "普通用户自助注册（**管理员账号不开放自助注册**，由部署期 CLI 脚本创建）。\n\n"
        "**入参规则**：账号须为手机号或邮箱；密码 ≥8 位且同时包含字母与数字。\n\n"
        "**错误**：1001 该账号已注册 / 1002 密码强度不足 / 1005 账号格式不正确。"
    ),
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> dict:
    """用户注册接口。

    参数：
        payload (RegisterRequest): {username, password}
        db (Session): 数据库会话（由依赖注入，事务由服务层提交）
    返回：
        dict: 统一响应体，data 为 {id, username, role}
    异常：
        BizError(1005/1002/1001): 账号格式 / 密码强度 / 账号重复
    说明：
        HTTP 状态取 201 Created（技术文档 5.2：201 = 已创建）。
        业务成败仍以响应体 code 判定（0 为成功），HTTP 状态仅表达类别。
    """
    result = auth_service.register(db, username=payload.username, password=payload.password)
    return success(data=result)


# ============================================================
# A2 登录（公开，双端共用）
# ============================================================
@router.post(
    "/login",
    response_model=Envelope[LoginResponse],
    summary="A2 用户登录（双端共用）",
    description=(
        "用户端与管理端**共用**同一登录接口，前端按返回的 `user.role` 分流至对应入口"
        "（技术文档 5.4 A2）。\n\n"
        "**限流**：按 IP + 账号 5 次失败/分钟（1006）。\n\n"
        "**错误**：1003 账号或密码错误（防枚举统一文案）/ 1004 账号已停用 / "
        "1006 登录尝试过于频繁。"
    ),
)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    client_ip: str = Depends(get_client_ip),
) -> dict:
    """用户登录接口。

    参数：
        payload (LoginRequest): {username, password, remember}
        db (Session): 数据库会话
        client_ip (str): 客户端 IP（由依赖解析，作为限流维度的一半）
    返回：
        dict: 统一响应体，data 为 {access_token, token_type, expires_in, user}
    异常：
        BizError(1006/1003/1004): 限流 / 账号或密码错误 / 账号已停用
    """
    result = auth_service.login(
        db,
        username=payload.username,
        password=payload.password,
        remember=payload.remember,
        client_ip=client_ip,
    )
    return success(data=result)


# ============================================================
# A3 登出（需登录）
# ============================================================
@router.post(
    "/logout",
    response_model=Envelope[LogoutResponse],
    summary="A3 用户登出",
    description=(
        "将当前令牌的 jti 加入服务端内存黑名单至其自然过期，使令牌**立即失效**"
        "（AC-4.1-5）。客户端的令牌清除由前端完成（清理对应登录态存储键）。"
    ),
)
def logout(claims: TokenClaims = Depends(get_current_claims)) -> dict:
    """用户登出接口。

    参数：
        claims (TokenClaims): 令牌载荷（含 jti 与过期时刻）
    返回：
        dict: 统一响应体，data 为 {revoked: true}
    说明：
        **刻意不查库**：登出语义上应当总是成功——即使账号刚被停用或删除，
        也应允许调用方清理自己的登录态。因此这里用轻量的 get_current_claims
        而非 get_current_user（详见 deps.py「设计说明 ①」）。
    """
    result = auth_service.logout(claims)
    return success(data=result)


# ============================================================
# A4 当前用户信息（需登录）
# ============================================================
@router.get(
    "/me",
    response_model=Envelope[UserBrief],
    summary="A4 当前登录用户信息",
    description=(
        "以有效令牌换取当前用户信息，供前端刷新页面后恢复登录态并**复核角色**"
        "（避免仅凭本地存储的 role 做越权判断）。\n\n"
        "**错误**：1003 登录已失效 / 1004 账号已停用。"
    ),
)
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """查询当前登录用户信息接口。

    参数：
        current_user (User): 当前登录用户（由依赖完成令牌校验与用户载入）
        db (Session): 数据库会话
    返回：
        dict: 统一响应体，data 为 {id, username, role, status}
    说明：
        **不接受任何前端传入的 user_id 参数**——用户身份只能来自令牌
        （技术文档 2.2：api 层禁止接受前端传入的 user_id，防止横向越权）。
    """
    result = auth_service.get_user_brief(db, current_user.id)
    return success(data=result)
