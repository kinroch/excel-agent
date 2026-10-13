# -*- coding: utf-8 -*-
"""
认证服务层（A1~A4 的业务编排与事务边界）

依据文档
--------
- 《开发技术文档 V1.0.1》3.1「分层规约」：services 层持有事务边界，api 层只做协议转换
- 《开发技术文档 V1.0.1》5.4「认证模块（/auth）」：A1 注册 / A2 登录 / A3 登出 / A4 当前用户
- 《开发技术文档 V1.0.1》5.1：登录限流（IP + 账号 5 次/分钟）
- 《PRD》F-4.1-1~4、AC-4.1-2/3/4/5、AC-4.9-2
- 《UI/UX 设计文档》6.1 / 6.2：双端登录与异常文案

功能说明
--------
本模块是「认证」这一业务能力的唯一实现处，对外提供四个函数：

    register(db, username, password)            → 注册（A1）
    login(db, username, password, remember)     → 登录（A2）
    logout(claims)                              → 登出（A3）
    get_user_brief(db, user_id)                 → 当前用户信息（A4）

分层职责界定
------------
- **校验顺序**（注册）：账号格式 → 密码强度 → 账号唯一性。
  先做「不查库」的纯格式校验，再做需要访问数据库的唯一性校验，
  既省一次往返，也让错误提示更贴近用户输入位置。
- **事务边界**：写操作（注册、登录时更新 last_login_at）在本层显式 commit；
  api 层不感知事务。查询失败不写库，不产生副作用。
- **防枚举**：登录时「账号不存在」与「密码错误」对外统一为 1003，不泄露账号是否存在
  （AC-4.1-3）。注意 1004（账号已停用）**是刻意区分**的——PRD AC-4.9-2 要求停用账号
  登录时给出明确提示，此处不属枚举风险（攻击者已持有正确密码）。

异常说明
--------
本模块统一抛 `core.errors.BizError`，由 main.py 全局处理器转为统一响应体：
    1001 该账号已注册    1002 密码强度不足   1003 账号或密码错误
    1004 账号已停用      1005 账号格式不正确 1006 登录尝试过于频繁
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.logging import get_logger
from app.core.security import (
    TokenClaims,
    create_access_token,
    hash_password,
    revoke_token,
    verify_password,
)
from app.infra.models import User, utc_now_iso
from app.infra.ratelimit import build_key, login_limiter
from app.schemas.auth import (
    LoginResponse,
    LogoutResponse,
    RegisterResponse,
    UserBrief,
)

logger = get_logger(__name__)


# ============================================================
# 一、入参校验工具（纯函数，不查库、无副作用）
# ============================================================

# 中国大陆手机号：1 开头，第二位 3~9，共 11 位
_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")

# 邮箱：允许常见的 local@domain.tld 形态（宽松但足以拦截明显错误输入）
_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# 密码强度：至少 8 位，且同时包含「字母」与「数字」（附录 A 1002 的口径）
_PASSWORD_MIN_LENGTH = 8
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")


def is_valid_account(username: str) -> bool:
    """判断账号是否为合法的手机号或邮箱。

    参数：
        username (str): 用户输入的账号
    返回：
        bool: 命中手机号或邮箱任一种格式即为 True
    说明：
        账号口径来自技术文档 5.4 A1「username（手机号或邮箱格式校验）」与数据库设计文档
        users.username 注释「账号（手机号/邮箱）」。
        **对外公开**：`scripts/init_admin.py` 创建管理员时复用同一口径，
        避免 CLI 与服务层各写一份校验规则造成口径漂移。
    """
    account = (username or "").strip()
    return bool(_PHONE_PATTERN.match(account) or _EMAIL_PATTERN.match(account))


def is_strong_password(password: str) -> bool:
    """判断密码是否满足强度要求（≥8 位且同时含字母与数字）。

    参数：
        password (str): 明文密码
    返回：
        bool: 满足强度返回 True
    说明：
        与前端「密码强度条」（UI/UX 组件规范 .strength）使用**同一口径**，
        保证前后端判定一致，不会出现「前端满格、后端拒绝」的割裂体验。
        **对外公开**：`scripts/init_admin.py` 沿用同一强度规则（管理员密码不应弱于普通用户）。
    """
    if not password or len(password) < _PASSWORD_MIN_LENGTH:
        return False
    return bool(_HAS_LETTER.search(password) and _HAS_DIGIT.search(password))


def _to_brief(user: User) -> UserBrief:
    """把 ORM 用户对象转换为对外的简要信息模型。

    参数：
        user (User): 数据库用户对象
    返回：
        UserBrief: 含 id / username / role / status（**不含 password_hash**）
    说明：
        显式逐字段构造，而非 `model_validate(user)`，目的是从代码层面杜绝
        密码哈希被无意序列化下发的风险（NFR-S2）。
    """
    return UserBrief(id=user.id, username=user.username, role=user.role, status=user.status)


# ============================================================
# 二、A1 注册
# ============================================================

def register(db: Session, username: str, password: str) -> RegisterResponse:
    """注册普通用户（A1 / F-4.1-1 / AC-4.1-2）。

    参数：
        db (Session): 数据库会话（事务由本函数提交）
        username (str): 账号，须为手机号或邮箱
        password (str): 明文密码，须 ≥8 位且含字母与数字
    返回：
        RegisterResponse: {id, username, role}
    异常：
        BizError(1005): 账号格式不正确
        BizError(1002): 密码强度不足
        BizError(1001): 该账号已注册
    说明：
        - **角色固定为 user**：技术文档 5.4 与 UI/UX 6.2 均明确「管理员账号不开放自助注册」，
          管理员由 `scripts/init_admin.py` CLI 创建（技术文档 10.2 步骤 3）；
        - 唯一性依赖 users 表的唯一索引 uk_users_username：先做一次可读性更好的
          「查重」提示，同时捕获 IntegrityError 兜底并发注册竞态，
          两者返回同一错误码 1001，调用方无感。
    """
    account = (username or "").strip()

    # ---- 第 1 步：账号格式（纯格式校验，不查库） ----
    if not is_valid_account(account):
        raise BizError(1005)

    # ---- 第 2 步：密码强度（纯格式校验，不查库） ----
    if not is_strong_password(password):
        raise BizError(1002)

    # ---- 第 3 步：账号唯一性（查库；给出可读提示） ----
    exists = db.execute(select(User.id).where(User.username == account)).scalar_one_or_none()
    if exists is not None:
        raise BizError(1001)

    # ---- 第 4 步：落库 ----
    now = utc_now_iso()
    user = User(
        username=account,
        password_hash=hash_password(password),
        role="user",       # 自助注册恒为普通用户
        status="active",   # 新账号默认可用
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # 并发注册：两个请求同时通过「查重」后，唯一索引只会放行一个
        db.rollback()
        logger.warning("注册写入触发唯一索引冲突（并发注册）：%s", exc)
        raise BizError(1001) from exc

    db.refresh(user)
    logger.info("用户注册成功：id=%s username=%s", user.id, user.username)
    return RegisterResponse(id=user.id, username=user.username, role=user.role)


# ============================================================
# 三、A2 登录
# ============================================================

def login(db: Session, username: str, password: str, remember: bool, client_ip: str) -> LoginResponse:
    """登录（A2 / F-4.1-2 / AC-4.1-2、AC-4.1-3、AC-4.9-2）。

    参数：
        db (Session): 数据库会话
        username (str): 账号
        password (str): 明文密码
        remember (bool): 是否勾选「保持登录状态」；True → 7 天，False → 12 小时
        client_ip (str): 客户端 IP，用于登录限流维度（技术文档 5.1）
    返回：
        LoginResponse: {access_token, token_type, expires_in, user}
    异常：
        BizError(1006): 登录尝试过于频繁（IP + 账号 5 次/分钟）
        BizError(1003): 账号或密码错误（防枚举统一文案）
        BizError(1004): 账号已停用
    说明：
        **双端共用本接口，按返回的 user.role 由前端分流**（技术文档 5.4 A2
        「双端共用，按 role 分流」）。后端不区分「从哪个端登录」，
        越权由前端守卫拦截 + 后端角色依赖二次校验双保险（AC-4.1-4）。
    """
    account = (username or "").strip()
    limit_key = build_key(client_ip, account)

    # ---- 第 1 步：限流预检查（在验密之前拦截，避免为爆破请求消耗 bcrypt 算力） ----
    decision = login_limiter.is_blocked(limit_key)
    if decision.blocked:
        logger.warning(
            "登录被限流拦截：ip=%s account=%s 窗口内失败=%d 次",
            client_ip, account, decision.failure_count,
        )
        raise BizError(1006, f"登录尝试过于频繁，请 {decision.retry_after} 秒后再试")

    # ---- 第 2 步：查用户 ----
    user = db.execute(select(User).where(User.username == account)).scalar_one_or_none()

    # ---- 第 3 步：验密（账号不存在与密码错误统一 1003，防枚举） ----
    password_ok = False
    if user is not None:
        password_ok = verify_password(password, user.password_hash)
    if user is None or not password_ok:
        # 登记一次失败：攻击者猜错会累积计数，合法用户登对时立即清零
        login_limiter.record_failure(limit_key)
        logger.info("登录失败：ip=%s account=%s 原因=%s", client_ip, account,
                    "账号不存在" if user is None else "密码错误")
        raise BizError(1003)

    # ---- 第 4 步：账号状态（AC-4.9-2：停用账号登录被拒并给出明确提示） ----
    if user.status != "active":
        login_limiter.record_failure(limit_key)
        logger.info("登录被拒（账号已停用）：id=%s account=%s", user.id, account)
        raise BizError(1004)

    # ---- 第 5 步：签发令牌 ----
    issued = create_access_token(user_id=user.id, role=user.role, remember=remember)

    # ---- 第 6 步：更新最近登录时间（写操作，本层提交） ----
    now = utc_now_iso()
    user.last_login_at = now
    user.updated_at = now
    db.commit()

    # ---- 第 7 步：成功后清零限流计数（避免误伤合法用户） ----
    login_limiter.reset(limit_key)

    logger.info("登录成功：id=%s account=%s role=%s 有效期=%d秒",
                user.id, user.username, user.role, issued.expires_in)
    return LoginResponse(
        access_token=issued.token,
        token_type="bearer",
        expires_in=issued.expires_in,
        user=_to_brief(user),
    )


# ============================================================
# 四、A3 登出
# ============================================================

def logout(claims: TokenClaims) -> LogoutResponse:
    """登出（A3 / F-4.1-2 / AC-4.1-5）。

    参数：
        claims (TokenClaims): 当前请求已解析出的令牌载荷（由 deps.get_current_user 提供）
    返回：
        LogoutResponse: {revoked: True}
    说明：
        登出 = 服务端把该令牌的 jti 加入内存黑名单至自然过期（技术文档 5.1/5.2）。
        客户端的令牌删除由前端完成（UI/UX 3.2：清除对应存储键）；
        服务端这一步的意义在于——**即使客户端令牌被窃取，也已立即失效**。
        时间戳取自令牌自身的过期时刻，因此黑名单条目的存活期与令牌一致，不会提前释放。
    """
    revoke_token(claims.jti, claims.expires_at.timestamp())
    logger.info("登出成功：user_id=%s jti=%s", claims.user_id, claims.jti)
    return LogoutResponse(revoked=True)


# ============================================================
# 五、A4 当前用户信息
# ============================================================

def resolve_current_user(db: Session, user_id: int) -> User:
    """按令牌中的用户 ID 解析出当前用户实体（鉴权链路的可信来源）。

    参数：
        db (Session): 数据库会话
        user_id (int): 用户 ID（来自令牌 sub，**不接受前端传入**）
    返回：
        User: ORM 用户对象（调用方可读 role / status，但**不得下发 password_hash**）
    异常：
        BizError(1003): 用户不存在或已停用
    说明：
        **为什么每个请求都查库、而不是只信任令牌里的 role**
        令牌签发后无法撤回（除黑名单），若管理员在此期间停用了账号，
        仅凭令牌载荷判断会让「停用」在令牌过期前失效（最长 7 天）。
        查库使**停用即时生效**（AC-4.9-2 的安全延伸），且本地 SQLite 单表主键查询成本极低。
        角色同样以库中值为准（防「降权后旧令牌仍具管理员权限」）。
    """
    user = db.get(User, user_id)
    if user is None:
        logger.warning("解析当前用户失败：令牌有效但用户不存在 id=%s", user_id)
        raise BizError(1003, "登录已失效，请重新登录")
    if user.status != "active":
        logger.info("解析当前用户失败：账号已停用 id=%s", user_id)
        raise BizError(1004)
    return user


def get_user_brief(db: Session, user_id: int) -> UserBrief:
    """查询当前登录用户的简要信息（A4 / F-4.1-3）。

    参数：
        db (Session): 数据库会话
        user_id (int): 用户 ID（来自令牌 sub，**不接受前端传入**）
    返回：
        UserBrief: {id, username, role, status}
    异常：
        BizError(1003): 用户不存在
        BizError(1004): 账号已停用
    说明：
        前端在刷新页面后用本接口「以令牌换用户信息」，用于恢复登录态并复核角色，
        避免仅凭 localStorage 中的 role 字段做越权判断（UI/UX 3.2 守卫的可信来源）。
    """
    return _to_brief(resolve_current_user(db, user_id))
