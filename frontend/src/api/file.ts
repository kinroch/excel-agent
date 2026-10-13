/**
 * api/file.ts —— 文件与解析模块接口封装（F1~F8，依据技术文档 5.5）
 *
 * 功能说明：
 *   1. 封装八个文件接口的类型与调用方法，供数据工作台（Workspace）使用；
 *   2. 全部走 client.ts 的 http 实例，自动获得「统一响应体解包 + 401 拦截」能力；
 *   3. 类型定义与后端 backend/app/schemas/file.py 一一对应（契约边界，改一处须同步另一处）。
 *
 * 接口对照：
 *   F1 upload               POST   /api/v1/files                              F-4.2-1/2
 *   F2 listFiles            GET    /api/v1/files                              F-4.2-9
 *   F3 getFileDetail        GET    /api/v1/files/{id}                         AC-4.2-1
 *   F4 deleteFile           DELETE /api/v1/files/{id}                         F-4.2-9
 *   F5 listSheets           GET    /api/v1/files/{id}/sheets                  AC-4.2-2
 *   F6 listFields           GET    /api/v1/files/{id}/fields?sheet=           F-4.2-4/8
 *   F7 preview              GET    /api/v1/files/{id}/preview?sheet=&page=    F-4.2-7
 *   F8 setMissingPolicy     PATCH  /api/v1/files/{id}/missing-policy          F-4.2-6
 */

import { http, unwrap, type ApiEnvelope } from './client';

/** 解析状态机（与后端 files.parse_status 的取值一一对应） */
export type ParseStatus = 'pending' | 'parsing' | 'parsed' | 'failed';

/** 缺失值处理策略（与后端 files.missing_policy 的取值一一对应） */
export type MissingPolicy = 'prompt' | 'fill' | 'drop';

/** 字段数据类型（与后端 F6 的 dtype 取值一一对应） */
export type FieldDataType = '字符串' | '整数' | '小数' | '日期' | '布尔';

/** 单元格取值：后端以字符串下发，缺失值以 null 表示 */
export type CellValue = string | null;

/** F1 上传受理结果 */
export interface UploadResult {
  file_id: number;
  parse_status: ParseStatus;
  original_name: string;
}

/** F2 列表项 */
export interface FileListItem {
  id: number;
  original_name: string;
  format: string;
  size_bytes: number;
  parse_status: ParseStatus;
  parse_progress: number;
  sheet_count: number;
  created_at: string;
}

/** F2 列表结果 */
export interface FileListResult {
  items: FileListItem[];
  total: number;
  /** 可分析文件数（parse_status === 'parsed'），对应页头 chip */
  analyzable_count: number;
}

/** 数据质量小结五项（UI/UX 6.3 stat-strip） */
export interface DataQuality {
  row_total: number;
  col_total: number;
  missing_cells: number;
  high_missing_fields: number;
  merged_cells: number;
}

/** F3 详情结果 */
export interface FileDetail {
  file_id: number;
  original_name: string;
  format: string;
  size_bytes: number;
  parse_status: ParseStatus;
  parse_progress: number;
  /** 阶段文字（如「正在解析第 2/3 个 Sheet」），可直接展示 */
  stage_text: string;
  quality: DataQuality;
  /** 解析失败的可读原因（parse_status === 'failed' 时非空） */
  error: string | null;
  missing_policy: MissingPolicy;
  /** CSV 实际识别出的编码（AC-4.2-6 展示用），xlsx 为 null */
  encoding: string | null;
  created_at: string;
}

/** F4 删除结果 */
export interface DeleteResult {
  deleted: boolean;
  file_id: number;
}

/** F5 单个 Sheet 结构 */
export interface SheetItem {
  /** Sheet 索引（0 起，F6/F7 的 sheet 参数） */
  index: number;
  name: string;
  row_count: number;
  col_count: number;
  header_row: number;
  /** 解析处理信息（表头识别/多级表头/合并单元格/全空行列/编码） */
  header_note: string;
}

/** F5 结果 */
export interface SheetListResult {
  sheets: SheetItem[];
  sheet_count: number;
}

/** F6 单个字段说明 */
export interface FieldItem {
  name: string;
  dtype: FieldDataType | string;
  /** 示例值（敏感字段已由服务端脱敏） */
  samples: string[];
  /** 缺失率 0~1 */
  missing_rate: number;
  infer_basis: string;
  sensitive: boolean;
  /** 敏感类型（手机号/身份证/邮箱/银行卡），非敏感为 null */
  sensitive_kind: string | null;
}

/** F6 结果 */
export interface FieldListResult {
  sheet_index: number;
  sheet_name: string;
  fields: FieldItem[];
  /** 高缺失率判定阈值（缺失率 ≥ 该值由前端标红） */
  high_missing_threshold: number;
}

