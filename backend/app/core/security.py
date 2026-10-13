# -*- coding: utf-8 -*-
"""
安全内核模块（密码哈希 / JWT 签发校验 / 登出黑名单）

依据文档
--------
- 《开发技术文档 V1.0.1》5.2「安全设计」、8.1「认证与授权」
- 《PRD》F-4.1「注册登录」、NFR-S2「密码与令牌安全」
- 《数据库设计文档 V1.0.2》users 表（role ∈ {user, admin}、status ∈ {active, disabled}）

功能说明（三段能力，互不依赖）
------------------------------
1. **密码哈希**（hash_password / verify_password）
   使用 passlib 的 `bcrypt_sha256` 方案，而非裸 bcrypt，原因见下方「关键设计 ①」。
2. **JWT 签发与校验**（create_access_token / decode_token）
   载荷固定为 `{sub, role, exp, jti}`；有效期按「是否勾选保持登录」在 12 小时 / 7 天间切换。
3. **登出黑名单**（revoke_token / is_token_revoked / blacklist_size）
   内存级 jti 黑名单，支撑 AC-4.1-5「登出后原令牌立即失效」。

关键设计（三条硬约束，评审重点）
--------------------------------
① **为什么用 bcrypt_sha256 而不是 bcrypt**
   裸 bcrypt 算法只取前 **72 字节**作为有效输入，第 73 字节起被静默忽略。
   本系统账号允许手机号/邮箱，密码允许中文（一个 UTF-8 汉字占 3 字节），
   用户设置 25 个汉字密码即超过 72 字节 → 后段差异被吞掉，出现「不同密码也能登录」的
   严重安全问题。`bcrypt_sha256` 方案先用 SHA-256 把任意长度口令压成定长摘要，
   再交给 bcrypt，从根本上消除该截断风险；且其输出自带 `$bcrypt-sha256$` 前缀，
   与历史裸 bcrypt 哈希可共存、不会误判。

② **为什么黑名单是「内存级」而非 Redis/数据库**
   本期为单进程 uvicorn 部署（技术文档 2.2 部署形态），进程内字典即可覆盖登出场景，
   避免为「登出」引入额外中间件。代价与前提已明确记录：**多进程/多实例部署时，
   黑名单不跨进程共享**，届时需替换为 Redis 或 users 表加 token_version 字段（已在
   M1 交付说明的「待确认项」中登记）。存储结构为 `jti -> 过期时间戳`，
   而非单纯集合，目的是支持「惰性清理」——令牌自然过期后即可从黑名单中删除，防止内存无界增长。

③ **令牌无效一律用 1003 + 覆盖文案**
   过期、签名错误、格式错误、jti 已吊销，这些情况下**对外表现一致**（HTTP 401 / code 1003），
   不向调用方泄露「是过期还是被吊销」的细节，避免信息泄露；
   但**日志/文案层面区分**，便于排障与前端引导（前端统一跳登录页）。

异常说明
--------
- decode_token 在令牌无效时**始终**抛 `BizError(1003)`，由 main.py 全局处理器转为统一响应体；
- 本模块不依赖 FastAPI，是纯工具层；FastAPI 依赖项（get_current_user / require_admin）
  因需读取请求头与数据库会话，放在 `app/api/deps.py`，避免 core 层反向依赖框架与数据层。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.errors import BizError
from app.core.logging import get_logger

logger = get_logger(__name__)

# ============================================================
# 一、常量与配置
# ============================================================

# JWT 签名算法（对称密钥 HS256，密钥来自 settings.secret_key）
_JWT_ALGORITHM = "HS256"

# 密码哈希上下文：唯一方案 bcrypt_sha256（new 时使用，verify 时按哈希前缀自动识别）
# - deprecated="auto"：便于未来平滑升级方案而不影响存量用户
# - bcrypt_sha256 自带 `$bcrypt-sha256$` 前缀，passlib 会据此选择校验器
_pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")

# 黑名单惰性清理的触发阈值：条目数超过该值时才做一次全量扫描，避免每次登出都 O(n)
_BLACKLIST_CLEANUP_THRESHOLD = 256


# ============================================================
# 二、数据类型（供 api / services 层按需引用）
# ============================================================

@dataclass(frozen=True)
class IssuedToken:
    """签发结果对象。

    属性：
        token (str): Base64URL 编码的 JWT 字符串
        jti (str): 令牌唯一标识（UUID4 字符串），登出时凭它入黑名单
        expires_in (int): 有效期秒数（前端可用于倒计时/自动刷新提示）
        expires_at (datetime): 过期时刻（UTC，带时区）
    """

    token: str
    jti: str
    expires_in: int
    expires_at: datetime


@dataclass(frozen=True)
class TokenClaims:
    """校验通过的令牌载荷。

    属性：
        user_id (int): 用户主键（来自 JWT 的 sub，已转为 int）
        role (str): 角色，取值 user / admin
        jti (str): 令牌唯一标识
        expires_at (datetime): 过期时刻（UTC，带时区）
    """

    user_id: int
    role: str
    jti: str
    expires_at: datetime


# ============================================================
# 三、密码哈希
# ============================================================

def hash_password(plain_password: str) -> str:
    """对明文密码做 bcrypt_sha256 哈希。

    参数：
        plain_password (str): 用户明文密码（长度与强度已在 schema 层校验）
    返回：
        str: 形如 `$bcrypt-sha256$v=2,t=2b,r=12$<salt>$<digest>` 的哈希串
    说明：
        哈希串自带算法与参数信息，可直接写入 users.password_hash（NFR-S2）。
        本函数不做明文校验，避免与 schema 层职责重叠。
    """
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验明文密码与数据库中的哈希是否匹配。

    参数：
        plain_password (str): 待校验明文密码
        password_hash (str): users.password_hash 字段值
    返回：
        bool: 匹配返回 True；不匹配或哈希串损坏返回 False（不抛异常）
    说明：
        对「哈希串格式非法」采取「返回 False」而非抛异常，防止脏数据把 401 变成 500；
        调用方（auth_service）据此统一抛出 BizError(1003)，不区分「用户不存在」与「密码错误」，
        避免账号枚举攻击。
    """
    try:
        return _pwd_context.verify(plain_password, password_hash)
    except (ValueError, TypeError) as exc:
        # 哈希串损坏/为空/非 bcrypt 格式时走到这里，记警告并在行为上按「校验失败」处理
        logger.warning("密码校验时哈希串异常，按校验失败处理：%s", exc)
        return False


