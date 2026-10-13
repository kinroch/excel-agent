# -*- coding: utf-8 -*-
"""
ORM 模型层（SQLAlchemy 2.0 类型化访问层）

依据文档
--------
- 《开发技术文档 V1.0.1》1.3「技术选型基线」：SQLAlchemy 2.0 仅作**类型化访问层**，不引入 MySQL 方言依赖
- 《开发技术文档 V1.0.1》3.1：ORM 模型归属 infra 层
- 《数据库设计文档 V1.0.2》第 5 章「全量建表 DDL」：字段、类型、约束的唯一权威来源

功能说明
--------
1. 定义声明式基类 `Base` 与 13 张表的 ORM 映射，供 services 层以类型安全方式读写数据；
2. 提供统一的「时间戳写入」辅助，避免各服务层各写一份 `datetime.now()` 格式化逻辑。

**重要设计约定（评审重点）**
-----------------------------
① **建表的唯一权威是 `db/schema.sql`，不是本模块。**
   本文件只做「映射」，字段/约束与 schema.sql 逐条对齐（便于静态审查）。
   **禁止**在生产流程中调用 `Base.metadata.create_all()`——那会让 ORM 成为第二份 DDL 来源，
   一旦与 schema.sql 漂移就会产生「新库有、老库缺」的隐性故障。建表统一走
   `infra.db.init_database()` / `scripts/init_db.py` 执行 schema.sql。

② **时间戳统一用 TEXT（ISO-8601 字符串）而非 DATETIME。**
   与 schema.sql 的列类型严格一致；SQLite 无原生日期类型，字符串形态可读性更好，
   且跨语言（前端 / JSON）零转换成本。`created_at`/`updated_at` 必须由调用方显式传入
   （见 `utc_now_iso()`），不使用数据库默认值，保证时间口径可测试、可回放。

③ **不定义 ORM 关系（relationship）导航属性（本期）。**
   本期查询路径简单（多为按 user_id / session_id 单表或两表关联），
   显式 join 比懒加载更可控，也避免 N+1 与序列化时的循环引用问题。
   后续若出现复杂聚合需求，再按需引入。

异常说明
--------
本模块只声明映射，不主动抛业务异常；唯一可能抛出的是 SQLAlchemy 的
`NoSuchColumnError` / `IntegrityError` 等，由 services 层捕获并翻译为 BizError。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import REAL, CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ============================================================
# 一、声明式基类与公共工具
# ============================================================

class Base(DeclarativeBase):
    """全部 ORM 模型的声明式基类（SQLAlchemy 2.0 风格）。

    说明：
        仅作为 `Base.metadata` 的载体与模型注册表；不在此处设置 `__abstract__` 之外的逻辑，
        以保证各模型映射完全由 schema.sql 决定。
    """


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO-8601 字符串（秒级精度，带 +00:00 时区）。

    返回：
        str: 形如 `2026-10-13T08:30:00+00:00`
    说明：
        统一全库时间戳写入口径，避免各服务层手写 `datetime.now()` 造成
        「有的存本地时间、有的存 UTC」的混乱。秒级精度足够业务使用，
        且截断微秒后便于日志比对与去重。
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ============================================================
# 二、用户与认证（1. users）
# ============================================================

class User(Base):
    """用户表（PRD 4.1 / 4.9）。

    字段说明：
        id            : 主键，自增
        username      : 账号（手机号/邮箱），唯一
        password_hash : bcrypt_sha256 哈希（M1 由 core.security.hash_password 生成）
        role          : 角色，user（普通用户）/ admin（管理员），决定可访问的前端入口
        status        : 状态，active（正常）/ disabled（停用，登录返回 1004）
        created_at    : 创建时间（ISO 字符串）
        updated_at    : 最近更新时间
        last_login_at : 最近登录成功时间，从未登录为 NULL
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_login_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # 角色与状态取值域，与 schema.sql 的 CHECK 约束一一对应
        CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
        CheckConstraint("status IN ('active','disabled')", name="ck_users_status"),
        # 账号唯一索引（登录查询走此索引）
        Index("uk_users_username", "username", unique=True),
        # 管理后台「按状态 + 注册时间」列表页的覆盖索引
        Index("ix_users_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        """调试用字符串表示（友情提示：绝不包含 password_hash，避免哈希泄露到日志）。"""
        return f"<User id={self.id} username={self.username!r} role={self.role} status={self.status}>"


# ============================================================
# 三、文件与解析（2. files / 3. data_sheets）
# ============================================================

class File(Base):
    """上传文件表（PRD 4.2）。

    字段说明：
        user_id        : 所属用户
        original_name  : 用户原始文件名（展示用，不做存储路径）
        storage_path   : 落盘相对路径 uploads/{user_id}/{file_id}_{name}
        format         : 文件格式，xlsx / csv
        size_bytes     : 字节大小，上限 50MB（F-4.2-2）
        parse_status   : 解析状态机 pending/parsing/parsed/failed
        parse_progress : 解析进度 0~100（前端轮询展示）
        parse_error    : 解析失败可读原因
        missing_policy : 缺失值策略 prompt（询问）/ fill（填充）/ drop（丢弃）
        quality_json   : 数据质量报告（字段完整度、异常值等）JSON
        created_at     : 创建时间
        deleted_at     : 软删除时间；为 NULL 表示未删除
    """

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    parse_progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_policy: Mapped[str] = mapped_column(Text, nullable=False, default="prompt")
    quality_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("format IN ('xlsx','csv')", name="ck_files_format"),
        CheckConstraint(
            "parse_status IN ('pending','parsing','parsed','failed')", name="ck_files_parse_status"
        ),
        CheckConstraint("parse_progress BETWEEN 0 AND 100", name="ck_files_parse_progress"),
        CheckConstraint("missing_policy IN ('prompt','fill','drop')", name="ck_files_missing_policy"),
        # 用户文件列表页：按 user + 未删除 + 时间倒序
        Index("ix_files_user", "user_id", "deleted_at", "created_at"),
    )


class DataSheet(Base):
    """Sheet 结构与字段说明表（F-4.2-3/4/8）。

    字段说明：
        file_id    : 所属文件
        name       : Sheet 名
        row_count  : 数据行数（不含表头）
        col_count  : 列数
        header_row : 表头所在行号（1 起）
        fields_json: 字段说明数组 JSON（字段名、推断类型、示例值、是否可度量）
    """

    __tablename__ = "data_sheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    col_count: Mapped[int] = mapped_column(Integer, nullable=False)
    header_row: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fields_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_sheets_file", "file_id"),)


# ============================================================
# 四、会话与消息（4. sessions / 5. messages）
# ============================================================

class Session(Base):
    """会话表（PRD 4.6）。

    字段说明：
        user_id    : 所属用户
        title      : 会话标题，默认取首问前 20 字
        file_id    : 绑定的数据文件；为 NULL 表示尚未绑定
        created_at : 创建时间
        updated_at : 最近活跃时间（会话列表按它倒序）
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_sessions_user", "user_id", "updated_at"),
        Index("ix_sessions_updated", "updated_at"),
    )


