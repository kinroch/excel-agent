# -*- coding: utf-8 -*-
"""
管理员账号初始化 CLI（M1 认证与权限 / 技术文档 10.2 部署步骤 3）

功能说明
--------
提供**唯一的管理员账号创建入口**。技术文档 5.4 与 UI/UX 6.2 均明确：
「管理员账号不开放自助注册」——因此 A1 注册接口恒将 role 置为 user，
管理员只能由本脚本在部署期创建。

行为规约
--------
    1. 幂等：账号已存在且已是管理员时，不重复创建，默认直接返回成功（退出码 0）；
    2. 防误改：账号已存在但**是普通用户**时，拒绝覆盖并提示使用 `--promote`
       （避免因账号写错而把一个普通用户的密码静默改成管理员密码）；
    3. 防误重置：管理员已存在时，**必须显式传 `--reset-password`** 才允许改密，
       避免运维重复执行脚本时无意重置线上管理员密码（本项目安全评审关注点）；
    4. 校验同源：账号格式与密码强度复用 `services.auth_service` 的公开校验函数，
       保证 CLI 与注册接口口径完全一致，不会出现「接口能注册、脚本建不了」的割裂。

用法
----
    # 交互式（密码用隐藏输入，不进 shell 历史）
    .venv/Scripts/python.exe backend/scripts/init_admin.py

    # 参数式（适合自动化部署；注意 shell 历史会记录密码，建议优先用交互式）
    .venv/Scripts/python.exe backend/scripts/init_admin.py \\
        --username admin@example.com --password "Admin12345"

    # 把已有普通用户提升为管理员
    .venv/Scripts/python.exe backend/scripts/init_admin.py \\
        --username ops@example.com --promote

    # 重置既有管理员密码（需显式确认开关）
    .venv/Scripts/python.exe backend/scripts/init_admin.py \\
        --username admin@example.com --password "NewPass123" --reset-password

退出码
------
    0 = 成功（新建 / 已存在 / 提升 / 重置）
    1 = 业务性失败（校验不通过、账号冲突、拒绝覆盖等）
    2 = 环境性失败（数据库不可写、意外异常）

安全说明
--------
- 脚本**从不打印明文密码**，也不把密码写入日志；
- 密码经 `security.hash_password`（bcrypt_sha256）哈希后落库，与注册链路同源（NFR-S2）；
- 本脚本会先执行幂等建库（init_database），保证首次部署可直接运行。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# 将 backend/ 加入模块搜索路径，使脚本可从任意工作目录被调用
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.infra.db import SessionLocal, init_database  # noqa: E402
from app.infra.models import User, utc_now_iso  # noqa: E402
from app.services.auth_service import is_strong_password, is_valid_account  # noqa: E402

# 密码最短长度提示语（与 auth_service 口径保持一致，仅用于友好提示）
_PASSWORD_HINT = "密码需 ≥8 位且同时包含字母与数字"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    参数：
        argv (list[str] | None): 参数列表；None 时取 sys.argv
    返回：
        argparse.Namespace: 含 username / password / promote / reset_password
    """
    parser = argparse.ArgumentParser(
        prog="init_admin.py",
        description="创建或维护管理员账号（管理员不开放自助注册，仅此入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--username", help="管理员账号（手机号或邮箱）；缺省时交互输入")
    parser.add_argument("--password", help="管理员密码；缺省时交互隐藏输入（推荐）")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="将已存在的普通用户提升为管理员（不修改其密码）",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="允许重置已存在管理员的密码（默认拒绝，防止重复执行时误改线上密码）",
    )
    return parser.parse_args(argv)


