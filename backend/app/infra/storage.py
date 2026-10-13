# -*- coding: utf-8 -*-
"""
文件存储基础设施模块（依据技术文档 2.2 数据存储 / 3.1 infra 层 / 8.1 数据安全）

功能说明
--------
统一管理三类文件产物在本地磁盘上的**路径约定**与**读写/删除能力**，
使业务层（services）不直接拼接文件系统路径（分层规约：infra 层无业务语义，
只提供存取能力，不含上传校验、不含权限判断）。

三类产物与目录约定
------------------
1. 上传原件（数据库设计文档 files.storage_path 字段的权威格式）：
       uploads/{user_id}/{file_id}_{原始文件名}
   文件内容保持原样，是「用户原始数据」的唯一副本，任何阶段都不得改写。

2. 解析产物（M2 新增，派生自上传原件，可随时重建）：
       uploads/_parsed/{file_id}/sheet_{sheet_index}.csv
   为什么落 CSV 而不是继续用 xlsx 形态：
       a) M3 代码沙箱按技术文档 3.4 要求「仅挂载脱敏后的数据副本（CSV 形态）」，
          解析阶段直接产出 CSV 副本，沙箱层无需二次转换；
       b) 预览分页（F7）可按行偏移直接读取 CSV，不必每次重新解析 Excel；
       c) CSV 结构透明，便于数据质量统计（缺失率）与脱敏样本核验。
   注意：`_parsed` 为保留目录名，与用户目录 {user_id}（纯数字）不会冲突。

3. 导出产物（M4 报告 / M5 Excel 回写使用，本阶段仅占位）：
       exports/{user_id}/{报告或任务标识}_{文件名}

异常说明
--------
- ensure_dirs() 在磁盘不可写时抛 OSError；
- build_* 系列**仅做路径拼接**，不创建目录、不校验存在性；
- save_bytes() 会自动创建父目录，写盘失败抛 OSError；
- delete_upload() / remove_parsed_dir() 对不存在的路径**静默成功**（幂等），
  仅当删除过程中出现真实 IO 错误才向上抛。
"""

import os
import shutil
from pathlib import Path

from app.core.config import settings

# 解析产物目录名（uploads/ 下的一级保留目录）。
# 命名以下划线开头，确保与用户目录（{user_id}，纯数字）在视觉与逻辑上都不冲突。
PARSED_DIR_NAME = "_parsed"


def ensure_dirs() -> None:
    """确保上传目录、导出目录、解析产物根目录存在（幂等）。

    参数：无
    返回：无
    异常：
        OSError: 目录无法创建（磁盘只读 / 权限不足）
    """
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.export_dir).mkdir(parents=True, exist_ok=True)
    build_parsed_root().mkdir(parents=True, exist_ok=True)


def build_upload_dir(user_id: int) -> Path:
    """拼接某用户的上传目录路径（不创建目录）。

    参数：
        user_id (int): 用户 ID
    返回：
        Path: uploads/{user_id}
    """
    return Path(settings.upload_dir) / str(user_id)


def build_upload_path(user_id: int, file_id: int, original_name: str) -> Path:
    """拼接上传文件的落盘路径（不创建目录）。

    参数：
        user_id (int): 文件归属用户 ID
        file_id (int): 文件记录 ID
        original_name (str): 原始文件名（含扩展名）
    返回：
        Path: uploads/{user_id}/{file_id}_{original_name}
    """
    # Path(...).name 去除客户端可能携带的路径部分，防目录穿越（../../etc/passwd 之类）
    safe_name = Path(original_name).name
    return build_upload_dir(user_id) / f"{file_id}_{safe_name}"


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


# ---------------------------------------------------------------------------
# 以下为解析产物（uploads/_parsed/）路径工具
# ---------------------------------------------------------------------------


def build_parsed_root() -> Path:
    """解析产物根目录路径（不创建目录）。

    返回：
        Path: uploads/_parsed
    """
    return Path(settings.upload_dir) / PARSED_DIR_NAME


def build_parsed_dir(file_id: int) -> Path:
    """拼接某文件解析产物目录路径（不创建目录）。

    参数：
        file_id (int): 文件记录 ID
    返回：
        Path: uploads/_parsed/{file_id}
    """
    return build_parsed_root() / str(file_id)


