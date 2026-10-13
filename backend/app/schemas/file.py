# -*- coding: utf-8 -*-
"""
文件与解析模块数据契约（依据《开发技术文档 V1.0.1》5.5「文件与解析模块（/files）」）

功能说明
--------
定义 F1~F8 八个接口的请求体与响应体模型，作为前后端并行开发的**契约边界**：

| 编号 | 接口 | 方法 | 请求体 | 响应体 |
|------|------|------|--------|--------|
| F1 | 上传文件 | POST /api/v1/files | multipart(file, sheet 无关) | UploadResponse |
| F2 | 文件列表 | GET /api/v1/files | — | FileListResponse |
| F3 | 文件详情与解析进度 | GET /api/v1/files/{id} | — | FileDetailResponse |
| F4 | 删除文件 | DELETE /api/v1/files/{id} | — | DeleteResponse |
| F5 | Sheet 结构列表 | GET /api/v1/files/{id}/sheets | — | SheetListResponse |
| F6 | 字段说明 | GET /api/v1/files/{id}/fields?sheet= | — | FieldListResponse |
| F7 | 数据预览（分页） | GET /api/v1/files/{id}/preview?sheet=&page=&size= | — | PreviewResponse |
| F8 | 设置缺失值处理策略 | PATCH /api/v1/files/{id}/missing-policy | MissingPolicyRequest | MissingPolicyResponse |

设计约定
--------
1. 本文件只做**类型级约束**；扩展名/大小/非空/编码等**业务校验**在
   `services/file_service` 与 `services/parse_service` 中执行，以便产出技术文档
   约定的错误码（2001 格式不支持 / 2002 超限 / 2003 空文件 / 2004 解析失败 /
   2005 无权操作），避免 Pydantic 校验失败被统一处理器兜底成 9000 而丢失业务语义。
2. 所有响应体最终由 `core/response.success()` 包装为统一响应体 `{code, message, data}`，
   因此模型本身只描述 `data` 字段的结构。
3. 单元格值类型为 `str | int | float | bool | None`（缺失值为 None），
   前端将 None 渲染为「（缺失）」灰字（UI/UX 6.3 区块 03）。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ============================================================
# 公共枚举（与数据库 CHECK 约束一一对应）
# ============================================================

# 解析状态机：与 files.parse_status 的 CHECK 约束一致
ParseStatus = Literal["pending", "parsing", "parsed", "failed"]

# 缺失值处理策略：与 files.missing_policy 的 CHECK 约束一致
MissingPolicy = Literal["prompt", "fill", "drop"]

# 字段数据类型：技术文档 5.5 F6「dtype(字符串/整数/小数/日期/布尔)」
FieldDataType = Literal["字符串", "整数", "小数", "日期", "布尔"]


# ============================================================
# F1 上传
# ============================================================

class UploadResponse(BaseModel):
    """F1 上传受理响应（技术文档 5.5：受理后 202，data 为 {file_id, parse_status}）。

    字段：
        file_id (int): 新文件记录 ID
        parse_status (ParseStatus): 受理后的解析状态，恒为 "parsing"
        original_name (str): 原始文件名（前端用于提示「已受理 xxx」）
    """

    file_id: int
    parse_status: ParseStatus
    original_name: str


# ============================================================
# F2 文件列表
# ============================================================

class FileListItem(BaseModel):
    """F2 列表项（UI/UX 6.3 区块 01：4 列表格 + 状态徽标）。

    字段：
        id (int): 文件 ID
        original_name (str): 原始文件名
        format (str): xlsx / csv
        size_bytes (int): 字节大小
        parse_status (ParseStatus): 解析状态（决定徽标颜色与文案）
        parse_progress (int): 解析进度 0~100
        sheet_count (int): 已解析出的 Sheet 数（未解析完成时为 0）
        created_at (str): 上传时间
    """

    id: int
    original_name: str
    format: str
    size_bytes: int
    parse_status: ParseStatus
    parse_progress: int
    sheet_count: int
    created_at: str


class FileListResponse(BaseModel):
    """F2 文件列表响应。

    字段：
        items (list[FileListItem]): 当前用户的文件列表（时间倒序）
        total (int): 总数（未删除）
        analyzable_count (int): 可分析文件数（parse_status='parsed'），
                                对应 UI/UX 6.3 页头「已上传 N 个可分析文件」chip
    """

    items: list[FileListItem]
    total: int
    analyzable_count: int


# ============================================================
# F3 文件详情与解析进度
# ============================================================

class DataQuality(BaseModel):
    """数据质量小结（UI/UX 6.3「stat-strip 五项」）。

    字段：
        row_total (int): 总行数（全部 Sheet 数据行之和）
        col_total (int): 总列数（全部 Sheet 列数之和）
        missing_cells (int): 缺失单元格总数
        high_missing_fields (int): 高缺失率字段数（缺失率 ≥ 阈值的字段）
        merged_cells (int): 展开的合并单元格区域数
    """

    row_total: int = 0
    col_total: int = 0
    missing_cells: int = 0
    high_missing_fields: int = 0
    merged_cells: int = 0


class FileDetailResponse(BaseModel):
    """F3 文件详情响应（技术文档 5.5 F3）。

    字段：
        file_id (int): 文件 ID
        original_name (str): 原始文件名
        format (str): xlsx / csv
        size_bytes (int): 字节大小
        parse_status (ParseStatus): 解析状态
        parse_progress (int): 进度 0~100
        stage_text (str): 阶段文字（如「正在解析第 2/3 个 Sheet」），前端直接展示
        quality (DataQuality): 数据质量小结
        error (str | None): 解析失败的可读原因（parse_status='failed' 时非空）
        missing_policy (MissingPolicy): 当前缺失值处理策略
        encoding (str | None): CSV 实际识别出的编码（AC-4.2-6 展示用），xlsx 为 None
        created_at (str): 上传时间
    """

    file_id: int
    original_name: str
    format: str
    size_bytes: int
    parse_status: ParseStatus
    parse_progress: int
    stage_text: str
    quality: DataQuality
    error: str | None = None
    missing_policy: MissingPolicy
    encoding: str | None = None
    created_at: str


# ============================================================
# F4 删除
# ============================================================

class DeleteResponse(BaseModel):
    """F4 删除响应。

    字段：
        deleted (bool): 是否已删除；恒为 True
        file_id (int): 被删除的文件 ID
    """

    deleted: bool = True
    file_id: int


# ============================================================
# F5 Sheet 结构列表
# ============================================================

class SheetItem(BaseModel):
    """F5 单个 Sheet 结构（UI/UX 6.3 Sheet 分段切换）。

    字段：
        index (int): Sheet 顺序索引（0 起，用于 F6/F7 的 sheet 参数）
        name (str): Sheet 名
        row_count (int): 数据行数（不含表头）
        col_count (int): 列数
        header_row (int): 识别出的表头所在原始行号（1 起）
        header_note (str): 表头识别与合并单元格展开的处理说明（UI/UX「解析处理信息行」）
    """

    index: int
    name: str
    row_count: int
    col_count: int
    header_row: int
    header_note: str


class SheetListResponse(BaseModel):
    """F5 Sheet 结构列表响应。

    字段：
        sheets (list[SheetItem]): Sheet 列表
        sheet_count (int): Sheet 数量
    """

    sheets: list[SheetItem]
    sheet_count: int


# ============================================================
# F6 字段说明
# ============================================================

class FieldItem(BaseModel):
    """F6 单个字段说明（技术文档 5.5 F6 / UI/UX 6.3 字段说明表 5 列）。

    字段：
        name (str): 字段名
        dtype (FieldDataType): 推断出的数据类型
        samples (list[str]): 最多 3 个示例值；**敏感字段的示例已服务端脱敏**后再下发
        missing_rate (float): 缺失率 0~1
        infer_basis (str): 类型推断依据（如「全部为整数」「样本含 - 分隔日期」）
        sensitive (bool): 是否判定为敏感字段（F-4.7-2）
        sensitive_kind (str | None): 敏感类型（手机号/身份证/邮箱/银行卡），非敏感为 None
    """

    name: str
    dtype: FieldDataType
    samples: list[str]
    missing_rate: float
    infer_basis: str
    sensitive: bool = False
    sensitive_kind: str | None = None


class FieldListResponse(BaseModel):
    """F6 字段说明响应。

    字段：
        sheet_index (int): 所属 Sheet 索引
        sheet_name (str): 所属 Sheet 名
        fields (list[FieldItem]): 字段说明数组
        high_missing_threshold (float): 高缺失率判定阈值（前端据此标红）
    """

    sheet_index: int
    sheet_name: str
    fields: list[FieldItem]
    high_missing_threshold: float


# ============================================================
# F7 数据预览（分页）
# ============================================================

class PreviewResponse(BaseModel):
    """F7 数据预览响应（技术文档 5.5 F7）。

    字段：
        sheet_index (int): 所属 Sheet 索引
        sheet_name (str): 所属 Sheet 名
        columns (list[str]): 列名（表头）
        rows (list[list[Any]]): 行数据；缺失单元格为 None（前端渲染「（缺失）」）
        page (int): 当前页（1 起）
        per_page (int): 每页行数
        total_rows (int): 总行数
        total_pages (int): 总页数
    """

    sheet_index: int
    sheet_name: str
    columns: list[str]
    rows: list[list[Any]]
    page: int
    per_page: int
    total_rows: int
    total_pages: int


# ============================================================
# F8 缺失值处理策略
# ============================================================

class MissingPolicyRequest(BaseModel):
    """F8 设置缺失值处理策略请求（F-4.2-6 / AC-4.2-5）。

    字段：
        policy (MissingPolicy): prompt 提示 / fill 填充 / drop 剔除
    """

    policy: MissingPolicy = Field(..., description="缺失值处理策略：prompt / fill / drop")


class MissingPolicyResponse(BaseModel):
    """F8 响应。

    字段：
        file_id (int): 文件 ID
        missing_policy (MissingPolicy): 更新后的策略
    """

    file_id: int
    missing_policy: MissingPolicy