class Message(Base):
    """消息表（PRD 4.6 / 6.1）。

    字段说明：
        session_id : 所属会话
        role       : 发言方 user / assistant / system
        msg_type   : 消息类型 text/intent/plan/clarify/chart/report/notice/block
        content    : 文本内容，或结构化 JSON 字符串
        task_id    : 关联分析任务（循环外键，SQLite 运行时校验，可为 NULL）
        created_at : 创建时间
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    msg_type: Mapped[str] = mapped_column(Text, nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_tasks.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        Index("ix_messages_session", "session_id", "created_at"),
        Index("ix_messages_task", "task_id"),
    )


# ============================================================
# 五、分析与产物（6. analysis_tasks / 7. charts / 8. reports）
# ============================================================

class AnalysisTask(Base):
    """分析任务表（PRD 4.3，状态机见技术文档 3.6）。

    字段说明：
        session_id   : 所属会话
        message_id   : 触发该任务的用户消息
        file_id      : 本次分析使用的文件
        status       : pending/running/awaiting_clarify/success/failed
        stage        : 当前阶段 intent/plan/executing/retry/chart/report
        intent_json  : 意图解析结果
        plan_json    : 分析步骤计划
        result_json  : 结果数据集（行列 + 汇总）
        code_text    : 取数过程代码（F-4.3-8 可解释性）
        caliber_json : 口径说明（缺失排除行数、脱敏说明）
        error        : 失败可读原因（不回显原始堆栈）
        retry_count  : 重试次数，上限 3（F-4.3-9）
        duration_ms  : 端到端耗时毫秒
        created_at   : 创建时间
        finished_at  : 结束时间（成功或失败）
    """

    __tablename__ = "analysis_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caliber_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','awaiting_clarify','success','failed')",
            name="ck_tasks_status",
        ),
        Index("ix_tasks_session", "session_id", "created_at"),
        Index("ix_tasks_status", "status", "created_at"),
        Index("ix_tasks_message", "message_id"),
        Index("ix_tasks_file", "file_id"),
    )


class Chart(Base):
    """图表表（PRD 4.4）。

    字段说明：
        task_id      : 所属分析任务
        chart_type   : bar/line/pie/scatter
        title        : 图表标题
        option_json  : ECharts option 配置
        match_reason : 图表匹配依据（AC-4.4-1，用于评测可解释性）
        created_at   : 创建时间
    """

    __tablename__ = "charts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("analysis_tasks.id"), nullable=False)
    chart_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    option_json: Mapped[str] = mapped_column(Text, nullable=False)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("chart_type IN ('bar','line','pie','scatter')", name="ck_charts_type"),
        Index("ix_charts_task", "task_id"),
    )


class Report(Base):
    """分析报告表（PRD 4.5）。

    字段说明：
        task_id    : 所属任务（唯一：一任务一报告）
        session_id : 冗余会话归属，便于按会话列出报告
        title      : 报告标题
        content_md : Markdown 源（页内展示与导出同源，保证一致）
        meta_json  : 数据来源 / 生成时间 / 口径 / 耗时等元信息
        created_at : 创建时间
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("analysis_tasks.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_reports_session", "session_id", "created_at"),
        # 一任务一报告：既是业务约束，也是导出接口幂等的兜底
        Index("uk_reports_task", "task_id", unique=True),
    )