def build_parsed_sheet_path(file_id: int, sheet_index: int) -> Path:
    """拼接单个 Sheet 的解析产物（CSV）路径（不创建目录）。

    参数：
        file_id (int): 文件记录 ID
        sheet_index (int): Sheet 序号（从 0 开始，与 data_sheets.sheet_index 一致）
    返回：
        Path: uploads/_parsed/{file_id}/sheet_{sheet_index}.csv

    说明：
        该路径是**可推导的**：只要知道 file_id 与 sheet_index 就能定位产物，
        因此无需在数据库新增字段记录（不改动已冻结的建表脚本）。
    """
    return build_parsed_dir(file_id) / f"sheet_{sheet_index}.csv"


def remove_parsed_dir(file_id: int) -> None:
    """删除某文件的全部解析产物（幂等）。

    参数：
        file_id (int): 文件记录 ID
    返回：无

    使用场景：
        ① 删除文件（F5）时清理派生数据；
        ② 重新解析前清空旧产物，避免残留旧 Sheet 造成「多出来的 sheet_3.csv」。

    异常：
        OSError: 目录存在但删除失败（如被其他进程占用）。
        路径不存在时静默返回，不抛异常。
    """
    target = build_parsed_dir(file_id)
    if not target.exists():
        return
    # ignore_errors=False：真实 IO 错误要暴露给调用方，由 services 层决定是否降级
    shutil.rmtree(target, ignore_errors=False)


# ---------------------------------------------------------------------------
# 以下为实际读写 / 删除能力（供 services 层调用）
# ---------------------------------------------------------------------------


def save_bytes(dest: Path, data: bytes) -> int:
    """把字节内容写入目标路径（自动创建父目录）。

    参数：
        dest (Path): 目标文件路径（通常由 build_upload_path 生成）
        data (bytes): 文件内容
    返回：
        int: 实际写入的字节数
    异常：
        OSError: 磁盘不可写 / 空间不足 / 路径被占用

    说明：
        使用分块写入（默认整块写），并在同一目录内先写 .part 临时文件再原子替换，
        避免「写入中途进程退出」留下半截文件被当作解析成功。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with tmp.open("wb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())  # 强制落盘，降低断电丢数据风险
        os.replace(tmp, dest)  # 原子替换（同目录内 rename 在多数文件系统上是原子的）
    finally:
        # 异常路径下清理临时文件，避免遗留 .part 垃圾
        if tmp.exists() and not dest.exists():
            tmp.unlink(missing_ok=True)
    return len(data)


def delete_file(path: Path) -> bool:
    """删除单个文件（幂等）。

    参数：
        path (Path): 目标文件路径
    返回：
        bool: 真正删除返回 True；文件本就不存在返回 False
    异常：
        OSError: 文件存在但删除失败（权限 / 占用）
    """
    if not path.exists():
        return False
    path.unlink()
    return True


def delete_upload(storage_path: str) -> bool:
    """按数据库记录中的 storage_path 删除上传原件（幂等）。

    参数：
        storage_path (str): files.storage_path 字段值（相对 backend/ 的路径字符串）
    返回：
        bool: 真正删除返回 True；文件本就不存在返回 False

    说明：
        入参是数据库中的字符串（非 Path），此处的转换职责放在 infra 层，
        避免 services 层到处写 Path(...) 转换。
    """
    return delete_file(Path(storage_path))


def harden_upload_dir_permissions() -> bool:
    """收紧上传目录的访问权限（依据技术文档 8.1 数据安全）。

    返回：
        bool: 成功收紧返回 True；当前平台不适用（Windows）或目录不存在返回 False

    说明：
        在 POSIX 系统上把 uploads/ 及其下已存在的一级目录权限设为 0o700
        （仅属主可读写执行），防止同机其他系统用户读取用户原始数据。
        Windows 无 POSIX 权限位语义（依赖 ACL），此处不做处理并返回 False，
        以免误用 os.chmod 造成不可预期的行为。部署到 Linux 时此函数自动生效。
    """
    if os.name != "posix":
        return False
    root = Path(settings.upload_dir)
    if not root.exists():
        return False
    os.chmod(root, 0o700)
    for child in root.iterdir():
        if child.is_dir():
            os.chmod(child, 0o700)
    return True