/** F7 预览结果 */
export interface PreviewResult {
  sheet_index: number;
  sheet_name: string;
  columns: string[];
  rows: CellValue[][];
  page: number;
  per_page: number;
  total_rows: number;
  total_pages: number;
}

/** F8 结果 */
export interface MissingPolicyResult {
  file_id: number;
  missing_policy: MissingPolicy;
}

/**
 * F1 上传文件（受理即返回，解析异步）。
 *
 * @param file 待上传的 xlsx / csv 文件
 * @param onProgress 上传进度回调（0~100），用于「上传中」进度条
 * @returns 受理结果（parse_status 恒为 'parsing'）
 * @throws BizApiError 2001 格式不支持 / 2002 超出大小上限 / 2003 空文件 / 2004 加密受保护
 *
 * 说明：
 *   使用 multipart/form-data；**不手动设置 Content-Type**——必须让浏览器自动
 *   补上 `boundary`，否则后端无法解析分段（这是上传接口最常见的坑）。
 *
 *   ⚠ 另一个更隐蔽的坑在 `client.ts`：全站 axios 实例绝不能设全局
 *   `Content-Type: application/json`，否则 axios 1.x 会把 FormData 当成 JSON
 *   序列化（`formDataToJSON` 丢弃 File/Blob），请求体变成 `{}`，
 *   后端报「参数错误[body.file]：Field required」。详见 client.ts 的实例注释。
 */
export function uploadFile(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  return unwrap(
    http.post<ApiEnvelope<UploadResult>>('/files', form, {
      onUploadProgress: (event) => {
        if (!onProgress || !event.total) return;
        onProgress(Math.round((event.loaded / event.total) * 100));
      },
    }),
  );
}

/**
 * F2 文件列表。
 *
 * @returns 文件列表（时间倒序）与可分析文件数
 */
export function listFiles(): Promise<FileListResult> {
  return unwrap(http.get<ApiEnvelope<FileListResult>>('/files'));
}

/**
 * F3 文件详情与解析进度（前端轮询本接口直至 parsed / failed）。
 *
 * @param fileId 文件 ID
 * @returns 详情（含状态、进度、阶段文字、质量小结与失败原因）
 * @throws BizApiError 2005 文件不存在或无权访问
 */
export function getFileDetail(fileId: number): Promise<FileDetail> {
  return unwrap(http.get<ApiEnvelope<FileDetail>>(`/files/${fileId}`));
}

/**
 * F4 删除文件（不可恢复，前端须二次确认）。
 *
 * @param fileId 文件 ID
 * @returns 删除结果
 * @throws BizApiError 2005 文件不存在或无权访问
 */
export function deleteFile(fileId: number): Promise<DeleteResult> {
  return unwrap(http.delete<ApiEnvelope<DeleteResult>>(`/files/${fileId}`));
}

/**
 * F5 Sheet 结构列表。
 *
 * @param fileId 文件 ID
 * @returns Sheet 列表与数量
 * @throws BizApiError 2004 解析失败 / 2005 无权访问
 */
export function listSheets(fileId: number): Promise<SheetListResult> {
  return unwrap(http.get<ApiEnvelope<SheetListResult>>(`/files/${fileId}/sheets`));
}

/**
 * F6 字段说明。
 *
 * @param fileId 文件 ID
 * @param sheet Sheet 索引（0 起）
 * @returns 字段说明数组与高缺失阈值
 * @throws BizApiError 2004 解析失败或工作表不存在 / 2005 无权访问
 */
export function listFields(fileId: number, sheet: number): Promise<FieldListResult> {
  return unwrap(http.get<ApiEnvelope<FieldListResult>>(`/files/${fileId}/fields`, { params: { sheet } }));
}

/**
 * F7 数据预览（分页）。
 *
 * @param fileId 文件 ID
 * @param sheet Sheet 索引（0 起）
 * @param page 页码（1 起）
 * @param size 每页行数（默认 50）
 * @returns 当页数据与分页信息
 * @throws BizApiError 2004 解析失败/未完成/工作表不存在 / 2005 无权访问
 */
export function preview(
  fileId: number,
  sheet: number,
  page: number,
  size = 50,
): Promise<PreviewResult> {
  return unwrap(
    http.get<ApiEnvelope<PreviewResult>>(`/files/${fileId}/preview`, {
      params: { sheet, page, size },
    }),
  );
}

/**
 * F8 设置缺失值处理策略。
 *
 * @param fileId 文件 ID
 * @param policy prompt 提示 / fill 填充 / drop 剔除
 * @returns 更新后的策略
 * @throws BizApiError 2005 文件不存在或无权访问
 */
export function setMissingPolicy(fileId: number, policy: MissingPolicy): Promise<MissingPolicyResult> {
  return unwrap(
    http.patch<ApiEnvelope<MissingPolicyResult>>(`/files/${fileId}/missing-policy`, { policy }),
  );
}
