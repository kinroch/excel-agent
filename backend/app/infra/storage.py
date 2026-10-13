# -*- coding: utf-8 -*-
"""
文件存储基础设施模块（依据技术文档 2.2 数据存储 / 3.1 infra 层）

功能说明
--------
统一管理「上传原件（uploads/）」与「导出产物（exports/）」的落盘路径与目录创建，
使业务层不直接拼接文件系统路径（分层规约：infra 层无业务语义，只提供存取能力）。

目录约定（数据库设计文档 files.storage_path 字段）：
    uploads/{user_id}/{file_id}_{原始文件名}
    exports/{user_id}/{报告或任务标识}_{文件名}

异常说明
--------
ensure_dirs() 在磁盘不可写时抛 OSError；build_upload_path() 仅做路径拼接，不创建目录。
"""

from pathlib import Path

from app.core.config import settings


def ensure_dirs() -> None:
    """确保上传目录、导出目录存在（幂等）。

    参数：无
    返回：无
    异常：
        OSError: 目录无法创建（磁盘只读 / 权限不足）
    """
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.export_dir).mkdir(parents=True, exist_ok=True)


def build_upload_path(user_id: int, file_id: int, original_name: str) -> Path:
    """拼接上传文件的落盘路径（不创建目录）。

    参数：
        user_id (int): 文件归属用户 ID
        file_id (int): 文件记录 ID
        original_name (str): 原始文件名（含扩展名）
    返回：
        Path: uploads/{user_id}/{file_id}_{original_name}
    """
    safe_name = Path(original_name).name  # 去除客户端可能携带的路径部分，防目录穿越
    return Path(settings.upload_dir) / str(user_id) / f"{file_id}_{safe_name}"


def build_export_path(user_id: int, filename: str) -> Path:
    """拼接导出文件的落盘路径。

    参数：
        user_id (int): 归属用户 ID
        filename (str): 导出文件名
    返回：
        Path: exports/{user_id}/{filename}
    """
    safe_name = Path(filename).name
    return Path(settings.export_dir) / str(user_id) / safe_name