def _prompt_missing(username: str | None, password: str | None) -> tuple[str, str | None]:
    """对缺省参数做交互式补全。

    参数：
        username (str | None): 命令行传入的账号
        password (str | None): 命令行传入的密码
    返回：
        tuple[str, str | None]: (账号, 密码)。密码可能仍为 None（promote 场景不需要密码）
    说明：
        密码使用 getpass 隐藏输入并要求二次确认，避免明文回显与被 shell 历史记录。
    """
    if not username:
        username = input("管理员账号（手机号或邮箱）：").strip()

    if password:
        return username, password

    # promote 场景（仅提升角色）不涉及改密，此处由调用方判断是否需要密码
    first = getpass.getpass("管理员密码（输入时不回显）：")
    second = getpass.getpass("请再次输入以确认：")
    if first != second:
        print("[失败] 两次输入的密码不一致。")
        raise SystemExit(1)
    return username, first


def _create_admin(db, username: str, password: str) -> None:
    """在数据库中新建管理员账号。

    参数：
        db: SQLAlchemy 会话
        username (str): 已校验的账号
        password (str): 已校验的明文密码（落库前哈希）
    返回：
        无
    """
    now = utc_now_iso()
    admin = User(
        username=username,
        password_hash=hash_password(password),
        role="admin",
        status="active",
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    print(f"[成功] 已创建管理员：id={admin.id} username={admin.username} role=admin")


def main(argv: list[str] | None = None) -> int:
    """脚本入口：创建 / 提升 / 重置管理员账号。

    参数：
        argv (list[str] | None): 命令行参数；None 时取 sys.argv
    返回：
        int: 退出码（0 成功 / 1 业务失败 / 2 环境失败）
    """
    args = _parse_args(argv)

    # ---- 第 1 步：确保表结构存在（幂等，首次部署可直接运行） ----
    try:
        init_database()
    except Exception as exc:  # noqa: BLE001 —— 建库失败属环境问题，统一以退出码 2 结束
        print(f"[失败] 数据库初始化失败：{exc}")
        return 2

    # ---- 第 2 步：收集并校验入参 ----
    username = (args.username or "").strip()
    password = args.password

    # promote 模式不改密码，故不要求提供密码
    need_password = not args.promote
    if not username or (need_password and not password):
        try:
            username, password = _prompt_missing(
                username if username else None,
                password if password else None,
            )
        except (EOFError, KeyboardInterrupt):
            print("\n[取消] 已中断输入。")
            return 1

    if not is_valid_account(username):
        print(f"[失败] 账号格式不正确，需为手机号或邮箱：{username}")
        return 1
    if need_password and not is_strong_password(password or ""):
        print(f"[失败] {_PASSWORD_HINT}。")
        return 1

    # ---- 第 3 步：查库并分支处理 ----
    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()

        # --- 分支 A：账号不存在 → 新建管理员 ---
        if existing is None:
            if args.promote:
                print(f"[失败] --promote 要求账号已存在，但未找到：{username}")
                return 1
            _create_admin(db, username, password or "")
            return 0

        # --- 分支 B：账号已存在，且已是管理员 ---
        if existing.role == "admin":
            if not args.reset_password:
                print(
                    f"[提示] 管理员已存在（id={existing.id} username={existing.username}），"
                    "未做任何修改。如需重置密码请显式追加 --reset-password。"
                )
                return 0
            if not password:
                print("[失败] --reset-password 需要同时提供 --password 或交互输入密码。")
                return 1
            existing.password_hash = hash_password(password)
            existing.updated_at = utc_now_iso()
            db.commit()
            print(f"[成功] 已重置管理员密码：id={existing.id} username={existing.username}")
            return 0

        # --- 分支 C：账号已存在，但当前是普通用户 ---
        if not args.promote:
            print(
                f"[失败] 账号 {username} 已存在且为普通用户（role={existing.role}）。\n"
                "        为避免误改用户数据，脚本拒绝直接覆盖。\n"
                "        如确实要将其提升为管理员，请追加 --promote（不会修改其密码）。"
            )
            return 1

        existing.role = "admin"
        existing.updated_at = utc_now_iso()
        db.commit()
        print(f"[成功] 已将普通用户提升为管理员：id={existing.id} username={existing.username}")
        return 0

    except Exception as exc:  # noqa: BLE001 —— 兜底，避免脚本抛出裸堆栈
        db.rollback()
        print(f"[失败] 执行过程中出现异常：{exc}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
