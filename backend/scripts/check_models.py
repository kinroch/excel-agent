# -*- coding: utf-8 -*-
"""
ORM 模型与建表脚本对账脚本（质量门禁，M1 引入）

功能说明
--------
`db/schema.sql` 是建表的唯一权威来源，`app/infra/models.py` 是类型化访问层。
两者若发生漂移（模型多字段/少字段/类型不符），运行期才会以「列不存在」等形式暴露，
排查成本高。本脚本把这种漂移**提前到开发阶段**发现：

    1. 在临时目录建一个全新 SQLite 库，执行 schema.sql（不触碰真实 data/app.db）；
    2. 用 PRAGMA table_info 读出每张表在**数据库侧**的真实列与类型；
    3. 读取 ORM 模型 `Base.metadata` 的**模型侧**列与类型；
    4. 双向比对：表集合、列集合、列类型（经归一化），并核对索引名集合。

对账口径（类型归一化）
----------------------
SQLite 与 SQLAlchemy 的类型名写法不同（如 INTEGER/int、REAL/float、TEXT/str），
故统一映射到「亲和类型」后比较：整型→INTEGER、浮点→REAL、字符串→TEXT。

使用方式
--------
    cd backend
    <venv>/Scripts/python.exe scripts/check_models.py

退出码：
    0 = 完全一致；1 = 存在差异（差异明细打印到 stdout）
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

# 将 backend/ 加入模块搜索路径，保证脚本可独立运行（不依赖安装为包）
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.infra.models import ALL_MODELS, Base  # noqa: E402

SCHEMA_SQL_PATH = BACKEND_DIR / "db" / "schema.sql"

# 「允许模型不声明」的索引白名单。
# 收录原因：schema.sql 中的表达式索引（含 IFNULL(...) 等 SQL 表达式）无法用
# SQLAlchemy 的 Index() 以列名形式表达，强行声明会与建表脚本产生假差异。
# 键为表名，值为该表下允许缺失的索引名集合。
_INDEX_EXEMPTIONS: dict[str, set[str]] = {
    # 唯一维度约束：ON daily_statistics(stat_date, scope, IFNULL(user_id, 0), metric)
    "daily_statistics": {"uk_stat_dim"},
}

# SQLite 类型名 → 归一化亲和类型
_AFFINITY_MAP = {
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "BIGINT": "INTEGER",
    "REAL": "REAL",
    "FLOAT": "REAL",
    "DOUBLE": "REAL",
    "TEXT": "TEXT",
    "VARCHAR": "TEXT",
    "CHAR": "TEXT",
    "STRING": "TEXT",
    "NUMERIC": "REAL",
}


def _normalize_type(raw: str) -> str:
    """把数据库或模型给出的类型名归一化为亲和类型。

    参数：
        raw (str): 形如 "INTEGER" / "int" / "VARCHAR(64)" / "REAL"
    返回：
        str: INTEGER / REAL / TEXT / OTHER
    """
    upper = (raw or "").upper().strip()
    # 去掉长度声明，如 VARCHAR(64) -> VARCHAR
    base = upper.split("(", 1)[0].strip()
    return _AFFINITY_MAP.get(base, base or "OTHER")


def _load_db_schema() -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """在临时库执行 schema.sql，读出真实的表/列/索引。

    返回：
        tuple：(
            {表名: {列名: 亲和类型}},
            {表名: {索引名, ...}}
        )
    """
    sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    tmp_dir = tempfile.mkdtemp(prefix="eda_schema_check_")
    db_path = Path(tmp_dir) / "check.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(sql)
        conn.commit()

        # 收集用户表（排除 sqlite 内部表）
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]

        columns: dict[str, dict[str, str]] = {}
        indexes: dict[str, set[str]] = {}
        for table in tables:
            columns[table] = {
                row[1]: _normalize_type(row[2])
                for row in conn.execute(f"PRAGMA table_info('{table}')")  # noqa: S608 —— 表名来自库内元数据
            }
            # PRAGMA index_list 返回列：seq, name, unique, origin, partial
            indexes[table] = {
                row[1] for row in conn.execute(f"PRAGMA index_list('{table}')")  # noqa: S608
            }
        return columns, indexes
    finally:
        conn.close()


def _load_model_schema() -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """从 ORM 元数据读出模型侧的表/列/索引。

    返回：
        tuple：同 _load_db_schema 的结构
    """
    columns: dict[str, dict[str, str]] = {}
    indexes: dict[str, set[str]] = {}
    # 用 tables.values() 而非 sorted_tables：schema.sql 中 messages ↔ analysis_tasks
    # 存在循环外键，sorted_tables 会因无法拓扑排序而发出 SAWarning（顺序无关紧要，故绕开）。
    for table in Base.metadata.tables.values():
        columns[table.name] = {
            col.name: _normalize_type(str(col.type)) for col in table.columns
        }
        indexes[table.name] = {idx.name for idx in table.indexes}
    return columns, indexes


def main() -> int:
    """执行对账并打印结果。

    返回：
        int：0 表示一致，1 表示存在差异
    """
    print("=" * 66)
    print("ORM 模型 ↔ schema.sql 对账")
    print("=" * 66)

    db_cols, db_idx = _load_db_schema()
    md_cols, md_idx = _load_model_schema()

    problems: list[str] = []

    # ---- 1. 表集合比对 ----
    db_tables, md_tables = set(db_cols), set(md_cols)
    for t in sorted(db_tables - md_tables):
        problems.append(f"[缺模型] schema.sql 有表 {t}，但 models.py 未定义")
    for t in sorted(md_tables - db_tables):
        problems.append(f"[多模型] models.py 有表 {t}，但 schema.sql 未定义")

    # ---- 2. 列与类型比对（仅对两侧都存在的表） ----
    for t in sorted(db_tables & md_tables):
        d, m = db_cols[t], md_cols[t]
        for c in sorted(set(d) - set(m)):
            problems.append(f"[缺字段] {t}.{c}（库中为 {d[c]}，模型未定义）")
        for c in sorted(set(m) - set(d)):
            problems.append(f"[多字段] {t}.{c}（模型为 {m[c]}，库中不存在）")
        for c in sorted(set(d) & set(m)):
            if d[c] != m[c]:
                problems.append(f"[类型不符] {t}.{c}：库 {d[c]} ↔ 模型 {m[c]}")

    # ---- 3. 索引名比对 ----
    for t in sorted(db_tables & md_tables):
        d, m = db_idx[t], md_idx[t]
        exempt = _INDEX_EXEMPTIONS.get(t, set())
        # 库中索引可能含 sqlite 自动索引（sqlite_autoindex_*）与豁免的表达式索引，比对时排除
        d_named = {i for i in d if not i.startswith("sqlite_autoindex") and i not in exempt}
        for i in sorted(d_named - m):
            problems.append(f"[缺索引] {t}: 库中有 {i}，模型未声明")
        for i in sorted(m - d_named):
            problems.append(f"[多索引] {t}: 模型声明了 {i}，库中不存在")

    # ---- 4. ALL_MODELS 登记完整性（防止「新增模型却忘了登记导出清单」） ----
    registered = {model.__tablename__ for model in ALL_MODELS}
    for t in sorted(md_tables - registered):
        problems.append(f"[漏登记] 模型 {t} 未出现在 ALL_MODELS 导出清单中")
    for t in sorted(registered - md_tables):
        problems.append(f"[登记失效] ALL_MODELS 中的 {t} 未注册到 Base.metadata")

    # ---- 5. 汇总输出 ----
    print(f"库侧：{len(db_tables)} 张表 / 模型侧：{len(md_tables)} 张表（ALL_MODELS 登记 {len(registered)} 个）")
    print(f"模型覆盖：{', '.join(sorted(md_tables))}")
    print("-" * 66)

    if not problems:
        print("[PASS] 模型与建表脚本完全一致（表 / 列 / 类型 / 索引名）")
        return 0

    print(f"[FAIL] 发现 {len(problems)} 处差异：")
    for p in problems:
        print("  - " + p)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
