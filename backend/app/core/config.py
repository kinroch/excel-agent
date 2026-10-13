# -*- coding: utf-8 -*-
"""
应用配置模块（依据技术文档 10.1「环境与配置清单」）

功能说明
--------
集中加载本服务的运行配置，作为全项目唯一的配置读取入口：

1. 配置来源优先级：**环境变量 > backend/.env 文件 > 代码内默认值**；
2. 由 pydantic-settings 提供类型校验与默认值，非法值在进程启动时即报错（快速失败）；
3. 供 core / api / services / infra 各层统一 `from app.core.config import settings` 引用，
   禁止在其它模块内散落读取 os.environ。

使用示例
--------
    from app.core.config import settings
    engine_url = settings.database_url

异常说明
--------
- .env 中存在非法值（如 ANALYSIS_WORKERS 写成了非整数）时，实例化 Settings 会抛
  pydantic.ValidationError，进程启动失败——这是刻意设计，避免带病运行。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录绝对路径。config.py 位于 backend/app/core/config.py，向上回溯三层即 backend/
BASE_DIR: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置对象。

    字段分组：应用元信息 / 安全密钥 / 数据库 / 文件目录 / 异步任务 / LLM 网关 / CORS。
    所有字段均可被同名（不分大小写）的环境变量覆盖。
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),  # 从 backend/.env 读取（该文件不入版本库）
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 中未被声明的多余键，避免因增量配置导致启动失败
    )

    # ---------------- 应用元信息 ----------------
    app_name: str = "Excel对话式数据分析智能体"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"  # 接口基础路径（技术文档 5.1）
    debug: bool = False  # 调试模式：开启后日志更详细（不对外暴露堆栈）

    # ---------------- 安全密钥（部署时生成，不入库不入仓；技术文档 10.1） ----------------
    secret_key: str = "dev-only-secret-change-me"  # JWT 签名密钥（M1 认证阶段启用）
    aes_key: str = "dev-only-aes-key-change-me"  # configs 表 api_key 加密密钥（M3 阶段启用）
    access_token_expire_hours: int = 12  # 默认登录有效期 12 小时（F-4.1-3）
    remember_token_expire_hours: int = 168  # 勾选「保持登录」有效期 7 天（F-4.1-3）

    # ---------------- 数据库（ADR-001：sqlite3 + WAL） ----------------
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}"
    db_echo: bool = False  # 是否打印 SQLAlchemy 执行的 SQL（排障用）

    # ---------------- 文件目录（技术文档 2.2 数据存储） ----------------
    upload_dir: str = str(BASE_DIR / "uploads")  # 用户上传原件目录
    export_dir: str = str(BASE_DIR / "exports")  # 报告 / Excel 回写导出目录
    max_upload_size_mb: int = 50  # 上传大小上限（F-4.2-2）
    log_dir: str = str(BASE_DIR / "logs")  # 运行日志目录

    # ---------------- 异步任务（NFR-P3 / ADR-002） ----------------
    analysis_workers: int = 2  # 分析 worker 池大小，可用 ANALYSIS_WORKERS 调整
    task_queue_maxsize: int = 64  # 进程内内存队列容量上限

    # ---------------- LLM 网关（默认值，运行时以 configs 表为准；技术文档 3.5） ----------------
    llm_connect_timeout: int = 15  # 连接超时（秒）
    llm_read_timeout: int = 60  # 读取超时（秒）
    llm_max_retries: int = 3  # 退避重试次数上限（2s/4s/8s）

    # ---------------- 跨域（开发期前端 Vite 地址） ----------------
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def max_upload_size_bytes(self) -> int:
        """上传大小上限的字节表示。

        返回：
            int：max_upload_size_mb 换算为字节的结果，供文件校验直接比较使用。
        """
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def sqlite_file_path(self) -> Path | None:
        """从 database_url 反解出 SQLite 数据库文件的本地路径。

        返回：
            Path：当 database_url 形如 sqlite:///<path> 时返回文件路径；
            None：非 SQLite 方言时返回 None（本期不会出现，保留兼容）。

        说明：
            供 init_db 脚本与备份脚本定位 data/app.db，避免路径重复拼接造成不一致。
        """
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            return Path(raw)
        return None


# 全局单例，各层直接导入使用
settings = Settings()
