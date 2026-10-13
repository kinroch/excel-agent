# -*- coding: utf-8 -*-
"""
认证模块数据契约（依据《开发技术文档 V1.0.1》5.4「认证模块（/auth）」）

功能说明
--------
定义 A1~A4 四个接口的请求体与响应体模型，作为前后端并行开发的**契约边界**：

| 编号 | 接口 | 方法 | 请求体 | 响应体 |
|------|------|------|--------|--------|
| A1 | 注册 | POST /api/v1/auth/register | RegisterRequest | RegisterResponse |
| A2 | 登录 | POST /api/v1/auth/login | LoginRequest | LoginResponse |
| A3 | 登出 | POST /api/v1/auth/logout | — | LogoutResponse |
| A4 | 当前用户 | GET  /api/v1/auth/me | — | UserBrief |

设计约定
--------
1. **本文件只做类型级约束**（必填、长度上限），不写业务规则；
2. 账号格式（手机号/邮箱）与密码强度（≥8 位且含字母与数字）由
   `services/auth_service` 校验，以便产出技术文档约定的错误码
   （1001 该账号已注册 / 1002 密码强度不足），避免 Pydantic 校验失败被
   统一处理器兜底成 9000 而丢失业务语义；
3. 所有响应体最终会被 `core/response.success()` 包装成统一响应体
   `{code, message, data}` 下发，因此模型本身只描述 `data` 字段的结构。
"""

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """A1 注册请求体（F-4.1-1 / AC-4.1-2）。

    字段：
        username (str): 账号，手机号或邮箱；具体格式在服务层校验
        password (str): 明文密码；强度要求在服务层校验
    """

    username: str = Field(..., min_length=1, max_length=64, description="账号（手机号或邮箱）")
    password: str = Field(..., min_length=1, max_length=128, description="明文密码")


class LoginRequest(BaseModel):
    """A2 登录请求体（F-4.1-2 / AC-4.1-2、AC-4.1-3）。

    字段：
        username (str): 账号
        password (str): 明文密码
        remember (bool): 是否勾选「保持登录状态」；True 时令牌有效期 7 天，
                         否则 12 小时（F-4.1-3）
    """

    username: str = Field(..., min_length=1, max_length=64, description="账号")
    password: str = Field(..., min_length=1, max_length=128, description="明文密码")
    remember: bool = Field(default=False, description="保持登录状态（7 天）")


class UserBrief(BaseModel):
    """用户简要信息（登录响应与 A4 共用）。

    字段：
        id (int): 用户 ID
        username (str): 账号
        role (str): 角色，user | admin
        status (str): 状态，active | disabled
    """

    id: int
    username: str
    role: str
    status: str


class RegisterResponse(BaseModel):
    """A1 注册响应体（技术文档 5.4：data 为 {id, username, role}）。

    说明：注册成功后不直接下发令牌，用户需在登录页显式登录
    （与 UI/UX 6.1「注册并进入 AI 数据分析」的交互一致——前端在注册成功后
    自动调用 A2 完成登录）。
    """

    id: int
    username: str
    role: str


class LoginResponse(BaseModel):
    """A2 登录响应体（技术文档 5.4）。

    字段：
        access_token (str): JWT 访问令牌
        token_type (str): 固定 "bearer"
        expires_in (int): 有效期秒数，供前端展示或预判过期
        user (UserBrief): 登录用户信息
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserBrief


class LogoutResponse(BaseModel):
    """A3 登出响应体（AC-4.1-5）。

    字段：
        revoked (bool): 服务端是否已将该令牌加入黑名单；恒为 True
    """

    revoked: bool = True
