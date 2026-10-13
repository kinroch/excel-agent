# -*- coding: utf-8 -*-
"""
M1 认证与权限 · 验收测试

覆盖范围（对齐 PRD 验收标准与技术文档 5.4 错误语义）
----------------------------------------------------
    AC-4.1-1  未登录访问受保护接口 → 401 + 1003（前端据此跳登录页）
    AC-4.1-2  合法账号密码登录成功，且只能看到自己的数据（token → /me）
    AC-4.1-3  密码错误提示「账号或密码错误」，**不泄露账号是否存在**（防枚举）
    AC-4.1-4  角色不符被拒绝（后端二次校验，1403）
    AC-4.1-5  登出后原令牌立即失效（jti 黑名单）
    AC-4.9-2  被停用账号登录被拒（1004）
    技术文档 5.1  登录限流：IP + 账号 5 次失败/分钟（1006）
    技术文档 5.4  A1 注册：1001 重复 / 1002 密码强度 / 1005 账号格式

运行方式
--------
    cd backend
    ../../.venv/Scripts/python.exe -m pytest tests/test_m1_auth.py -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import require_admin, require_user
from app.core.errors import BizError
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    is_token_revoked,
    revoke_token,
    verify_password,
)
from app.infra.db import SessionLocal
from app.infra.models import User, utc_now_iso
from app.main import app

# ============================================================
# 测试夹具与工具
# ============================================================


@pytest.fixture(scope="module")
def client():
    """模块级 TestClient（with 语法触发 lifespan，完成幂等建库）。

    产出：
        TestClient: 可发起请求的测试客户端
    """
    with TestClient(app) as test_client:
        yield test_client


def _unique_email() -> str:
    """生成全局唯一的邮箱形态账号，避免用例间互相干扰（含限流维度隔离）。"""
    return f"u{uuid.uuid4().hex[:12]}@example.com"


def _unique_phone() -> str:
    """生成一个格式合法、可读性高的手机号账号（后 8 位随机）。"""
    return "138" + uuid.uuid4().int.__str__().zfill(8)[:8]


def _register(client: TestClient, username: str, password: str = "Passw0rd123"):
    """调用注册接口（A1）。

    返回：
        requests.Response: 原始响应对象
    """
    return client.post("/api/v1/auth/register", json={"username": username, "password": password})


def _login(client: TestClient, username: str, password: str = "Passw0rd123", remember: bool = False):
    """调用登录接口（A2）。"""
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "remember": remember},
    )


def _register_and_login(client: TestClient, password: str = "Passw0rd123") -> tuple[str, str]:
    """注册并登录一个新账号。

    返回：
        tuple[str, str]: (账号, 访问令牌)
    """
    account = _unique_email()
    assert _register(client, account, password).status_code == 201
    resp = _login(client, account, password)
    assert resp.status_code == 200
    return account, resp.json()["data"]["access_token"]


def _disable_user(account: str) -> None:
    """直接把账号状态改为 disabled（模拟管理员停用，F-4.1-5）。"""
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == account)).scalar_one()
        user.status = "disabled"
        user.updated_at = utc_now_iso()
        db.commit()
    finally:
        db.close()


# ============================================================
# 一、A1 注册
# ============================================================


def test_register_success_creates_normal_user(client: TestClient) -> None:
    """注册成功：HTTP 201 + code 0，角色恒为 user，且密码以 bcrypt_sha256 哈希落库。"""
    account = _unique_email()
    resp = _register(client, account, "Passw0rd123")

    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["username"] == account
    assert body["data"]["role"] == "user"  # 管理员不开放自助注册（UI/UX 6.2）
    assert isinstance(body["data"]["id"], int)

    # 落库校验：哈希前缀正确、明文绝不入库
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == account)).scalar_one()
        assert user.password_hash.startswith("$bcrypt-sha256$")
        assert "Passw0rd123" not in user.password_hash
        assert user.status == "active"
    finally:
        db.close()


def test_register_accepts_phone_account(client: TestClient) -> None:
    """账号口径为「手机号或邮箱」，手机号同样可注册。"""
    phone = _unique_phone()
    resp = _register(client, phone)
    assert resp.status_code == 201
    assert resp.json()["data"]["username"] == phone


def test_register_duplicate_returns_1001(client: TestClient) -> None:
    """重复注册返回 1001 与附录 A 约定文案（HTTP 400）。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201

    resp = _register(client, account)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001
    assert resp.json()["message"] == "该账号已注册，请直接登录"