def needs_rehash(password_hash: str) -> bool:
    """判断存量哈希是否需要按当前策略重新计算。

    参数：
        password_hash (str): 数据库中的哈希串
    返回：
        bool: 需要升级（例如未来提高 bcrypt rounds）返回 True
    说明：
        本期不做自动重算，仅预留接口：登录成功后可调用本函数判断，
        若为 True 则在用户无感知的情况下写回新哈希。本期默认策略不变，恒返回 False 亦可接受。
    """
    try:
        return _pwd_context.needs_update(password_hash)
    except (ValueError, TypeError):
        return False


# ============================================================
# 四、JWT 签发与校验
# ============================================================

def _resolve_expire_seconds(remember: bool) -> int:
    """根据「保持登录」开关解析令牌有效期（秒）。

    参数：
        remember (bool): True=保持登录（7 天），False=默认（12 小时）
    返回：
        int: 有效期秒数
    说明：
        取值来自 settings（F-4.1-3），不写死数字，便于按环境调整。
    """
    hours = settings.remember_token_expire_hours if remember else settings.access_token_expire_hours
    return hours * 3600


def create_access_token(user_id: int, role: str, remember: bool = False) -> IssuedToken:
    """签发 JWT 访问令牌。

    参数：
        user_id (int): 用户主键
        role (str): 角色，user / admin（写入载荷供鉴权使用，避免每请求查库）
        remember (bool): 是否勾选「保持登录」
    返回：
        IssuedToken: 含 token 串、jti、有效期秒数、过期时刻
    说明：
        - `sub` 按 JWT 规范必须是字符串，故写入时 str(user_id)、解析时 int(...) 回转；
        - `jti` 使用 UUID4，保证同一用户多次登录产生不同标识，登出可精确吊销单枚令牌；
        - `exp` 采用 UTC 时间戳，避免服务器时区差异导致的提前/滞后过期。
    """
    expire_seconds = _resolve_expire_seconds(remember)
    expires_at = datetime.now(UTC) + timedelta(seconds=expire_seconds)
    jti = str(uuid.uuid4())

    payload = {
        "sub": str(user_id),   # 主体：用户 ID（字符串形式）
        "role": role,          # 角色：user / admin
        "jti": jti,            # 令牌唯一标识：登出吊销依据
        "exp": int(expires_at.timestamp()),  # 过期时间戳（UTC 秒）
        "iat": int(datetime.now(UTC).timestamp()),  # 签发时间
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)
    return IssuedToken(token=token, jti=jti, expires_in=expire_seconds, expires_at=expires_at)


