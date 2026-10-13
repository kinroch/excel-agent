# -*- coding: utf-8 -*-
"""
文件服务层（F1~F8 的业务编排与事务边界）

依据文档
--------
- 《开发技术文档 V1.0.1》3.1「分层规约」：services 层持有事务边界，api 层只做协议转换
- 《开发技术文档 V1.0.1》5.5「文件与解析模块（/files）」：F1~F8 接口契约
- 《开发技术文档 V1.0.1》3.6「异步任务与并发」：解析进度按 Sheet 粒度落库 + 崩溃恢复
- 《开发技术文档 V1.0.1》8.1「合规要求」：上传目录权限收紧、敏感字段脱敏
- 《PRD》F-4.2-1~9、AC-4.2-1~6
- 《UI/UX 设计文档》6.3「数据工作台」三区块

功能说明
--------
本模块是「文件接入与解析」这一业务能力的唯一实现处，对外提供：

    F1  receive_upload(...)              受理上传（校验 → 落盘 → 建记录 → 返回 202 载荷）
        run_parse_task(file_id)          后台解析任务（由路由以 BackgroundTasks 调度）
    F2  list_files(db, user_id)          文件列表（含可分析计数）
    F3  get_file_detail(db, user_id, id) 文件详情与解析进度（前端轮询）
    F4  delete_file(db, user_id, id)     删除文件（原件 + 派生数据 + 软删记录）
    F5  list_sheets(db, user_id, id)     Sheet 结构列表
    F6  list_fields(db, user_id, id, i)  字段说明（敏感字段样本已脱敏）
    F7  preview(db, user_id, id, i, ...) 数据预览（分页）
    F8  set_missing_policy(...)          设置缺失值处理策略
        fail_stale_parsing()             崩溃恢复（服务重启时把中断的解析置为 failed）

关键设计决策（评审重点）
------------------------
① **解析走异步快路径**：受理即返回 202（技术文档 5.5 F1），实际解析在
   `BackgroundTasks` 中执行。技术文档 3.6 同时要求「常规文件秒级完成」与
   「超大文件转后台协程」——两者用同一套代码满足：响应先返回（前端立刻拿到
   file_id 并开始轮询 F3），解析在后台线程中完成。对小文件，前端第一次轮询
   时大概率已 parsed，体验等同同步。
   解析本身是**阻塞型 CPU/IO 任务**，故通过 `asyncio.to_thread` 放到线程池执行，
   绝不占用事件循环（否则会卡住所有并发请求）。

② **后台任务必须自建数据库会话**：请求级会话（deps.get_db）在响应返回时即关闭，
   BackgroundTasks 在其之后运行。因此 `_parse_worker_sync` 内部用 `SessionLocal()`
   自建会话并在 finally 中关闭。这是「异步任务」类代码最常见的缺陷来源。

③ **进度写入采用短事务**：每完成一个 Sheet 即 commit 一次（技术文档 3.6
   「按 Sheet 粒度更新 parse_progress」）。SQLite 已开启 WAL（ADR-001），
   读写不互斥，前端轮询不会被写事务阻塞。

④ **CSV 副本路径可推导，不改冻结的 DDL**：解析产物落
   `uploads/_parsed/{file_id}/sheet_{i}.csv`，F7 预览与 M3 沙箱共用同一份数据，
   保证「预览所见 = 分析所用」。

⑤ **删除是「软删记录 + 硬删数据」**（PRD 6.2 数据留存）：
   files 记录保留 `deleted_at` 供管理端统计口径使用；上传原件、CSV 副本、
   data_sheets 明细行一律物理删除。理由：用户执行「删除」时的合理预期是数据
   不再留存，仅保留一条不可还原的统计痕迹；这也满足 F-4.7-8「数据不他用」。

⑥ **删除/查询一律带 user_id 过滤**（技术文档 2.2），且「文件不存在」与
   「文件不属于当前用户」返回**同一个** 2005，避免通过错误码差异探测他人
   file_id 是否存在。

⑦ **quality_json 的字段构成**：按数据库设计文档，该列承载「数据质量小结」。
   本实现存入 {五项质量指标 + encoding + high_missing_threshold + stage_text
   + sheet_meta}。其中 sheet_meta 保存每个 Sheet 的解析处理参数
   （multi_level / merged_cells / dropped_rows / dropped_cols / extra_notes），
   使 F5 的「解析处理信息行」可用纯函数**原样复现**，从而无需为它新增数据库列。

异常说明
--------
本模块统一抛 `core.errors.BizError`，由 main.py 全局处理器转为统一响应体：
    2001 格式不支持   2002 超出大小上限   2003 空文件
    2004 解析失败     2005 无权操作该文件
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import BizError
from app.core.logging import get_logger
from app.infra import storage
from app.infra.db import SessionLocal
from app.infra.models import DataSheet, File, utc_now_iso
from app.schemas.file import (
    DataQuality,
    DeleteResponse,
    FieldItem,
    FieldListResponse,
    FileDetailResponse,
    FileListItem,
    FileListResponse,
    MissingPolicy,
    MissingPolicyResponse,
    PreviewResponse,
    SheetItem,
    SheetListResponse,
    UploadResponse,
)
from app.services import parse_service
from app.services.parse_service import ParseFailure

logger = get_logger(__name__)


# ============================================================
# 一、上传校验常量
# ============================================================

# 读盘分块大小：1MB。既能边读边校验大小上限（超出即刻中止，不把超限文件读进内存），
# 又不会因分块过小导致系统调用次数暴涨。
UPLOAD_CHUNK_SIZE = 1024 * 1024

# 允许的 MIME 类型（技术文档 5.5 F1「扩展名/MIME（仅 .xlsx/.csv）」）。
# 说明：**扩展名是权威判据**，MIME 仅作交叉校验，因为浏览器上报的 MIME 极不可靠
# （CSV 常见被上报为 application/vnd.ms-excel / text/plain / application/octet-stream，
# 甚至为空）。因此这里的策略是：MIME 在「明确矛盾」时拒绝，在「未知/通用」时放行。
ALLOWED_MIME: frozenset[str] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx 标准 MIME
        "application/vnd.ms-excel",  # 部分浏览器把 csv 报成这个
        "application/csv",
        "text/csv",
        "text/x-csv",
        "text/plain",  # 部分系统把 csv 报成纯文本
        "application/octet-stream",  # 通用二进制：无法据此判断，放行交由扩展名判定
        "",  # 未上报
    }
)

# F2 列表单次返回的最大条数（防止极端情况下一次性返回过多行拖慢前端）。
# 超出部分由 total / analyzable_count 如实反映，前端提示「展示最近 N 条」。
FILE_LIST_LIMIT = 200

# F7 预览每页行数上限（前端默认传 50；此处做防御性夹取，避免构造超大页拖垮响应）
PREVIEW_MAX_PER_PAGE = 200
PREVIEW_DEFAULT_PER_PAGE = 50


# ============================================================
# 二、内部工具
# ============================================================


def _load_owned_file(db: Session, user_id: int, file_id: int) -> File:
    """载入「属于该用户且未删除」的文件记录。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前登录用户 ID（**来自令牌，不接受前端传入**）
        file_id (int): 文件 ID
    返回：
        File: 文件记录
    异常：
        BizError(2005): 文件不存在、已被删除，或不属于当前用户

    安全说明：
        三种情况合并为同一错误码是**刻意的**（设计决策⑥）：若「不存在」返回 404
        而「非本人」返回 403，攻击者可借此枚举出系统中存在哪些 file_id。
    """
    record = db.execute(
        select(File).where(
            File.id == file_id,
            File.user_id == user_id,
            File.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if record is None:
        logger.info("文件访问被拒（不存在/已删除/非本人）：user_id=%s file_id=%s", user_id, file_id)
        raise BizError(2005)
    return record


def _load_quality(record: File) -> dict[str, Any]:
    """解析 files.quality_json 为字典（容错）。

    参数：
        record (File): 文件记录
    返回：
        dict: 质量小结；为空或格式非法时返回空字典

    说明：
        该列由本模块自己写入，正常不会格式非法；但历史数据/人工改库仍可能破坏它，
        因此解析失败时降级为空字典，让 F3 仍能返回（只是质量项为零），
        不因一个展示字段把整个详情接口打挂。
    """
    if not record.quality_json:
        return {}
    try:
        parsed = json.loads(record.quality_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("quality_json 解析失败，按空值降级：file_id=%s", record.id)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_fields(record: DataSheet) -> list[dict[str, Any]]:
    """解析 data_sheets.fields_json 为字段数组（容错）。

    参数：
        record (DataSheet): Sheet 记录
    返回：
        list[dict]: 字段说明数组；非法时返回空列表
    """
    if not record.fields_json:
        return []
    try:
        parsed = json.loads(record.fields_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("fields_json 解析失败，按空值降级：sheet_id=%s", record.id)
        return []
    return parsed if isinstance(parsed, list) else []


def _sheet_note_from_quality(quality: dict[str, Any], sheet_index: int, header_row: int) -> str:
    """复现指定 Sheet 的「解析处理信息行」文案（F5）。

    参数：
        quality (dict): files.quality_json 反序列化结果
        sheet_index (int): Sheet 索引
        header_row (int): 表头行号（来自 data_sheets.header_row）
    返回：
        str: 解析处理说明

    说明：
        与解析期使用**同一个纯函数** parse_service.build_sheet_note，
        入参全部来自 quality_json.sheet_meta，因此输出与解析当时完全一致
        （见设计决策⑦）。
    """
    sheet_meta = quality.get("sheet_meta") or {}
    meta = sheet_meta.get(str(sheet_index)) or {}
    return parse_service.build_sheet_note(
        header_row_no=header_row,
        multi_level=bool(meta.get("multi_level", False)),
        merged_cells=int(meta.get("merged_cells", 0)),
        dropped_rows=int(meta.get("dropped_rows", 0)),
        dropped_cols=int(meta.get("dropped_cols", 0)),
        extra_notes=[str(item) for item in (meta.get("extra_notes") or [])],
    )


def _stage_text(record: File, quality: dict[str, Any]) -> str:
    """生成 F3 的阶段文字。

    参数：
        record (File): 文件记录
        quality (dict): 质量小结（解析期会把最新阶段文字写入 stage_text）
    返回：
        str: 阶段文字，前端直接展示

    口径：
        parsing 期间用解析期实时写入的 stage_text（如「正在解析第 2/3 个 Sheet」）；
        其余状态给出固定文案，保证前端任何时候都有可展示的文字（不会出现空白进度区）。
    """
    if record.parse_status == "parsing":
        return str(quality.get("stage_text") or "正在解析")
    if record.parse_status == "parsed":
        return "解析完成"
    if record.parse_status == "failed":
        return "解析失败"
    return "排队等待解析"


# ============================================================
# 三、F1 上传受理
# ============================================================


async def receive_upload(
    db: Session,
    user_id: int,
    filename: str,
    content_type: str | None,
    read_chunk: Callable[[int], Awaitable[bytes]],
) -> UploadResponse:
    """受理文件上传（F1 / F-4.2-1、F-4.2-2 / AC-4.2-1、AC-4.2-3、AC-4.2-6）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID（来自令牌）
        filename (str): 上传文件名（含扩展名）
        content_type (str | None): 上传时声明的 MIME 类型
        read_chunk (Callable[[int], Awaitable[bytes]]): 分块读取回调
            （由 api 层传入 `UploadFile.read`，使本服务不依赖 Starlette 类型）
    返回：
        UploadResponse: {file_id, parse_status:"parsing", original_name}
    异常：
        BizError(2001): 扩展名或 MIME 不受支持
        BizError(2002): 超出单文件 50MB 上限
        BizError(2003): 空文件

    校验顺序（技术文档 5.5 F1 明文规定，且顺序有实际意义）：
        ① 扩展名 + MIME —— 最廉价，先挡掉明显非目标文件；
        ② 大小 —— 边读边计数，**一旦超限立即中止**，不把超限文件读进内存；
        ③ 非空 —— 读完后判空；
        ④ CSV 编码探测 —— 对 csv 额外执行一次（AC-4.2-6），结果写入日志；
           真正的编码选用与「编码异常」判定在解析阶段（F3 会如实反馈失败原因）。

    为什么「受理即返回」而不是解析完再返回：
        技术文档 5.5 F1 规定响应 202 + parse_status="parsing"，解析异步进行。
        这样超大文件不会把 HTTP 连接挂住，前端也能立刻展示进度条（NFR-P2）。
    """
    # ---- 第 1 步：扩展名 + MIME ----
    fmt = parse_service.detect_format(filename)
    if fmt is None:
        logger.info("上传被拒（格式不支持）：user_id=%s filename=%s", user_id, filename)
        raise BizError(2001)
    declared_mime = (content_type or "").split(";")[0].strip().lower()
    if declared_mime and declared_mime not in ALLOWED_MIME:
        # MIME 与扩展名明确矛盾（如把 .csv 声明成 image/png）时拒绝，防伪装扩展名上传
        logger.info(
            "上传被拒（MIME 与扩展名矛盾）：user_id=%s filename=%s mime=%s",
            user_id, filename, declared_mime,
        )
        raise BizError(2001)

    # ---- 第 2 步 + 第 3 步：边读边校验大小，读完后判空 ----
    limit = settings.max_upload_size_bytes
    buffer = bytearray()
    while True:
        chunk = await read_chunk(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            logger.info("上传被拒（超出大小上限）：user_id=%s filename=%s", user_id, filename)
            raise BizError(2002, f"文件超出大小上限（{settings.max_upload_size_mb}MB）")

    if not buffer:
        logger.info("上传被拒（空文件）：user_id=%s filename=%s", user_id, filename)
        raise BizError(2003)

    # ---- 第 4 步：CSV 编码探测（AC-4.2-6；结果仅记录，不改变受理结论） ----
    if fmt == "csv":
        encoding, warning = parse_service.detect_csv_encoding(bytes(buffer[: parse_service.ENCODING_SAMPLE_BYTES]))
        logger.info("csv 编码探测：user_id=%s filename=%s encoding=%s warning=%s",
                    user_id, filename, encoding, warning or "-")
    else:
        # 加密的 xlsx 是 CFB/OLE 容器而非 zip，提前嗅探可在受理阶段就给出准确原因
        if bytes(buffer[:8]).startswith(b"\xd0\xcf\x11\xe0"):
            logger.info("上传被拒（加密文件）：user_id=%s filename=%s", user_id, filename)
            raise BizError(2004, "文件已加密受保护，请先取消密码后重新上传")

    # ---- 第 5 步：落库 + 落盘 ----
    # 先 flush 取到自增 file_id（storage_path 的命名构成需要它），再写盘，最后提交。
    # 顺序理由：若写盘失败则整体回滚，DB 中不会留下「指向不存在文件」的坏记录；
    # 反之（先提交再写盘）一旦写盘失败，就会产生一条必然解析失败的幽灵记录。
    now = utc_now_iso()
    record = File(
        user_id=user_id,
        original_name=Path(filename).name,
        storage_path="",  # 占位，下一行即刻回填（NOT NULL 约束，故不能为 None）
        format=fmt,
        size_bytes=len(buffer),
        parse_status="parsing",  # 受理即进入解析态（技术文档 5.5 F1 响应语义）
        parse_progress=0,
        parse_error=None,
        missing_policy="prompt",  # 默认「提示」，由 F8 调整（F-4.2-6）
        quality_json=None,
        created_at=now,
        deleted_at=None,
    )
    db.add(record)
    db.flush()  # 取 file_id，尚未提交

    dest = storage.build_upload_path(user_id, record.id, record.original_name)
    record.storage_path = str(dest)
    try:
        storage.save_bytes(dest, bytes(buffer))
    except OSError as exc:
        db.rollback()
        logger.error("上传落盘失败：user_id=%s filename=%s err=%s", user_id, filename, exc)
        raise BizError(9000, "文件保存失败，请检查磁盘空间后重试") from exc

    db.commit()
    db.refresh(record)

    # 上传目录权限收紧（技术文档 8.1：上传文件目录权限 700；Windows 下为 no-op）
    storage.harden_upload_dir_permissions()

    logger.info(
        "上传受理：file_id=%s user_id=%s name=%s format=%s size=%d",
        record.id, user_id, record.original_name, fmt, record.size_bytes,
    )
    return UploadResponse(
        file_id=record.id,
        parse_status="parsing",
        original_name=record.original_name,
    )


# ============================================================
# 四、后台解析任务（F1 的异步后半程）
# ============================================================


def _persist_progress(record: File, db: Session, percent: int, text: str) -> None:
    """把解析进度写入数据库（短事务）。

    参数：
        record (File): 文件记录
        db (Session): 会话
        percent (int): 进度 0~100
        text (str): 阶段文字
    返回：无

    说明：
        每次都新构造 quality_json 而不是读改写——因为解析期该列只承载进度信息，
        完整质量小结在解析结束时一次性写入（见 _parse_worker_sync）。
    """
    record.parse_progress = max(0, min(percent, 100))
    record.quality_json = json.dumps({"stage_text": text}, ensure_ascii=False)
    db.commit()


def _parse_worker_sync(file_id: int) -> None:
    """后台解析任务（同步实现，由 run_parse_task 放进线程执行）。

    参数：
        file_id (int): 文件 ID
    返回：无

    步骤：
        ① 自建数据库会话（Blocking 说明见设计决策②）；
        ② 载入记录并置为 parsing；
        ③ 调用 parse_service.parse_file（带进度回调）；
        ④ 成功 → 重建 data_sheets 行 + 写质量小结 + 置 parsed；
           失败 → 清理派生数据 + 写失败原因 + 置 failed。
    """
    db = SessionLocal()
    try:
        record = db.get(File, file_id)
        if record is None:
            logger.warning("后台解析：文件记录不存在 file_id=%s", file_id)
            return
        if record.deleted_at is not None:
            logger.info("后台解析：文件已删除，跳过 file_id=%s", file_id)
            return

        # ---- 置为解析中（受理时已是 parsing，此处兜底置位并清零进度） ----
        record.parse_status = "parsing"
        record.parse_progress = 2
        record.parse_error = None
        record.quality_json = json.dumps({"stage_text": "正在读取文件"}, ensure_ascii=False)
        db.commit()

        path = Path(record.storage_path)
        if not path.exists():
            raise ParseFailure("文件已丢失，请重新上传")

        # 进度回调：由 parse_service 在每个 Sheet 完成后调用；闭包捕获 db/record
        def on_progress(percent: int, text: str) -> None:
            _persist_progress(record, db, percent, text)

        outcome = parse_service.parse_file(
            path=path,
            fmt=record.format,
            original_name=record.original_name,
            file_id=file_id,
            on_progress=on_progress,
        )

        # ---- 成功：重建 Sheet 明细（先删后插，保证重复解析不产生残留行） ----
        db.execute(delete(DataSheet).where(DataSheet.file_id == file_id))
        for sheet in outcome.sheets:
            db.add(
                DataSheet(
                    file_id=file_id,
                    name=sheet.name,
                    row_count=sheet.row_count,
                    col_count=sheet.col_count,
                    header_row=sheet.header_row,
                    # 严格按数据库设计文档：fields_json = 字段说明数组
                    fields_json=json.dumps(
                        [
                            {
                                "name": f.name,
                                "dtype": f.dtype,
                                "samples": f.samples,
                                "missing_rate": f.missing_rate,
                                "infer_basis": f.infer_basis,
                                "sensitive": f.sensitive,
                                # 附加项：敏感类型，供前端「分析前脱敏」标注具体类别
                                "sensitive_kind": f.sensitive_kind,
                            }
                            for f in sheet.fields
                        ],
                        ensure_ascii=False,
                    ),
                )
            )

        record.parse_status = "parsed"
        record.parse_progress = 100
        record.parse_error = None
        record.quality_json = json.dumps(
            {
                "row_total": outcome.row_total,
                "col_total": outcome.col_total,
                "missing_cells": outcome.missing_cells,
                "high_missing_fields": outcome.high_missing_fields,
                "merged_cells": outcome.merged_cells,
                "encoding": outcome.encoding,
                "high_missing_threshold": parse_service.HIGH_MISSING_THRESHOLD,
                "stage_text": None,
                "sheet_meta": {
                    str(sheet.index): {
                        "multi_level": sheet.multi_level,
                        "merged_cells": sheet.merged_cells,
                        "dropped_rows": sheet.dropped_rows,
                        "dropped_cols": sheet.dropped_cols,
                        "extra_notes": sheet.extra_notes,
                    }
                    for sheet in outcome.sheets
                },
            },
            ensure_ascii=False,
        )
        db.commit()
        logger.info("后台解析完成：file_id=%s sheets=%d", file_id, len(outcome.sheets))

    except ParseFailure as exc:
        _mark_failed(db, file_id, exc.reason)
    except Exception as exc:  # noqa: BLE001 —— 后台任务必须兜住所有异常并落库，否则状态永挂 parsing
        logger.exception("后台解析出现未预期异常：file_id=%s err=%s", file_id, exc)
        _mark_failed(db, file_id, "解析失败，请检查文件内容后重试")
    finally:
        db.close()


def _mark_failed(db: Session, file_id: int, reason: str) -> None:
    """把解析标记为失败并清理派生数据。

    参数：
        db (Session): 会话
        file_id (int): 文件 ID
        reason (str): 用户可读的失败原因（直接展示在 F3 的红色警示条）
    返回：无

    说明：
        必须清理 data_sheets 与 CSV 副本：否则用户看到「解析失败」却仍能打开
        上一次成功解析的旧数据，会产生「失败但可用」的危险错觉（AC-4.2-3）。
    """
    db.rollback()  # 丢弃半途的写入，避免把不完整结果提交
    try:
        record = db.get(File, file_id)
        if record is not None:
            db.execute(delete(DataSheet).where(DataSheet.file_id == file_id))
            record.parse_status = "failed"
            record.parse_progress = 0
            record.parse_error = reason
            record.quality_json = json.dumps({"stage_text": "解析失败"}, ensure_ascii=False)
            db.commit()
        try:
            storage.remove_parsed_dir(file_id)
        except OSError as exc:
            logger.warning("清理解析产物失败（不影响失败状态）：file_id=%s err=%s", file_id, exc)
        logger.info("解析失败已落库：file_id=%s reason=%s", file_id, reason)
    except Exception as exc:  # noqa: BLE001 —— 兜底，避免标记失败本身再抛异常
        logger.exception("写入解析失败状态时异常：file_id=%s err=%s", file_id, exc)


async def run_parse_task(file_id: int) -> None:
    """后台解析任务（异步入口，由路由通过 BackgroundTasks 调度）。

    参数：
        file_id (int): 文件 ID
    返回：无

    说明：
        `asyncio.to_thread` 把阻塞的解析工作移出事件循环。解析涉及大量同步 IO
        （openpyxl 解压、csv 读写）与 CPU 计算（类型推断），直接在协程里执行会
        阻塞整个进程（连健康检查都无响应）。这一点是「异步」二字在本模块的实质含义。
    """
    await asyncio.to_thread(_parse_worker_sync, file_id)


def fail_stale_parsing() -> int:
    """崩溃恢复：把上次进程中断时残留的解析中记录置为失败（技术文档 3.6）。

    返回：
        int: 被修正的记录数

    说明：
        服务重启后，内存中未完成的后台任务已全部丢失，而这些记录的
        parse_status 仍停留在 pending/parsing，前端会永远轮询不到终态。
        启动时统一置为 failed 并给出可操作的提示文案。
        **刻意不自动重跑**：技术文档 3.6 明确「一期语义简单可预期」，
        自动重跑可能在文件损坏时形成重启风暴。
    """
    db = SessionLocal()
    try:
        stale = db.execute(
            select(File).where(
                File.parse_status.in_(("pending", "parsing")),
                File.deleted_at.is_(None),
            )
        ).scalars().all()
        if not stale:
            return 0
        for record in stale:
            record.parse_status = "failed"
            record.parse_progress = 0
            record.parse_error = "服务重启导致解析中断，请重新上传"
            record.quality_json = json.dumps({"stage_text": "解析中断"}, ensure_ascii=False)
        db.commit()
        logger.warning("崩溃恢复：%d 条解析中记录已置为失败", len(stale))
        return len(stale)
    except Exception as exc:  # noqa: BLE001 —— 恢复动作失败不应阻断服务启动
        logger.exception("崩溃恢复执行失败：%s", exc)
        return 0
    finally:
        db.close()


# ============================================================
# 五、F2 文件列表
# ============================================================


def list_files(db: Session, user_id: int) -> FileListResponse:
    """查询当前用户的文件列表（F2 / F-4.2-9 / UI/UX 6.3 区块 01）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
    返回：
        FileListResponse: {items, total, analyzable_count}

    实现要点：
        - 用 LEFT JOIN + COUNT 一次查出每个文件的 Sheet 数，避免 N+1 查询；
        - 按 created_at DESC, id DESC 排序：created_at 为 ISO 字符串，同秒上传的
          文件靠 id 兜底保证顺序稳定（否则「刚上传的文件不在最上面」会像 bug）；
        - total 单独用 COUNT 查询，不受 FILE_LIST_LIMIT 截断影响。
    """
    sheet_count_subquery = (
        select(DataSheet.file_id, func.count(DataSheet.id).label("sheet_count"))
        .group_by(DataSheet.file_id)
        .subquery()
    )

    rows = db.execute(
        select(File, func.coalesce(sheet_count_subquery.c.sheet_count, 0))
        .outerjoin(sheet_count_subquery, sheet_count_subquery.c.file_id == File.id)
        .where(File.user_id == user_id, File.deleted_at.is_(None))
        .order_by(File.created_at.desc(), File.id.desc())
        .limit(FILE_LIST_LIMIT)
    ).all()

    total = db.execute(
        select(func.count(File.id)).where(File.user_id == user_id, File.deleted_at.is_(None))
    ).scalar_one()

    analyzable = db.execute(
        select(func.count(File.id)).where(
            File.user_id == user_id,
            File.deleted_at.is_(None),
            File.parse_status == "parsed",
        )
    ).scalar_one()

    items = [
        FileListItem(
            id=record.id,
            original_name=record.original_name,
            format=record.format,
            size_bytes=record.size_bytes,
            parse_status=record.parse_status,
            parse_progress=record.parse_progress,
            sheet_count=int(count),
            created_at=record.created_at,
        )
        for record, count in rows
    ]
    return FileListResponse(items=items, total=int(total), analyzable_count=int(analyzable))


# ============================================================
# 六、F3 文件详情与解析进度
# ============================================================


def get_file_detail(db: Session, user_id: int, file_id: int) -> FileDetailResponse:
    """查询文件详情与解析进度（F3 / AC-4.2-1 / NFR-P2）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
        file_id (int): 文件 ID
    返回：
        FileDetailResponse: 含状态、进度、阶段文字、质量小结与失败原因
    异常：
        BizError(2005): 文件不存在/已删除/非本人

    说明：
        前端以约 800ms 间隔轮询本接口直至 parse_status ∈ {parsed, failed}
        （技术文档 5.5 F3：解析不使用 SSE，简化协议）。
    """
    record = _load_owned_file(db, user_id, file_id)
    quality = _load_quality(record)

    return FileDetailResponse(
        file_id=record.id,
        original_name=record.original_name,
        format=record.format,
        size_bytes=record.size_bytes,
        parse_status=record.parse_status,
        parse_progress=record.parse_progress,
        stage_text=_stage_text(record, quality),
        quality=DataQuality(
            row_total=int(quality.get("row_total", 0) or 0),
            col_total=int(quality.get("col_total", 0) or 0),
            missing_cells=int(quality.get("missing_cells", 0) or 0),
            high_missing_fields=int(quality.get("high_missing_fields", 0) or 0),
            merged_cells=int(quality.get("merged_cells", 0) or 0),
        ),
        error=record.parse_error,
        missing_policy=record.missing_policy,
        encoding=quality.get("encoding"),
        created_at=record.created_at,
    )


# ============================================================
# 七、F4 删除文件
# ============================================================


def delete_file(db: Session, user_id: int, file_id: int) -> DeleteResponse:
    """删除文件（F4 / F-4.2-9 / PRD 6.2）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
        file_id (int): 文件 ID
    返回：
        DeleteResponse: {deleted: True, file_id}
    异常：
        BizError(2005): 文件不存在/已删除/非本人

    删除范围（设计决策⑤）：
        - 上传原件（uploads/{user_id}/{file_id}_{name}）—— 物理删除；
        - CSV 副本目录（uploads/_parsed/{file_id}/）—— 物理删除；
        - data_sheets 明细行 —— 物理删除；
        - files 记录 —— **软删除**（置 deleted_at），保留统计痕迹但不含任何数据内容。

    说明：
        文件系统删除失败不阻断数据库删除：宁可留下一个孤儿文件（无记录引用，
        不可通过接口访问），也不让用户看到「删除失败」而数据仍在列表中。
        失败会写入告警日志，由运维按日志清理。
    """
    record = _load_owned_file(db, user_id, file_id)

    try:
        storage.delete_upload(record.storage_path)
    except OSError as exc:
        logger.warning("删除上传原件失败（继续删除记录）：file_id=%s err=%s", file_id, exc)
    try:
        storage.remove_parsed_dir(file_id)
    except OSError as exc:
        logger.warning("删除解析产物失败（继续删除记录）：file_id=%s err=%s", file_id, exc)

    db.execute(delete(DataSheet).where(DataSheet.file_id == file_id))
    record.deleted_at = utc_now_iso()
    db.commit()

    logger.info("文件已删除：file_id=%s user_id=%s name=%s", file_id, user_id, record.original_name)
    return DeleteResponse(deleted=True, file_id=file_id)


# ============================================================
# 八、F5 Sheet 结构列表
# ============================================================


def _load_sheets(db: Session, file_id: int) -> list[DataSheet]:
    """按原始顺序载入某文件的全部 Sheet 记录。

    参数：
        db (Session): 数据库会话
        file_id (int): 文件 ID
    返回：
        list[DataSheet]: Sheet 记录（按 id 升序 = 解析时的原始顺序）

    说明：
        建表脚本中 data_sheets 没有 sheet_index 列，因此**顺序索引由 id 升序推导**。
        解析时按原始顺序逐条插入，id 升序即原始顺序，与 CSV 副本
        sheet_{index}.csv 的编号严格对应（这也是 F6/F7 的 sheet 参数语义）。
    """
    return list(
        db.execute(select(DataSheet).where(DataSheet.file_id == file_id).order_by(DataSheet.id.asc()))
        .scalars()
        .all()
    )


def list_sheets(db: Session, user_id: int, file_id: int) -> SheetListResponse:
    """查询 Sheet 结构列表（F5 / F-4.2-3 / AC-4.2-2）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
        file_id (int): 文件 ID
    返回：
        SheetListResponse: {sheets, sheet_count}
    异常：
        BizError(2005): 文件不存在/已删除/非本人
        BizError(2004): 解析失败

    说明：
        解析尚未完成时返回**空列表**而非报错——前端在 F3 显示进度条期间不会
        调用本接口，但轮询与调用之间存在天然竞态，返回空列表可让前端优雅降级
        （展示「解析中」占位）而不是弹错误提示。
    """
    record = _load_owned_file(db, user_id, file_id)
    if record.parse_status == "failed":
        # 技术文档 5.5 F7 规定解析失败返回 2004；F5 同理（无结构可给）
        raise BizError(2004, record.parse_error or "文件解析失败")

    quality = _load_quality(record)
    sheets = _load_sheets(db, file_id)
    items = [
        SheetItem(
            index=index,
            name=sheet.name,
            row_count=sheet.row_count,
            col_count=sheet.col_count,
            header_row=sheet.header_row,
            header_note=_sheet_note_from_quality(quality, index, sheet.header_row),
        )
        for index, sheet in enumerate(sheets)
    ]
    return SheetListResponse(sheets=items, sheet_count=len(items))


# ============================================================
# 九、F6 字段说明
# ============================================================


def list_fields(db: Session, user_id: int, file_id: int, sheet_index: int) -> FieldListResponse:
    """查询指定 Sheet 的字段说明（F6 / F-4.2-4、F-4.2-8 / AC-4.2-5、AC-4.7-2）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
        file_id (int): 文件 ID
        sheet_index (int): Sheet 索引（0 起）
    返回：
        FieldListResponse: {sheet_index, sheet_name, fields, high_missing_threshold}
    异常：
        BizError(2005): 文件不存在/已删除/非本人
        BizError(2004): Sheet 索引越界或解析失败

    安全说明：
        示例值在**解析期就已脱敏**（sensitive 字段的 samples 存的是掩码文本），
        因此本接口天然不会下发原始敏感值（F-4.7-2 展示层脱敏）。
        这是「脱敏在写入侧完成」而非「读取侧临时处理」——即使未来新增读取方，
        也不可能忘记脱敏。
    """
    record = _load_owned_file(db, user_id, file_id)
    if record.parse_status == "failed":
        raise BizError(2004, record.parse_error or "文件解析失败")

    sheets = _load_sheets(db, file_id)
    if sheet_index < 0 or sheet_index >= len(sheets):
        raise BizError(2004, "指定的工作表不存在")

    sheet = sheets[sheet_index]
    quality = _load_quality(record)
    fields = [
        FieldItem(
            name=str(item.get("name", "")),
            dtype=item.get("dtype", "字符串"),
            samples=[str(s) for s in (item.get("samples") or [])],
            missing_rate=float(item.get("missing_rate") or 0.0),
            infer_basis=str(item.get("infer_basis", "")),
            sensitive=bool(item.get("sensitive", False)),
            sensitive_kind=item.get("sensitive_kind"),
        )
        for item in _load_fields(sheet)
    ]
    return FieldListResponse(
        sheet_index=sheet_index,
        sheet_name=sheet.name,
        fields=fields,
        high_missing_threshold=float(
            quality.get("high_missing_threshold") or parse_service.HIGH_MISSING_THRESHOLD
        ),
    )


# ============================================================
# 十、F7 数据预览（分页）
# ============================================================


def preview(
    db: Session,
    user_id: int,
    file_id: int,
    sheet_index: int,
    page: int,
    per_page: int,
) -> PreviewResponse:
    """分页读取 Sheet 数据（F7 / F-4.2-7 / UI/UX 6.3 区块 03）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
        file_id (int): 文件 ID
        sheet_index (int): Sheet 索引（0 起）
        page (int): 页码（1 起；越界自动夹取到合法范围）
        per_page (int): 每页行数（自动夹取到 1~PREVIEW_MAX_PER_PAGE）
    返回：
        PreviewResponse: {columns, rows, page, per_page, total_rows, total_pages}
    异常：
        BizError(2005): 文件不存在/已删除/非本人
        BizError(2004): Sheet 索引越界、解析未完成或产物缺失

    说明：
        数据源是解析期落盘的 CSV 副本（`uploads/_parsed/{file_id}/sheet_{i}.csv`），
        而不是上传原件：① 保证预览与 M3 沙箱分析用的是**同一份数据**（口径一致）；
        ② 合并单元格已展开、表头已识别、全空行列已剔除，预览所见即分析所得。
        分页参数采用「夹取」而非报错：翻到最后一页后再点「下一页」应停在末页，
        而不是弹出参数错误（前端体验考虑）。
    """
    record = _load_owned_file(db, user_id, file_id)
    if record.parse_status == "failed":
        raise BizError(2004, record.parse_error or "文件解析失败")
    if record.parse_status != "parsed":
        raise BizError(2004, "文件仍在解析中，请稍候")

    sheets = _load_sheets(db, file_id)
    if sheet_index < 0 or sheet_index >= len(sheets):
        raise BizError(2004, "指定的工作表不存在")

    safe_per_page = max(1, min(int(per_page or PREVIEW_DEFAULT_PER_PAGE), PREVIEW_MAX_PER_PAGE))
    safe_page = max(1, int(page or 1))

    csv_path = storage.build_parsed_sheet_path(file_id, sheet_index)
    try:
        columns, rows, total_rows = parse_service.read_preview(csv_path, safe_page, safe_per_page)
    except ParseFailure as exc:
        logger.warning("预览失败：file_id=%s sheet=%d reason=%s", file_id, sheet_index, exc.reason)
        raise BizError(2004, exc.reason) from exc

    total_pages = max(1, (total_rows + safe_per_page - 1) // safe_per_page)
    if safe_page > total_pages:  # 越界页码回落到末页（与前端「下一页」边界行为一致）
        safe_page = total_pages
        columns, rows, total_rows = parse_service.read_preview(csv_path, safe_page, safe_per_page)

    return PreviewResponse(
        sheet_index=sheet_index,
        sheet_name=sheets[sheet_index].name,
        columns=columns,
        rows=rows,
        page=safe_page,
        per_page=safe_per_page,
        total_rows=total_rows,
        total_pages=total_pages,
    )


# ============================================================
# 十一、F8 缺失值处理策略
# ============================================================


def set_missing_policy(
    db: Session, user_id: int, file_id: int, policy: MissingPolicy
) -> MissingPolicyResponse:
    """设置缺失值处理策略（F8 / F-4.2-6 / AC-4.2-5）。

    参数：
        db (Session): 数据库会话
        user_id (int): 当前用户 ID
        file_id (int): 文件 ID
        policy (MissingPolicy): prompt 提示 / fill 填充 / drop 剔除
    返回：
        MissingPolicyResponse: {file_id, missing_policy}
    异常：
        BizError(2005): 文件不存在/已删除/非本人

    设计说明（重要）：
        本接口**只记录策略，不改写数据**。原因：
        ① 上传原件是用户的原始数据，任何阶段都不得篡改（设计决策⑤同理）；
        ② 「填充/剔除」是**分析期**口径，作用于「送进沙箱的数据副本」，
           而不是解析产物。把它延迟到分析期应用，可以：
           - 让用户随时来回切换策略（若在解析期就地改写，切回『提示』时数据已丢失）；
           - 在口径说明中如实记录「本次分析剔除了 N 行」/「填充了 N 个空值」（F-4.3-8）。
        因此本字段按技术文档 3.4「沙箱仅挂载脱敏后的数据副本」在 M3 生效。
    """
    record = _load_owned_file(db, user_id, file_id)
    record.missing_policy = policy
    db.commit()
    logger.info("缺失值策略已更新：file_id=%s policy=%s", file_id, policy)
    return MissingPolicyResponse(file_id=file_id, missing_policy=policy)
