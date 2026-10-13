# -*- coding: utf-8 -*-
"""
数据库基础设施模块（依据技术文档 3.1 infra 层、4.1 SQLite 选型、ADR-001）

功能说明
--------
1. 创建 SQLAlchemy 2.0 引擎，连接 SQLite 数据库（WAL 模式 + 外键强制 + 同步策略）；
2. 提供会话工厂 SessionLocal 与 FastAPI 依赖 get_db，服务层通过它获取事务边界；
3. 提供 init_database()：幂等执行 db/schema.sql，完成 13 张表与索引的创建；
4. 提供 check_database()：健康检查用的连通性探测。

关键设计
--------
- PRAGMA 必须「每连接」生效：`journal_mode=WAL` 是库级持久设置，而
  `foreign_keys=ON`、`synchronous=NORMAL` 属于连接级设置，因此通过
  SQLAlchemy 的 connect 事件在每个新连接上执行，保证行为一致（技术文档 3.4）。
- schema.sql 采用 `CREATE TABLE IF NOT EXISTS` 书写，天然支持重复执行（M0 验收 AC）。

异常说明
--------
- 数据库文件所在目录不可创建、或 DDL 执行失败时，init_database() 抛 RuntimeError；
- get_db() 在请求结束时保证会话关闭（finally），不向调用方抛异常。
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import BASE_DIR, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# db/schema.sql 的绝对路径（backend/db/schema.sql）
SCHEMA_SQL_PATH: Path = BASE_DIR / "db" / "schema.sql"

# SQLAlchemy 引擎（模块级单例）。SQLite 在多线程场景需关闭同线程检查
engine: Engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """在每个新数据库连接上应用 SQLite PRAGMA。

    参数：
        dbapi_connection: DBAPI 原生连接对象（sqlite3.Connection）
        _connection_record: SQLAlchemy 连接池记录（未使用）
    返回：
        无
    说明：
        foreign_keys 默认关闭，必须显式开启才能让外键约束生效（数据库设计文档要求）。
        synchronous=NORMAL 是 WAL 模式下官方推荐的落盘策略，兼顾安全与性能。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
    finally:
        cursor.close()


# 会话工厂：expire_on_commit=False 便于提交后继续读取 ORM 对象属性
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：为每个请求提供一个数据库会话，请求结束自动关闭。

    产出：
        Session: SQLAlchemy 会话对象
    说明：
        不在本函数内自动 commit，事务边界由 services 层显式控制（分层规约）。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database() -> None:
    """执行 db/schema.sql，幂等创建全部表与索引。

    参数：无
    返回：无
    异常：
        FileNotFoundError: schema.sql 缺失
        RuntimeError: DDL 执行失败（语法错误、磁盘不可写等）
    说明：
        直接使用 DBAPI 的 executescript 以支持「一个文件含多条语句 + PRAGMA」，
        SQLAlchemy 的 execute() 一次仅支持单条语句，不适合执行完整建库脚本。
    """
    if not SCHEMA_SQL_PATH.exists():
        raise FileNotFoundError(f"建表脚本不存在：{SCHEMA_SQL_PATH}")

    sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")

    # 确保数据库文件所在目录存在
    db_path = settings.sqlite_file_path
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)

    raw = engine.raw_connection()
    try:
        raw.executescript(sql)
        raw.commit()
    except Exception as exc:  # 统一包装为 RuntimeError，便于启动期识别
        raise RuntimeError(f"执行建表脚本失败：{exc}") from exc
    finally:
        raw.close()

    logger.info("数据库初始化完成：%s", settings.database_url)


def check_database() -> bool:
    """健康检查：探测数据库是否可连通。

    返回：
        bool：可执行 SELECT 1 返回 True；发生异常返回 False
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 —— 健康检查需吞掉所有异常并回报 False
        logger.warning("数据库连通性检查失败：%s", exc)
        return False