@pytest.mark.parametrize(
    "weak_password",
    [
        "Ab1",           # 少于 8 位
        "abcdefghij",    # 无数字
        "12345678",      # 无字母
    ],
)
def test_register_weak_password_returns_1002(client: TestClient, weak_password: str) -> None:
    """密码强度不足（<8 位 / 缺数字 / 缺字母）统一返回 1002。"""
    resp = _register(client, _unique_email(), weak_password)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1002


@pytest.mark.parametrize(
    "bad_account",
    [
        "not-an-account",     # 既非手机号也非邮箱
        "1380013800",         # 手机号少一位
        "user@",              # 邮箱缺域名
        "user@@example.com",  # 邮箱格式错误
    ],
)
def test_register_invalid_account_returns_1005(client: TestClient, bad_account: str) -> None:
    """账号格式非法返回 1005（附录 A 未覆盖，M1 段内补码，见 errors.py 补码说明）。"""
    resp = _register(client, bad_account)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1005


# ============================================================
# 二、A2 登录
# ============================================================


def test_login_success_returns_token_and_user(client: TestClient) -> None:
    """登录成功：返回 bearer 令牌、有效期与用户信息；响应体不含任何密码字段。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201

    resp = _login(client, account, "Passw0rd123")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 12 * 3600  # 未勾选保持登录 → 12 小时（F-4.1-3）
    assert data["user"]["username"] == account
    assert data["user"]["role"] == "user"
    assert data["user"]["status"] == "active"
    assert "password" not in resp.text.lower()  # 绝不泄露哈希或明文

    # 令牌可被服务端解析，且载荷完整
    claims = decode_token(data["access_token"])
    assert claims.user_id == data["user"]["id"]
    assert claims.role == "user"


def test_login_remember_extends_expiry_to_7_days(client: TestClient) -> None:
    """勾选「保持登录状态」时有效期 7 天（F-4.1-3）。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201

    resp = _login(client, account, "Passw0rd123", remember=True)
    assert resp.json()["data"]["expires_in"] == 168 * 3600


def test_login_wrong_password_and_unknown_account_are_indistinguishable(client: TestClient) -> None:
    """AC-4.1-3 防枚举：密码错误与账号不存在返回**完全相同**的状态码与文案。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201

    wrong_pwd = _login(client, account, "Wrong12345")
    unknown = _login(client, _unique_email(), "Wrong12345")

    assert wrong_pwd.status_code == unknown.status_code == 401
    assert wrong_pwd.json()["code"] == unknown.json()["code"] == 1003
    assert wrong_pwd.json()["message"] == unknown.json()["message"] == "账号或密码错误"


def test_login_disabled_account_returns_1004(client: TestClient) -> None:
    """AC-4.9-2：被停用账号登录被拒，返回 1004（HTTP 403）。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201
    _disable_user(account)

    resp = _login(client, account, "Passw0rd123")
    assert resp.status_code == 403
    assert resp.json()["code"] == 1004
    assert resp.json()["message"] == "账号已停用，请联系管理员"


# ============================================================
# 三、A3 登出 / A4 当前用户 / 令牌失效
# ============================================================


def test_me_without_token_returns_401_1003(client: TestClient) -> None:
    """AC-4.1-1：未携带令牌访问受保护接口 → 401 + 1003（前端跳登录页）。"""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003


