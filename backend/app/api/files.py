# -*- coding: utf-8 -*-
"""
文件与解析路由层（F1~F8，技术文档 5.5「文件与解析模块（/files）」）

功能说明
--------
对外暴露八个接口，**只做三件事**：参数校验（Pydantic）→ 调用服务层 → 组装统一响应体。
不含任何业务规则（扩展名/大小/编码校验、权限过滤、解析编排），全部下沉至
`services/file_service` 与 `services/parse_service`。

接口清单
--------
    F1  POST   /api/v1/files                     上传文件（受理即返回 202，解析异步）
    F2  GET    /api/v1/files                     文件列表
    F3  GET    /api/v1/files/{file_id}            文件详情与解析进度（前端轮询）
    F4  DELETE /api/v1/files/{file_id}            删除文件
    F5  GET    /api/v1/files/{file_id}/sheets     Sheet 结构列表
    F6  GET    /api/v1/files/{file_id}/fields     字段说明
    F7  GET    /api/v1/files/{file_id}/preview    数据预览（分页）
    F8  PATCH  /api/v1/files/{file_id}/missing-policy  设置缺失值处理策略

鉴权
----
八个接口全部挂载 `require_user`（技术文档 5.1：公开接口仅注册与登录）。
用户身份一律取自令牌（`current_user.id`），**不接受任何前端传入的 user_id**，
这是数据隔离的第一道闸门（技术文档 2.2：所有用户态查询强制带 user_id 过滤）。

文件上传的协议细节
------------------
`UploadFile.read` 以回调形式传给服务层（`file_service.receive_upload`），
而不是把 `UploadFile` 对象直接下沉——这样 services 层不必依赖 Starlette 类型，
分层更干净；服务层得以「边读边校验大小」，超限文件不会被完整读入内存。

解析调度
--------
受理成功后通过 FastAPI 的 `BackgroundTasks` 调度 `file_service.run_parse_task`。
附带说明：BackgroundTasks 在**响应发送之后**执行，因此：
    ① 前台拿到 202 时解析尚未开始（parse_status 已置为 "parsing"，语义诚实）；
    ② 服务层内部必须自建数据库会话（请求级会话此刻已关闭）。

异常说明
--------
本层不抛业务异常，统一由 services 层抛 BizError，经 main.py 全局处理器转为
{code, message, data} 响应体，HTTP 状态码由错误码表决定（2001/2002/2003/2004/2005）。
"""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_user
from app.core.response import Envelope, success
from app.infra.models import User
from app.schemas.file import (
    DeleteResponse,
    FieldListResponse,
    FileDetailResponse,
    FileListResponse,
    MissingPolicyRequest,
    MissingPolicyResponse,
    PreviewResponse,
    SheetListResponse,
    UploadResponse,
)
from app.services import file_service

router = APIRouter(prefix="/files", tags=["files"])


