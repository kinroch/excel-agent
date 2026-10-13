# -*- coding: utf-8 -*-
"""
日志配置模块（依据技术文档 10.3「日志与排障」）

功能说明
--------
1. 统一初始化根 logger：控制台输出 + 文件输出（logs/app.log，50MB 滚动、保留 14 天）；
2. 提供 trace_id 上下文：一次分析任务的全部日志共享同一 trace_id，便于串联排障；
3. 日志格式采用「时间 | 级别 | trace_id | 模块 | 消息」，JSON 结构化字段自 M7 合规加固阶段补充。

使用示例
--------
    from app.core.logging import setup_logging, set_trace_id, get_logger
    setup_logging()                      # 应用启动时调用一次
    set_trace_id("a1b2c3d4")             # 请求入口设置
    get_logger(__name__).info("任务已入队")

异常说明
--------
日志目录不可创建时（磁盘只读等），降级为仅控制台输出并打印警告，不阻断应用启动。
"""

import logging
import logging.handlers
import sys
from contextvars import ContextVar
from pathlib import Path

from app.core.config import settings

# 当前请求 / 任务的 trace_id（协程安全：ContextVar 在 asyncio 任务间自动隔离）
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(trace_id)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_configured = False


class _TraceIdFilter(logging.Filter):
    """日志过滤器：把当前上下文中的 trace_id 注入每条日志记录。

    参数：
        record (logging.LogRecord): 日志记录对象
    返回：
        bool：恒为 True（不拦截任何日志）
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get()
        return True


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文的 trace_id。

    参数：
        trace_id (str): 追踪标识，通常由请求头 X-Trace-Id 透传或服务端生成
    """
    _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """读取当前上下文的 trace_id。

    返回：
        str：当前 trace_id，未设置时为 "-"
    """
    return _trace_id_var.get()


def setup_logging() -> None:
    """初始化全局日志（幂等，重复调用只生效一次）。

    参数：无
    返回：无
    异常：
        日志目录创建失败时捕获异常并降级为仅控制台输出，不向上抛出。
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    trace_filter = _TraceIdFilter()

    # 1) 控制台输出（stdout，容器/终端可见）
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    root.addHandler(console)

    # 2) 文件输出（滚动切分，保留 14 天）
    try:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_dir / "app.log"),
            when="midnight",
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(trace_filter)
        root.addHandler(file_handler)
    except OSError as exc:  # 目录不可写时降级，不阻断启动
        root.warning("日志文件初始化失败，降级为仅控制台输出：%s", exc)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取带统一配置的 logger。

    参数：
        name (str): 通常传 __name__
    返回：
        logging.Logger：标准 logger 实例
    """
    return logging.getLogger(name)