def test_me_with_invalid_token_returns_401_1003(client: TestClient) -> None:
    """令牌非法（签名不符/格式错误）同样归一为 401 + 1003，不泄露具体原因。"""
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003


def test_me_returns_current_user(client: TestClient) -> None:
    """AC-4.1-2：持有效令牌可取回自身信息（前端刷新后恢复登录态的可信来源）。"""
    account, token = _register_and_login(client)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["username"] == account
    assert data["role"] == "user"
    assert "password" not in resp.text.lower()


def test_logout_revokes_token_immediately(client: TestClient) -> None:
    """AC-4.1-5：登出后原令牌立即失效（服务端 jti 黑名单生效）。"""
    _, token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 登出前可用
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    logout_resp = client.post("/api/v1/auth/logout", headers=headers)
    assert logout_resp.status_code == 200
    assert logout_resp.json()["data"]["revoked"] is True

    # 登出后同一令牌被拒
    after = client.get("/api/v1/auth/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["code"] == 1003


def test_logout_requires_authentication(client: TestClient) -> None:
    """登出接口本身也需登录（技术文档 5.1：公开接口仅注册与登录）。"""
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003


def test_token_becomes_invalid_after_account_disabled(client: TestClient) -> None:
    """安全延伸：账号被停用后，**已签发的旧令牌**也立即失效（每请求查库复核状态）。

    若仅信任令牌载荷，停用将在令牌过期前（最长 7 天）不生效；
    本用例锁定「停用即时生效」这一设计决策（见 auth_service.resolve_current_user）。
    """
    account, token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    _disable_user(account)

    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 1004


# ============================================================
# 四、角色守卫（AC-4.1-4 后端二次校验）
# ============================================================


def _make_user(role: str) -> User:
    """构造一个不入库的 User 实例，仅用于直接测试角色守卫依赖。"""
    return User(
        id=1,
        username="probe@example.com",
        password_hash=hash_password("Passw0rd123"),
        role=role,
        status="active",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )


def test_require_admin_rejects_normal_user() -> None:
    """AC-4.1-4：普通用户触发管理接口依赖 → 1403 无权限（HTTP 403）。"""
    with pytest.raises(BizError) as excinfo:
        require_admin(current_user=_make_user("user"))
    assert excinfo.value.code == 1403
    assert excinfo.value.http_status == 403


def test_require_user_rejects_admin() -> None:
    """AC-4.1-4 反向：管理员访问用户端业务依赖 → 1403（双端登录态互不授权）。"""
    with pytest.raises(BizError) as excinfo:
        require_user(current_user=_make_user("admin"))
    assert excinfo.value.code == 1403


def test_require_admin_passes_for_admin() -> None:
    """管理员触发管理接口依赖时正常放行。"""
    admin = _make_user("admin")
    assert require_admin(current_user=admin) is admin


# ============================================================
# 五、登录限流（技术文档 5.1：IP + 账号 5 次失败/分钟）
# ============================================================


def test_login_blocked_after_five_failures(client: TestClient) -> None:
    """连续 5 次失败后，第 6 次被限流拦截（1006 / HTTP 429）。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201

    # 前 5 次失败：均为 1003
    for _ in range(5):
        assert _login(client, account, "Wrong12345").json()["code"] == 1003

    # 第 6 次：限流生效
    blocked = _login(client, account, "Wrong12345")
    assert blocked.status_code == 429
    assert blocked.json()["code"] == 1006


def test_rate_limit_isolated_per_account(client: TestClient) -> None:
    """限流维度为「IP + 账号」：一个账号被拦截不影响另一个账号正常登录。"""
    blocked_account = _unique_email()
    other_account = _unique_email()
    assert _register(client, blocked_account).status_code == 201
    assert _register(client, other_account).status_code == 201

    for _ in range(5):
        _login(client, blocked_account, "Wrong12345")
    assert _login(client, blocked_account, "Wrong12345").json()["code"] == 1006

    # 另一账号不受影响
    assert _login(client, other_account, "Passw0rd123").status_code == 200


def test_successful_login_resets_failure_counter(client: TestClient) -> None:
    """登录成功即清零失败计数，避免合法用户被自身历史失败拖累。"""
    account = _unique_email()
    assert _register(client, account).status_code == 201

    # 先制造 3 次失败
    for _ in range(3):
        assert _login(client, account, "Wrong12345").json()["code"] == 1003

    # 登录成功 → 计数清零
    assert _login(client, account, "Passw0rd123").status_code == 200

    # 若未清零，累计到第 6 次（即此处第 3 次）就会被限流；全部为 1003 即证明已清零
    for i in range(5):
        resp = _login(client, account, "Wrong12345")
        assert resp.json()["code"] == 1003, f"第 {i + 1} 次失败被误判为限流，说明计数未清零"


# ============================================================
# 六、安全内核（bcrypt_sha256 / jti 黑名单）
# ============================================================


def test_bcrypt_sha256_prevents_72_byte_truncation() -> None:
    """密码哈希采用 bcrypt_sha256，规避裸 bcrypt 的 72 字节截断风险。

    构造一个 72 字节（24 个汉字）以上的中文密码：
    - 正确密码必须通过；
    - **仅尾部不同**的密码必须被拒绝——若使用裸 bcrypt，尾部差异会被静默丢弃而误判通过。
    """
    long_password = "数据分析密码" * 8  # 48 字节以上，远超 72 字节边界前后的差异区
    hashed = hash_password(long_password)

    assert hashed.startswith("$bcrypt-sha256$")
    assert verify_password(long_password, hashed) is True
    assert verify_password(long_password + "尾", hashed) is False


def test_token_blacklist_revocation() -> None:
    """jti 黑名单：加入后查询为真，且 decode_token 拒绝该令牌。"""
    issued = create_access_token(user_id=999, role="user", remember=False)
    assert is_token_revoked(issued.jti) is False

    revoke_token(issued.jti, issued.expires_at.timestamp())
    assert is_token_revoked(issued.jti) is True

    with pytest.raises(BizError) as excinfo:
        decode_token(issued.token)
    assert excinfo.value.code == 1003


def test_expired_token_is_rejected() -> None:
    """过期令牌被拒绝（附录 A 1003 / HTTP 401）。"""
    import time

    from jose import jwt

    from app.core.config import settings

    # 构造一个已过期 1 小时的令牌（exp 为过去时刻）
    expired = jwt.encode(
        {
            "sub": "1",
            "role": "user",
            "jti": str(uuid.uuid4()),
            "exp": int(time.time()) - 3600,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    with pytest.raises(BizError) as excinfo:
        decode_token(expired)
    assert excinfo.value.code == 1003


def test_tampered_signature_is_rejected() -> None:
    """签名被篡改的令牌被拒绝（防止伪造身份）。

    篡改位点的选择说明（这是一个真实踩到的「偶发失败」）：
        HS256 的签名是 32 字节 HMAC，base64url 无填充编码后是 **43 个字符**。
        42 个字符承载 252 比特，第 43 个字符只用**高 4 位**承载剩余 4 比特，
        低 2 位是填充位。因此改动**最后一个字符**时，只要它的高 4 位与替换字符
        相同，解码出的字节就完全一致 —— 签名依然有效，`pytest.raises` 落空，
        用例表现为随机失败（实测约 1/16 概率）。
        改为篡改**首个字符**：6 个比特全部有效，解码结果必然不同，用例确定失败。
    """
    issued = create_access_token(user_id=1, role="user", remember=False)
    # 改动签名段第一个字符（见 docstring：最后一个字符含填充位，不可用）
    head, payload_part, signature = issued.token.split(".")
    tampered_first = "A" if signature[0] != "A" else "B"
    tampered = f"{head}.{payload_part}.{tampered_first}{signature[1:]}"

    with pytest.raises(BizError) as excinfo:
        decode_token(tampered)
    assert excinfo.value.code == 1003