# ============================================================
# F1 上传文件（受理即返回，解析异步）
# ============================================================
@router.post(
    "",
    response_model=Envelope[UploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="F1 上传文件（受理即返回，解析异步）",
    description=(
        "接收 xlsx / csv 文件，受理后立即返回并异步解析。\n\n"
        "**校验顺序**：扩展名/MIME → 大小 ≤50MB → 非空 → CSV 编码探测。\n\n"
        "**响应**：HTTP 202 + `{file_id, parse_status:\"parsing\", original_name}`；"
        "前端随后以约 800ms 间隔轮询 F3 获取进度。\n\n"
        "**错误**：2001 格式不支持 / 2002 超出大小上限 / 2003 空文件 / "
        "2004 文件已加密受保护。"
    ),
)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="待上传的 xlsx 或 csv 文件"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """上传文件接口（F1 / F-4.2-1、F-4.2-2 / AC-4.2-1、AC-4.2-3、AC-4.2-6）。

    参数：
        background_tasks (BackgroundTasks): FastAPI 后台任务收集器
        file (UploadFile): multipart 上传的文件字段（字段名固定为 file）
        db (Session): 数据库会话
        current_user (User): 当前登录用户（require_user 保证为普通用户）
    返回：
        dict: 统一响应体，data 为 {file_id, parse_status, original_name}
    异常：
        BizError(2001/2002/2003/2004): 见 summary
    说明：
        **不在本函数内直接执行解析**：解析是阻塞型 IO/CPU 任务，放在请求内会
        把响应时间与文件大小绑定（大文件直接超时）。改为 202 + 后台任务后，
        小文件依然「秒级可用」（前端第一次轮询即可拿到 parsed）。
    """
    result = await file_service.receive_upload(
        db,
        user_id=current_user.id,
        filename=file.filename or "",
        content_type=file.content_type,
        read_chunk=file.read,
    )

    # 调度后台解析（响应发出后执行；服务层内部自建会话）
    background_tasks.add_task(file_service.run_parse_task, result.file_id)

    return success(data=result)


# ============================================================
# F2 文件列表
# ============================================================
@router.get(
    "",
    response_model=Envelope[FileListResponse],
    summary="F2 文件列表",
    description=(
        "按上传时间倒序返回当前用户的文件列表（删除的不返回）。\n\n"
        "`analyzable_count` 对应页头「已上传 N 个可分析文件」chip（UI/UX 6.3）。"
    ),
)
def list_files(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """查询文件列表接口（F2 / F-4.2-9）。

    参数：
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为 {items, total, analyzable_count}
    """
    result = file_service.list_files(db, user_id=current_user.id)
    return success(data=result)


# ============================================================
# F3 文件详情与解析进度
# ============================================================
@router.get(
    "/{file_id}",
    response_model=Envelope[FileDetailResponse],
    summary="F3 文件详情与解析进度（轮询）",
    description=(
        "返回解析状态、进度、阶段文字与数据质量小结。\n\n"
        "前端以约 800ms 间隔轮询直至 `parse_status ∈ {parsed, failed}`"
        "（技术文档 5.5 F3：解析不使用 SSE，简化协议）。\n\n"
        "**错误**：2005 文件不存在或无权访问。"
    ),
)
def get_file_detail(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """查询文件详情接口（F3 / AC-4.2-1 / NFR-P2）。

    参数：
        file_id (int): 文件 ID（路径参数）
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为文件详情
    异常：
        BizError(2005): 文件不存在/已删除/非本人
    """
    result = file_service.get_file_detail(db, user_id=current_user.id, file_id=file_id)
    return success(data=result)


# ============================================================
# F4 删除文件
# ============================================================
@router.delete(
    "/{file_id}",
    response_model=Envelope[DeleteResponse],
    summary="F4 删除文件",
    description=(
        "删除上传原件、解析产物与 Sheet 明细，并把文件记录置为软删除。\n\n"
        "**该操作不可恢复**，前端须二次确认（UI/UX 6.3 区块 01 行内「删除」）。"
    ),
)
def delete_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """删除文件接口（F4 / F-4.2-9）。

    参数：
        file_id (int): 文件 ID
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为 {deleted: true, file_id}
    异常：
        BizError(2005): 文件不存在/已删除/非本人
    """
    result = file_service.delete_file(db, user_id=current_user.id, file_id=file_id)
    return success(data=result)


# ============================================================
# F5 Sheet 结构列表
# ============================================================
@router.get(
    "/{file_id}/sheets",
    response_model=Envelope[SheetListResponse],
    summary="F5 Sheet 结构列表",
    description=(
        "列出全部 Sheet（含数据行数、列数、表头行号与**解析处理信息**）。\n\n"
        "`header_note` 汇总「表头识别 / 多级表头降级 / 合并单元格展开 / 全空行列剔除 / "
        "CSV 编码识别」等处理说明，对应 UI/UX 6.3 区块 02 的解析处理信息行。\n\n"
        "**错误**：2004 解析失败 / 2005 无权访问。"
    ),
)
def list_sheets(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """查询 Sheet 结构列表接口（F5 / F-4.2-3 / AC-4.2-2）。

    参数：
        file_id (int): 文件 ID
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为 {sheets, sheet_count}
    异常：
        BizError(2004/2005)
    """
    result = file_service.list_sheets(db, user_id=current_user.id, file_id=file_id)
    return success(data=result)


# ============================================================
# F6 字段说明
# ============================================================
@router.get(
    "/{file_id}/fields",
    response_model=Envelope[FieldListResponse],
    summary="F6 字段说明",
    description=(
        "返回指定 Sheet 的字段说明：字段名 / 数据类型 / 示例值 / 缺失率 / 推断依据 / 敏感标记。\n\n"
        "**脱敏**：敏感字段（手机号/身份证/邮箱/银行卡）的示例值在**解析期**即完成掩码"
        "（如 `che***@example.com`），本接口不下发原始值（F-4.7-2）。\n\n"
        "**高缺失率**：缺失率 ≥ `high_missing_threshold` 的字段由前端标红。\n\n"
        "**错误**：2004 解析失败或工作表不存在 / 2005 无权访问。"
    ),
)
def list_fields(
    file_id: int,
    sheet: int = Query(0, ge=0, description="Sheet 索引（0 起，来自 F5 的 index）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """查询字段说明接口（F6 / F-4.2-4、F-4.2-8 / AC-4.2-5、AC-4.7-2）。

    参数：
        file_id (int): 文件 ID
        sheet (int): Sheet 索引（查询参数，默认 0）
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为 {sheet_index, sheet_name, fields, high_missing_threshold}
    异常：
        BizError(2004/2005)
    """
    result = file_service.list_fields(db, user_id=current_user.id, file_id=file_id, sheet_index=sheet)
    return success(data=result)


# ============================================================
# F7 数据预览（分页）
# ============================================================
@router.get(
    "/{file_id}/preview",
    response_model=Envelope[PreviewResponse],
    summary="F7 数据预览（分页）",
    description=(
        "按页返回 Sheet 数据（默认每页 50 行）。\n\n"
        "数据源是解析期落盘的 CSV 副本（合并单元格已展开、表头已识别、全空行列已剔除），"
        "与后续 M3 分析沙箱使用的是**同一份数据**，保证「预览所见 = 分析所得」。\n\n"
        "缺失单元格以 `null` 返回，前端渲染为「（缺失）」灰字。\n\n"
        "**错误**：2004 解析失败/未完成/工作表不存在 / 2005 无权访问。"
    ),
)
def preview(
    file_id: int,
    sheet: int = Query(0, ge=0, description="Sheet 索引（0 起）"),
    page: int = Query(1, ge=1, description="页码（1 起；越界自动回落到末页）"),
    size: int = Query(50, ge=1, le=200, description="每页行数（默认 50，上限 200）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """数据预览接口（F7 / F-4.2-7）。

    参数：
        file_id (int): 文件 ID
        sheet (int): Sheet 索引
        page (int): 页码（1 起）
        size (int): 每页行数
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为 {columns, rows, page, per_page, total_rows, total_pages}
    异常：
        BizError(2004/2005)
    说明：
        Query 上声明了 ge/le 约束：这属于**协议级参数校验**（api 层职责），
        与服务层的「夹取」策略是互补的两层——能约束的先在协议层挡住，
        剩余边界（如 page 超出末页）由服务层优雅回落，避免前端拿到 400 报错。
    """
    result = file_service.preview(
        db,
        user_id=current_user.id,
        file_id=file_id,
        sheet_index=sheet,
        page=page,
        per_page=size,
    )
    return success(data=result)


# ============================================================
# F8 缺失值处理策略
# ============================================================
@router.patch(
    "/{file_id}/missing-policy",
    response_model=Envelope[MissingPolicyResponse],
    summary="F8 设置缺失值处理策略",
    description=(
        "设置缺失值处理策略：`prompt` 提示 / `fill` 填充 / `drop` 剔除。\n\n"
        "本接口只记录策略，**不改写用户数据**；策略在分析期作用于送进沙箱的数据副本，"
        "因此可以随时来回切换（AC-4.2-5）。"
    ),
)
def set_missing_policy(
    file_id: int,
    payload: MissingPolicyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """设置缺失值处理策略接口（F8 / F-4.2-6 / AC-4.2-5）。

    参数：
        file_id (int): 文件 ID
        payload (MissingPolicyRequest): {policy}
        db (Session): 数据库会话
        current_user (User): 当前登录用户
    返回：
        dict: 统一响应体，data 为 {file_id, missing_policy}
    异常：
        BizError(2005): 文件不存在/已删除/非本人
    说明：
        policy 取值为 Literal 枚举（schemas/file.py），非法取值由 Pydantic 在协议层
        拦截，不会进入业务层，也不会与数据库 CHECK 约束冲突。
    """
    result = file_service.set_missing_policy(
        db, user_id=current_user.id, file_id=file_id, policy=payload.policy
    )
    return success(data=result)
