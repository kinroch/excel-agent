# -*- coding: utf-8 -*-
"""
建库与校验脚本（M0 环境与骨架）

功能说明
--------
1. 执行 backend/db/schema.sql，幂等创建 13 张业务表与全部索引；
2. `--verify` 模式在建库后对「实际表 / 索引集合」与「预期集合」逐项比对，
   不一致时以退出码 1 结束，可直接被 CI 引用；
3. `--rebuild` 模式先删除数据库文件再重建（仅限本地开发，部署环境禁用）。

与应用的等价性
--------------
本脚本调用的 init_database() 与 FastAPI 启动生命周期使用的是**同一个函数**，
因此「脚本建库成功」即代表「应用启动可正常建库」，不存在两套建表逻辑。

用法
----
    # 幂等建库（可重复执行，第二次执行不会报错）
    .venv/Scripts/python.exe backend/scripts/init_db.py

    # 建库并校验表/索引与基线一致
    .venv/Scripts/python.exe backend/scripts/init_db.py --verify

    # 本地重建（删除旧库后重新建）
    .venv/Scripts/python.exe backend/scripts/init_db.py --rebuild

异常说明
--------
- 建表失败 → 打印错误并以退出码 2 结束；
- 校验不一致 → 打印差异明细并以退出码 1 结束。
"""

import argparse
import sys
from pathlib import Path

# 将 backend/ 加入模块搜索路径，使脚本可从任意工作目录被调用
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.infra.db import SCHEMA_SQL_PATH, engine, init_database  # noqa: E402

# ============================================================
# 基线预期（逐条取自 backend/db/schema.sql，用于 --verify 对账）
# ============================================================
# 13 张业务表（数据库设计文档 V1.0.2 第 5 章）
EXPECTED_TABLES: set[str] = {
    "users",
    "files",
    "data_sheets",
    "sessions",
    "messages",
    "analysis_tasks",
    "charts",
    "reports",
    "feedbacks",
    "daily_statistics",
    "audit_logs",
    "sensitive_words",
    "configs",
}

# 21 个显式索引（CREATE [UNIQUE] INDEX 语句；不含 SQLite 自动生成的 sqlite_autoindex_*）
EXPECTED_INDEXES: set[str] = {
    "uk_users_username",
    "ix_users_status_created",
    "ix_files_user",
    "ix_sheets_file",
    "ix_sessions_user",
    "ix_sessions_updated",
    "ix_messages_session",
    "ix_messages_task",
    "ix_tasks_session",
    "ix_tasks_status",
    "ix_tasks_message",
    "ix_tasks_file",
    "ix_charts_task",
    "ix_reports_session",
    "uk_reports_task",
    "ix_feedback_value",
    "ix_feedback_task",
    "uk_stat_dim",
    "ix_audit_time",
    "ix_audit_action",
    "uk_sensitive_words_word",
}


def _query_names(sql: str) -> set[str]:
    """执行查询并返回单列结果集。

    参数：
        sql (str): 只返回一列名称的 SQL
    返回：
        set[str]: 名称集合
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    return {row[0] for row in rows}


def get_actual_tables() -> set[str]:
    """读取当前库中实际存在的业务表名。

    返回：
        set[str]: 排除 sqlite_ 内部表后的表名集合
    """
    return _query_names(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )


def get_actual_indexes() -> set[str]:
    """读取当前库中显式创建的索引名。

    返回：
        set[str]: 排除 SQLite 自动索引（sqlite_autoindex_*）后的索引名集合
    """
    return _query_names(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )


def _print_set_diff(title: str, expected: set[str], actual: set[str]) -> bool:
    """打印集合比对结果并返回是否一致。

    参数：
        title (str): 比对项名称（表 / 索引）
        expected (set[str]): 基线预期集合
        actual (set[str]): 实际集合
    返回：
        bool: 完全一致返回 True
    """
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    ok = not missing and not unexpected

    print(f"  [{title}] 预期 {len(expected)} 项，实际 {len(actual)} 项 → {'一致' if ok else '不一致'}")
    if missing:
        print(f"    缺失：{missing}")
    if unexpected:
        print(f"    多余：{unexpected}")
    return ok


def _rebuild() -> None:
    """删除数据库文件及其 WAL 附属文件，用于本地重建。

    参数：无
    返回：无
    说明：
        仅删除 sqlite_file_path 指向的 .db / -wal / -shm 三个文件，不触碰其它目录。
    """
    db_path = settings.sqlite_file_path
    if db_path is None:
        print("当前 database_url 非 SQLite，跳过删除步骤。")
        return
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{db_path}{suffix}")
        if target.exists():
            target.unlink()
            print(f"  已删除：{target}")


def main() -> int:
    """脚本主入口。

    返回：
        int: 0 成功；1 校验不一致；2 建库异常
    """
    parser = argparse.ArgumentParser(description="Excel 智能体建库与校验脚本（幂等）")
    parser.add_argument("--verify", action="store_true", help="建库后校验表与索引是否与基线一致")
    parser.add_argument("--rebuild", action="store_true", help="先删除旧数据库文件再重建（仅本地开发）")
    args = parser.parse_args()

    print("=" * 64)
    print("Excel 对话式数据分析智能体 · 建库脚本（M0）")
    print(f"  数据库：{settings.database_url}")
    print(f"  DDL 源：{SCHEMA_SQL_PATH}")
    print("=" * 64)

    if args.rebuild:
        print("[1/3] 删除旧数据库文件…")
        _rebuild()

    print("[2/3] 执行建表脚本（幂等）…")
    try:
        init_database()
    except Exception as exc:  # noqa: BLE001 —— 顶层脚本需给出明确失败信息
        print(f"建库失败：{exc}")
        return 2
    print("  建表完成。")

    tables = get_actual_tables()
    indexes = get_actual_indexes()
    print(f"[3/3] 现状：{len(tables)} 张表，{len(indexes)} 个显式索引。")

    if not args.verify:
        print("提示：加 --verify 可与基线逐项对账。")
        return 0

    print("-" * 64)
    print("校验（对照数据库设计文档 V1.0.2 第 5 章基线）：")
    table_ok = _print_set_diff("表", EXPECTED_TABLES, tables)
    index_ok = _print_set_diff("索引", EXPECTED_INDEXES, indexes)
    print("-" * 64)

    if table_ok and index_ok:
        print("校验通过：表与索引与基线完全一致。")
        return 0
    print("校验失败：请核对 backend/db/schema.sql。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