def decode_token(token: str) -> TokenClaims:
    """校验并解析 JWT，返回结构化载荷。

    参数：
        token (str): 请求头中提取的 JWT 串（不含 "Bearer " 前缀）
    返回：
        TokenClaims: 校验通过的载荷对象
    异常：
        BizError(1003): 令牌为空 / 签名错误 / 已过期 / 格式非法 / 缺少必要字段
    说明：
        所有失败路径对外统一为 1003（HTTP 401）——见模块 docstring「关键设计 ③」，
        本函数在抛出前会记录 warning 级日志并附具体原因，便于排障。
    """
    if not token:
        raise BizError(1003, "未提供登录凭证，请先登录")

    try:
        # options 显式要求 exp 必填校验；算法白名单防止 alg=none 降级攻击
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[_JWT_ALGORITHM],
            options={"require_exp": True, "require_sub": True},
        )
    except JWTError as exc:
        # JWTError 覆盖：过期(ExpiredSignatureError)、签名不符、格式非法等
        logger.warning("JWT 校验失败：%s", exc)
        raise BizError(1003, "登录已失效，请重新登录") from exc

    # ---- 必要字段存在性校验（防止伪造/残缺口令载荷） ----
    sub = payload.get("sub")
    role = payload.get("role")
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not sub or not role or not jti or exp is None:
        logger.warning("JWT 载荷缺少必要字段：sub=%s role=%s jti=%s", sub, role, jti)
        raise BizError(1003, "登录凭证不完整，请重新登录")

    try:
        user_id = int(sub)
        expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
    except (ValueError, TypeError, OSError) as exc:
        logger.warning("JWT 载荷字段类型异常：sub=%s exp=%s", sub, exp)
        raise BizError(1003, "登录凭证不合法，请重新登录") from exc

    # ---- 吊销检查：登出后的令牌即使未过期也必须拒绝 ----
    if is_token_revoked(jti):
        logger.info("令牌已吊销（用户已登出）：jti=%s", jti)
        raise BizError(1003, "登录已失效，请重新登录")

    return TokenClaims(user_id=user_id, role=role, jti=jti, expires_at=expires_at)


# ============================================================
# 五、登出黑名单（内存级，单进程部署）
# ============================================================

class TokenBlacklist:
    """线程安全的 jti 黑名单。

    存储结构：
        dict[str, float] —— 键为 jti，值为该令牌的**过期时间戳（UTC 秒）**。
        保存过期时间而非 True，是为了能在清理时判断「该条目是否已无保留必要」。

    线程安全：
        uvicorn 的请求处理可能落在不同线程（同步依赖由线程池执行），
        故读写均以 threading.Lock 保护；锁内只做字典操作，粒度极细，不构成性能瓶颈。
    """

    def __init__(self) -> None:
        """初始化空黑名单与互斥锁。"""
        self._store: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, jti: str, expires_at: float) -> None:
        """将 jti 加入黑名单。

        参数：
            jti (str): 令牌唯一标识
            expires_at (float): 该令牌的过期时间戳（UTC 秒）；已过期则直接忽略
        返回：
            无
        说明：
            令牌若已自然过期，加入黑名单无意义（decode 阶段会因 exp 失败先拒绝），
            此处直接跳过，防止脏条目占用内存。
        """
        now = time.time()
        if expires_at <= now:
            return
        with self._lock:
            self._store[jti] = expires_at
            # 超过阈值才触发惰性清理，摊薄 O(n) 扫描成本
            if len(self._store) > _BLACKLIST_CLEANUP_THRESHOLD:
                self._purge_expired_locked(now)

    def contains(self, jti: str) -> bool:
        """判断 jti 是否在黑名单中（已过期的条目视为不在）。

        参数：
            jti (str): 令牌唯一标识
        返回：
            bool: 已被吊销返回 True
        """
        with self._lock:
            expires_at = self._store.get(jti)
        if expires_at is None:
            return False
        # 极端情况下条目尚未被清理但已过期：此处按「不在黑名单」处理（exp 校验会兜底拦截）
        return expires_at > time.time()

    def _purge_expired_locked(self, now: float) -> None:
        """清理已过期条目（调用前必须已持有锁）。

        参数：
            now (float): 当前时间戳
        返回：
            无
        """
        expired = [k for k, v in self._store.items() if v <= now]
        for key in expired:
            self._store.pop(key, None)
        if expired:
            logger.debug("黑名单惰性清理：移除 %d 条过期记录", len(expired))

    def size(self) -> int:
        """返回当前黑名单条目数（测试与监控用）。

        返回：
            int: 条目数量
        """
        with self._lock:
            return len(self._store)


# 进程内单例：整个应用共享一份黑名单
_blacklist = TokenBlacklist()


def revoke_token(jti: str, expires_at: float) -> None:
    """吊销令牌（登出时调用）。

    参数：
        jti (str): 令牌唯一标识
        expires_at (float): 令牌过期时间戳（UTC 秒）
    返回：
        无
    说明：
        幂等：重复吊销同一 jti 不报错。过期时间由调用方从 decode_token 的 TokenClaims 传入。
    """
    _blacklist.add(jti, expires_at)
    logger.info("令牌已加入黑名单：jti=%s", jti)


def is_token_revoked(jti: str) -> bool:
    """查询令牌是否已被吊销。

    参数：
        jti (str): 令牌唯一标识
    返回：
        bool: 已吊销返回 True
    """
    return _blacklist.contains(jti)


def blacklist_size() -> int:
    """返回当前黑名单条目数（供健康检查/测试断言）。

    返回：
        int: 条目数量
    """
    return _blacklist.size()