# ============================================================
# 六、反馈与统计（9. feedbacks / 10. daily_statistics）
# ============================================================

class Feedback(Base):
    """满意度反馈表（F-4.6-7）。

    字段说明：
        user_id    : 评价用户
        message_id : 被评价的助手消息
        task_id    : 冗余任务归属，便于按任务归因统计
        value      : up（有用）/ down（无用）
        created_at : 创建时间
    约束：
        (user_id, message_id) 唯一——同一用户对同一条消息只能评价一次，重复提交走更新逻辑。
    """

    __tablename__ = "feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_tasks.id"), nullable=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("value IN ('up','down')", name="ck_feedback_value"),
        UniqueConstraint("user_id", "message_id", name="uk_feedback_user_message"),
        Index("ix_feedback_value", "value", "created_at"),
        Index("ix_feedback_task", "task_id"),
    )


class DailyStatistic(Base):
    """日聚合统计表（PRD 4.8）。

    字段说明：
        stat_date  : 统计日期 YYYY-MM-DD
        scope      : 统计范围 user（单用户）/ global（全局）
        user_id    : scope='user' 时必填；scope='global' 时为 NULL
        metric     : 指标名 task_count / success_rate / chart_count / ...
        value      : 指标数值（浮点）
        created_at : 写入时间
    约束：
        唯一维度 (stat_date, scope, IFNULL(user_id,0), metric)——约定见 schema.sql；
        用 IFNULL 归一化是为了让 global 行（user_id 为 NULL）也能参与唯一性判定。
    """

    __tablename__ = "daily_statistics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stat_date: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(REAL, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("scope IN ('user','global')", name="ck_stat_scope"),
        # 注意：schema.sql 中的 uk_stat_dim 是**表达式索引**
        #   ON daily_statistics(stat_date, scope, IFNULL(user_id, 0), metric)
        # SQLAlchemy 的 Index() 只能列举列名，无法表达 IFNULL(...) 表达式，
        # 因此此处**故意不声明**该索引（模型不参与建表，唯一性由 schema.sql 保证）。
        # scripts/check_models.py 已将其登记为「允许缺失」项，避免误报。
    )


# ============================================================
# 七、治理与配置（11. audit_logs / 12. sensitive_words / 13. configs）
# ============================================================

class AuditLog(Base):
    """操作审计日志表（F-4.9-5，仅追加，不可修改/删除）。

    字段说明：
        operator_id : 操作管理员 ID
        action      : 动作标识 user_disable/user_enable/session_view/report_export ...
        target_type : 目标类型 user/session/report
        target_id   : 目标主键（可为空，如批量导出无单一目标）
        detail_json : 脱敏后的详情 JSON（禁止写入密码、完整令牌等敏感值）
        ip          : 操作来源 IP
        created_at  : 操作时间
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_audit_time", "created_at"),
        Index("ix_audit_action", "action", "created_at"),
    )


class SensitiveWord(Base):
    """敏感词表（F-4.6-6，独立配置表，无业务外键）。

    字段说明：
        word       : 敏感词，唯一
        category   : 分类 政治/色情/暴恐/广告等
        enabled    : 是否启用，1 启用 / 0 停用（SQLite 无布尔类型，用整数表达）
        created_at : 创建时间
    """

    __tablename__ = "sensitive_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("uk_sensitive_words_word", "word", unique=True),)


class Config(Base):
    """系统配置表（技术文档 3.5 / 10.1，业务主键）。

    字段说明：
        key        : 配置键（业务主键）llm.provider / llm.api_key / llm.model ...
        value      : 配置值；api_key 存放 AES-GCM 密文（NFR-S2，加解密在 M3 引入）
        updated_at : 最近更新时间

    说明：
        本期（M1）尚不读写该表；提前定义映射是为了让配置层在 M3 落地时无需改动模型文件。
    """

    __tablename__ = "configs"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


# ============================================================
# 八、导出清单（供工具脚本与测试做「模型 ↔ schema.sql」对账）
# ============================================================

# 全部 ORM 模型与表名映射。scripts 侧可用它核对 schema.sql 中的表是否都被覆盖。
ALL_MODELS: tuple[type[Base], ...] = (
    User,
    File,
    DataSheet,
    Session,
    Message,
    AnalysisTask,
    Chart,
    Report,
    Feedback,
    DailyStatistic,
    AuditLog,
    SensitiveWord,
    Config,
)

__all__ = [
    "Base",
    "utc_now_iso",
    "ALL_MODELS",
    "User",
    "File",
    "DataSheet",
    "Session",
    "Message",
    "AnalysisTask",
    "Chart",
    "Report",
    "Feedback",
    "DailyStatistic",
    "AuditLog",
    "SensitiveWord",
    "Config",
]
