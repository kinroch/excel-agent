# -*- coding: utf-8 -*-
"""
安全工具模块（JWT 签发校验 / 密码哈希 / 角色依赖）

阶段边界说明（重要）
--------------------
本模块在 **M0 环境与骨架** 阶段仅建立文件与接口占位，函数体统一抛 NotImplementedError，
真正的实现安排在 **M1 认证与权限** 阶段（A1~A4、JWT、角色依赖、双端守卫），
以避免在骨架阶段写入半成品逻辑、造成后续返工。

M1 将在此文件实现（依据技术文档 5.2 / 8.1）：
    - hash_password / verify_password   ：bcrypt 哈希与校验（NFR-S2）
    - create_access_token               ：JWT 签发，载荷 {sub, role, exp}，支持「保持登录」7 天
    - decode_token                      ：JWT 校验，含过期与签名错误处理
    - revoke_token / is_revoked         ：登出 jti 内存黑名单（AC-4.1-5）
    - require_user / require_admin      ：FastAPI 依赖，角色守卫（AC-4.1-4）
"""

from typing import NoReturn


def _not_implemented(feature: str) -> NoReturn:
    """统一占位异常。

    参数：
        feature (str): 待实现功能名
    异常：
        NotImplementedError：始终抛出，提示该能力归属的里程碑
    """
    raise NotImplementedError(f"【M0 骨架占位】{feature} 将在 M1 认证与权限阶段实现")


def hash_password(plain_password: str) -> str:
    """【M1 实现】对明文密码做 bcrypt 哈希。

    参数：
        plain_password (str): 用户明文密码
    返回：
        str: bcrypt 哈希串
    """
    _not_implemented("密码哈希 hash_password")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """【M1 实现】校验明文密码与哈希是否匹配。"""
    _not_implemented("密码校验 verify_password")


def create_access_token(user_id: int, role: str, remember: bool = False) -> str:
    """【M1 实现】签发 JWT 访问令牌。"""
    _not_implemented("JWT 签发 create_access_token")


def decode_token(token: str) -> dict:
    """【M1 实现】校验并解析 JWT，返回载荷 {sub, role, exp}。"""
    _not_implemented("JWT 校验 decode_token")
