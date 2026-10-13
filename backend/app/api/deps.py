# -*- coding: utf-8 -*-
"""
路由层公共依赖（技术文档 3.1 api/deps.py）

职责
----
集中定义 FastAPI 依赖项，供各路由函数以 Depends(...) 复用。所有依赖遵循
「api → services → infra」单向调用，本模块不写业务逻辑，只做**鉴权编排与上下文注入**。

已提供依赖
----------
    get_db             数据库会话（转导出 infra.db.get_db，统一依赖入口）
    get_client_ip      客户端真实 IP（供登录限流与审计日志使用）
    get_current_claims 解析 Bearer 令牌 → TokenClaims（**不查库**，供登出使用）
    get_current_user   解析令牌并载入用户实体 → User（**查库**，供业务路由使用）
    require_user       要求 role == 'user'，否则 1403（AC-4.1-4）
    require_admin      要求 role == 'admin'，否则 1403（AC-4.1-4）

设计说明（评审重点）
--------------------
① **为什么区分 get_current_claims 与 get_current_user**
   登出（A3）只需令牌自身的 jti 与过期时刻，查库纯属浪费；同时「登出」在语义上应当
   **永远成功**（即使账号刚被停用，也要允许其清理登录态），故登出走不查库的轻量依赖。
   其余业务路由一律走 get_current_user，以获得最新的用户存在性与状态。

② **为什么返回 ORM User 而不是只返回 user_id**
   业务路由普遍需要 user_id 做数据隔离（技术文档 2.2「所有用户态查询强制带 user_id 过滤」），
   管理接口还需要 role 与 status。一次查库即可满足两者，避免「先取 id 再取角色」的二次查询。
   约定：路由层**只读**该对象的 id / role / status / username，绝不序列化 password_hash。

③ **为什么用 HTTPBearer(auto_error=False) 而不是 auto_error=True**
   默认行为在缺少凭证时抛 FastAPI 的 403 HTTPException，会被全局处理器包装成
   「code 9000 + HTTP 403」，与错误码表口径不符（缺少凭证应属 401/1003）。
   关闭自动报错后，由本模块显式抛 BizError(1003)，保证 401 语义与前端拦截器一致
   （技术文档 5.2：401 → 清除登录态并跳转对应登录页带 ?from=）。
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.logging import get_logger
from app.core.security import TokenClaims, decode_token
from app.infra.db import get_db as get_db  # noqa: F401  —— 转导出，统一依赖入口
from app.infra.models import User
from app.services import auth_service

logger = get_logger(__name__)

# auto_error=False：缺少 Authorization 头时不自动抛 403，交由下方依赖显式抛 1003
_bearer_scheme = HTTPBearer(auto_error=False, description="Bearer JWT（Authorization 头）")


# ============================================================
# 一、请求上下文
# ============================================================

def get_client_ip(request: Request) -> str:
    """提取客户端真实 IP（供登录限流与审计日志使用）。

    参数：
        request (Request): 当前请求
    返回：
        str: 客户端 IP；无法识别时返回 "unknown"
    说明：
        生产环境后端在 Nginx 之后，`request.client.host` 得到的是 Nginx 的内网地址，
        真实 IP 由 Nginx 通过 `X-Forwarded-For` 透传（deploy/nginx.conf.example 已配置
        `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`）。
        XFF 为逗号分隔链，**最左侧**为最初发起方，故取第 0 段。

        **安全边界**：直接暴露后端（不经 Nginx）时，XFF 可被客户端伪造，从而绕过 IP 维度限流。
        本部署形态下后端仅监听 127.0.0.1（deploy/eda-backend.service），不存在该暴露面；
        若未来改为公网直连，需将可信代理解析逻辑替换为白名单校验。
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


# ============================================================
# 二、令牌解析与当前用户
# ============================================================

def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TokenClaims:
    """解析 Authorization 头中的 Bearer 令牌，返回载荷（不查库）。

    参数：
        credentials (HTTPAuthorizationCredentials | None): FastAPI 从请求头解析出的凭证
    返回：
        TokenClaims: 含 user_id / role / jti / expires_at
    异常：
        BizError(1003): 缺少凭证、格式非法、签名错误、已过期或已被吊销
    说明：
        decode_token 内部已完成「签名 + 过期 + 黑名单」三重校验，失败统一抛 1003
        （见 core/security.py「关键设计 ③」）。
    """
    if credentials is None or not credentials.credentials:
        raise BizError(1003, "未提供登录凭证，请先登录")
    return decode_token(credentials.credentials)


def get_current_user(
    claims: TokenClaims = Depends(get_current_claims),
    db: Session = Depends(get_db),
) -> User:
    """解析令牌并载入当前用户实体（查库，校验存在性与启用状态）。

    参数：
        claims (TokenClaims): 令牌载荷
        db (Session): 数据库会话
    返回：
        User: 当前登录用户（只读使用其 id / role / status / username）
    异常：
        BizError(1003): 用户不存在
        BizError(1004): 账号已停用
    说明：
        实际解析逻辑下沉到 `services.auth_service.resolve_current_user`，
        以保证「api 层不做业务判断」的分层规约；本依赖只负责转发。
    """
    return auth_service.resolve_current_user(db, claims.user_id)


# ============================================================
# 三、角色守卫（AC-4.1-4 双保险中的「后端二次校验」）
# ============================================================

def require_user(current_user: User = Depends(get_current_user)) -> User:
    """要求当前登录者角色为 user（普通用户端业务接口）。

    参数：
        current_user (User): 当前登录用户
    返回：
        User: 校验通过的用户对象
    异常：
        BizError(1403): 角色不符（例如管理员访问用户端业务接口）
    说明：
        双端登录态互不授权（UI/UX 3.2）。前端守卫负责体验层拦截，
        后端角色依赖负责**安全层拦截**——二者是「双保险」关系，缺一不可：
        前端守卫可被绕过（改 localStorage、直接调接口），后端守卫才是真正的边界。
    """
    if current_user.role != "user":
        logger.warning(
            "越权访问被拒（需 user 角色）：id=%s role=%s", current_user.id, current_user.role
        )
        raise BizError(1403, "无权限访问用户端功能")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求当前登录者角色为 admin（管理后台接口，AC-4.1-4）。

    参数：
        current_user (User): 当前登录用户
    返回：
        User: 校验通过的管理员对象
    异常：
        BizError(1403): 角色不符（普通用户访问管理后台）
    说明：
        技术文档 2.2 提及本依赖的别名 `admin_role_required`，二者语义完全一致，
        统一以 `require_admin` 为对外名称（附录 A 1403「无权限访问」）。
        管理后台各接口（M6 的 /admin/*）统一挂载本依赖。
    """
    if current_user.role != "admin":
        logger.warning(
            "越权访问被拒（需 admin 角色）：id=%s role=%s", current_user.id, current_user.role
        )
        raise BizError(1403, "无权限访问管理后台")
    return current_user


# 技术文档 2.2 中出现的别名，保留以方便对照文档阅读
admin_role_required = require_admin
