/**
 * pages/user/Workspace.tsx —— 数据工作台（UI/UX 6.3 user.html · ai-workspace）
 *
 * 对应功能点与接口：F-4.2-1~9（F1~F8）；验收锚点 AC-4.2-1~6、AC-4.7-2；NFR-P2。
 *
 * 布局（与原型三区块一致）：
 *   页头：标题 + 功能描述 + 「已上传 N 个可分析文件」chip + 上传主按钮
 *   区块 01 数据文件：左「文件列表」面板；右上「上传文件」dropzone；右下「解析进度」
 *   区块 02 数据体检：上「Sheet 结构与字段说明」面板；下左「缺失值处理策略」；
 *                     下右「数据质量小结」（stat-strip 五项）
 *   区块 03 数据预览：全宽分页明细表（缺失单元格渲染「（缺失）」灰字）
 *   折叠说明：解析规则与边界处理
 *
 * 数据流（全部经 React Query 管理服务端状态，技术文档 6.2）：
 *   F2 列表 → 选中文件 → F3 详情（解析中 800ms 轮询）→ F5 Sheet 列表
 *            → F6 字段说明 / F7 预览（随 Sheet 与页码切换）
 *   F1 上传 → 失效 F2 列表并自动选中新文件 → 继续走上面的链路
 *   F4 删除 → 失效 F2 列表；F8 策略 → 失效 F3 详情
 *
 * 关键实现决策（评审重点）
 * ------------------------
 * ① **轮询只在解析进行中开启**：F3 的 refetchInterval 由响应中的 parse_status 决定
 *    （parsing/pending → 800ms，终态 → false）。若写成固定间隔，文件全部解析完成后
 *    仍会每 800ms 发一次请求，形成「后台空转」——这是轮询接口最常见的浪费。
 * ② **F7 预览使用 placeholderData 保留上一页数据**：翻页时表格不清空、不闪白，
 *    仅在右上角页码处体现加载中。
 * ③ **删除必须二次确认**：不可恢复操作按 UI/UX 6.3 用居中确认弹窗，而非浏览器
 *    原生 confirm（原生弹窗无法承载「影响范围」说明，且样式脱离设计体系）。
 * ④ **拒绝原因如实呈现**：AC-4.2-3 要求「拒绝并提示具体原因」。上传失败的
 *    BizApiError.message 就是后端错误码表文案（如「文件超出大小上限（50MB）」），
 *    直接进红色警示条，前端不做二次翻译（避免文案两处漂移）。
 * ⑤ **stat-strip 第三项按技术文档 F3 契约取「缺失单元格」**：UI/UX 原型此处写作
 *    「存在缺失」（字段数语义），而技术文档 F3 的 quality 只提供 missing_cells
 *    （单元格数语义）。实现选择不臆造数据：用 F3 实际下发的五项一一对应，
 *    标签如实写作「缺失单元格」。该项已登记为 M2 待确认项（见 M2 交付说明）。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactElement } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  deleteFile,
  getFileDetail,
  listFields,
  listFiles,
  listSheets,
  preview,
  setMissingPolicy,
  uploadFile,
  type FieldItem,
  type FileListItem,
  type MissingPolicy,
  type ParseStatus,
} from '@/api/file';
import { BizApiError } from '@/api/client';
import { PARSE_POLL_INTERVAL } from '@/api/queryClient';
import Icon from '@/icons/Icon';
import { formatBytes, formatDateTime, formatPercent } from '@/utils/format';

import './workspace.css';

/** 解析状态 → 徽标样式与文案（含 Sheet 数展示，对齐 UI/UX 6.3 四态徽标） */
function statusBadge(item: FileListItem): { className: string; text: string } {
  const { parse_status, parse_progress, sheet_count } = item;
  if (parse_status === 'parsed') {
    return { className: 'ws-badge ws-badge--ok', text: `已解析 · ${sheet_count} Sheet${sheet_count > 1 ? 's' : ''}` };
  }
  if (parse_status === 'failed') {
    return { className: 'ws-badge ws-badge--warn', text: '解析失败' };
  }
  return { className: 'ws-badge', text: `解析中 · ${parse_progress}%` };
}

/** 缺失值策略 → 中文文案与说明（AC-4.2-5） */
const POLICY_TEXT: Record<MissingPolicy, { label: string; desc: string }> = {
  prompt: {
    label: '提示',
    desc: '在字段说明中标注缺失率，并在每次分析结论中说明因缺失值被排除的行数',
  },
  fill: {
    label: '填充',
    desc: '分析前用该列的均值/众数填充缺失单元格（仅作用于送进分析沙箱的数据副本，不改动上传原件）',
  },
  drop: {
    label: '剔除',
    desc: '分析前剔除含缺失值的整行（仅作用于送进分析沙箱的数据副本，不改动上传原件）',
  },
};

/** 把任意错误转成可展示文案 */
function errorText(error: unknown): string {
  if (error instanceof BizApiError) return error.message;
  return '网络异常或服务不可用，请稍后重试';
}

/**
 * 数据工作台视图。
 *
 * @returns 工作台三区块 JSX
 */
export function Workspace(): ReactElement {
  const queryClient = useQueryClient();

  // ---------- 本地 UI 状态 ----------
  /** 当前选中文件 ID（决定区块 02/03 展示哪份文件） */
  const [selectedId, setSelectedId] = useState<number | null>(null);
  /** 当前选中的 Sheet 索引（F6/F7 的 sheet 参数） */
  const [sheetIndex, setSheetIndex] = useState(0);
  /** 当前预览页码（1 起） */
  const [page, setPage] = useState(1);
  /** 红色警示条文案（AC-4.2-3：文件被拒原因 / 解析失败原因） */
  const [notice, setNotice] = useState<string | null>(null);
  /** 上传进度 0~100（null 表示未在上传） */
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  /** dropzone 拖拽高亮态 */
  const [dragActive, setDragActive] = useState(false);
  /** 待删除文件（非空即弹出确认弹窗） */
  const [pendingDelete, setPendingDelete] = useState<FileListItem | null>(null);
  /** 隐藏的文件选择输入框 */
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ============================================================
  // F2 文件列表
  // ============================================================
  // 列表本身也需要轮询：解析完成会让 sheet_count 与状态徽标变化，
  // 只在「存在解析中的文件」时开启，避免空转（决策① 的同一条纪律）。
  const filesQuery = useQuery({
    queryKey: ['files'],
    queryFn: listFiles,
  });
  // items 必须 memo：`data?.items ?? []` 在 data 未就绪时每次渲染都会新建空数组，
  // 直接放进 useEffect 依赖会造成「effect 每次都认为依赖变了」的抖动。
  const items = useMemo(() => filesQuery.data?.items ?? [], [filesQuery.data]);
  const hasParsing = items.some(
    (item) => item.parse_status === 'parsing' || item.parse_status === 'pending',
  );
  // 说明：用 useEffect 驱动「解析中才轮询」，而非把 refetchInterval 写成动态函数——
  // 列表查询的轮询条件是「任一子项」在解析，无法从单个 query 的 data 里简洁表达。
  // 依赖取 refetch 函数引用（React Query 保证其稳定），不依赖整个 query 结果对象，
  // 否则每次渲染都会重建定时器，轮询将无法按 800ms 稳定触发。
  const refetchFiles = filesQuery.refetch;
  useEffect(() => {
    if (!hasParsing) return;
    const timer = window.setInterval(() => {
      void refetchFiles();
    }, PARSE_POLL_INTERVAL);
    return () => window.clearInterval(timer);
  }, [hasParsing, refetchFiles]);

  // ---------- 默认选中：优先最新一份「已解析」的文件，否则第一份 ----------
  useEffect(() => {
    if (selectedId != null && items.some((item) => item.id === selectedId)) return;
    const preferred = items.find((item) => item.parse_status === 'parsed') ?? items[0];
    setSelectedId(preferred ? preferred.id : null);
  }, [items, selectedId]);

  // ============================================================
  // F3 文件详情（解析中按 800ms 轮询，终态自动停止）
  // ============================================================
  const detailQuery = useQuery({
    queryKey: ['file', selectedId],
    queryFn: () => getFileDetail(selectedId as number),
    enabled: selectedId != null,
    refetchInterval: (query) => {
      const status = query.state.data?.parse_status;
      return status === 'parsing' || status === 'pending' ? PARSE_POLL_INTERVAL : false;
    },
  });
  const detail = detailQuery.data;
  const isParsed = detail?.parse_status === 'parsed';

  // 解析完成后刷新列表：让 sheet_count 与状态徽标同步（列表查询已不在轮询态）
  const detailStatus: ParseStatus | undefined = detail?.parse_status;
  useEffect(() => {
    if (detailStatus === 'parsed' || detailStatus === 'failed') {
      void queryClient.invalidateQueries({ queryKey: ['files'] });
    }
  }, [detailStatus, queryClient]);

  // 解析失败 / 越权时把原因放进警示条
  useEffect(() => {
    if (detailStatus === 'failed' && detail?.error) {
      setNotice(`${detail.original_name} 解析失败：${detail.error}`);
    }
  }, [detailStatus, detail?.error, detail?.original_name]);

  // ============================================================
  // F5 Sheet 结构列表
  // ============================================================
  const sheetsQuery = useQuery({
    queryKey: ['sheets', selectedId],
    queryFn: () => listSheets(selectedId as number),
    enabled: selectedId != null && isParsed,
  });
  // 同样 memo：`?? []` 会产生新数组，直接用于 useMemo/useEffect 依赖会每次渲染都变化
  const sheets = useMemo(() => sheetsQuery.data?.sheets ?? [], [sheetsQuery.data]);

  // 切换文件 → 重置 Sheet 与页码
  useEffect(() => {
    setSheetIndex(0);
    setPage(1);
  }, [selectedId]);
  // 切换 Sheet → 重置页码（否则第 3 页在只有 1 页的 Sheet 上会触发一次无谓的回落请求）
  useEffect(() => {
    setPage(1);
  }, [sheetIndex]);

  const currentSheet = sheets[sheetIndex];

  // ============================================================
  // F6 字段说明
  // ============================================================
  const fieldsQuery = useQuery({
    queryKey: ['fields', selectedId, sheetIndex],
    queryFn: () => listFields(selectedId as number, sheetIndex),
    enabled: selectedId != null && isParsed && sheets.length > 0,
  });
  const fields = useMemo(() => fieldsQuery.data?.fields ?? [], [fieldsQuery.data]);
  const highMissingThreshold = fieldsQuery.data?.high_missing_threshold ?? 0.3;

  // ============================================================
  // F7 数据预览（placeholderData 保留上一页，避免翻页闪白）
  // ============================================================
  const previewQuery = useQuery({
    queryKey: ['preview', selectedId, sheetIndex, page],
    queryFn: () => preview(selectedId as number, sheetIndex, page),
    enabled: selectedId != null && isParsed && sheets.length > 0,
    placeholderData: keepPreviousData,
  });
  const previewData = previewQuery.data;

  // ============================================================
  // F1 上传 / F4 删除 / F8 策略
  // ============================================================
  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadFile(file, setUploadPercent),
    onMutate: () => {
      setNotice(null);
      setUploadPercent(0);
    },
    onSuccess: (result) => {
      // 自动选中新文件：用户的下一步必然是看它的解析进度与结构（交互连贯性）
      setSelectedId(result.file_id);
      void queryClient.invalidateQueries({ queryKey: ['files'] });
    },
    onError: (error) => {
      // AC-4.2-3：拒绝原因直接采用后端错误码表文案
      setNotice(errorText(error));
    },
    onSettled: () => {
      setUploadPercent(null);
      // 清空 input.value，使「连续上传同一个文件」也能触发 change 事件
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (fileId: number) => deleteFile(fileId),
    onSuccess: (result) => {
      setPendingDelete(null);
      if (selectedId === result.file_id) setSelectedId(null);
      setNotice(null);
      void queryClient.invalidateQueries({ queryKey: ['files'] });
    },
    onError: (error) => {
      setPendingDelete(null);
      setNotice(errorText(error));
    },
  });

  const policyMutation = useMutation({
    mutationFn: (policy: MissingPolicy) => setMissingPolicy(selectedId as number, policy),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['file', selectedId] });
    },
    onError: (error) => setNotice(errorText(error)),
  });

  // ============================================================
  // 交互处理器
  // ============================================================
  const openPicker = useCallback(() => fileInputRef.current?.click(), []);

  /** 统一的上传入口（选择文件与拖拽落盘共用） */
  const submitFile = useCallback(
    (file?: File | null) => {
      if (!file) return;
      // 前端先做一次轻量校验，把明显不合规的文件挡在网络请求之前，
      // 减少无谓往返（后端仍会完整校验一遍，前端校验不是安全边界）。
      const lower = file.name.toLowerCase();
      if (!lower.endsWith('.xlsx') && !lower.endsWith('.csv')) {
        setNotice('不支持的文件格式，仅支持 xlsx、csv');
        return;
      }
      uploadMutation.mutate(file);
    },
    [uploadMutation],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDragActive(false);
      submitFile(event.dataTransfer.files?.[0]);
    },
    [submitFile],
  );

  const onDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(true);
  }, []);

  const onDragLeave = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
  }, []);

  // ============================================================
  // 派生展示值
  // ============================================================
  const analyzableCount = filesQuery.data?.analyzable_count ?? 0;
  const quality = detail?.quality;
  const uploading = uploadPercent != null;
  /** 当前 Sheet 是否有字段达到高缺失阈值（用于质量小结末尾的建议文案） */
  const worstField = useMemo<FieldItem | null>(() => {
    if (fields.length === 0) return null;
    return fields.reduce((acc, item) => (item.missing_rate > acc.missing_rate ? item : acc), fields[0]);
  }, [fields]);

  return (
    <section className="ws" aria-label="数据工作台">
      {/* ================= 页头 ================= */}
      <div className="ws-head">
        <div className="ws-head-main">
          <h1 className="page-title">数据工作台</h1>
          <p className="ws-desc">
            上传 xlsx / csv 文件后，系统自动识别多 Sheet 结构、表头与字段数据类型，
            处理合并单元格与缺失值，并给出可分析范围的体检结论
          </p>
        </div>
        <div className="ws-head-side">
          <span className="ws-chip">
            <Icon name="workspace" size={14} />
            已上传 {analyzableCount} 个可分析文件
          </span>
          <button type="button" className="ws-btn ws-btn--primary" onClick={openPicker} disabled={uploading}>
            <Icon name="upload" size={16} />
            {uploading ? `上传中 ${uploadPercent}%` : '上传文件'}
          </button>
        </div>
      </div>

      {/* ================= 警示条（AC-4.2-3 拒绝原因 / 解析失败原因） ================= */}
      {notice ? (
        <div className="ws-notice ws-notice--warn" role="alert">
          <Icon name="warning" size={16} />
          <div className="ws-notice-body">
            {notice}
            <button type="button" className="ws-notice-close" onClick={() => setNotice(null)} aria-label="关闭提示">
              <Icon name="close" size={14} />
            </button>
          </div>
        </div>
      ) : null}

      {/* ================= ① 数据文件 ================= */}
      <section className="ws-sec">
        <header className="ws-sec-head">
          <span className="ws-sec-num">01</span>
          <div className="ws-sec-titles">
            <h2 className="ws-sec-title">数据文件</h2>
            <p className="ws-sec-sub">本账号下的文件与解析状态；选择一份文件即可查看其结构体检与数据预览</p>
          </div>
          <span className="ws-ref">F-4.2-9 查看 / 删除</span>
        </header>

        <div className="ws-grid ws-grid--main">
          {/* ---- 文件列表 ---- */}
          <div className="ws-panel">
            <div className="ws-p-head">
              <div>
                <div className="ws-p-title">文件列表</div>
                <p className="ws-p-sub">仅支持 xlsx、csv ｜ 单文件上限 50MB</p>
              </div>
            </div>

            {filesQuery.isLoading ? (
              <p className="ws-empty">正在加载文件列表…</p>
            ) : items.length === 0 ? (
              <p className="ws-empty">暂无文件。请使用右侧「上传文件」区域添加 xlsx / csv 文件。</p>
            ) : (
              <div className="ws-table-wrap">
                <table className="ws-table">
                  <thead>
                    <tr>
                      <th>文件名</th>
                      <th>大小</th>
                      <th>解析状态</th>
                      <th className="ws-num">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const badge = statusBadge(item);
                      return (
                        <tr
                          key={item.id}
                          className={item.id === selectedId ? 'is-selected' : undefined}
                          onClick={() => setSelectedId(item.id)}
                        >
                          <td className="ws-strong">
                            {item.original_name}
                            <span className="ws-row-time">{formatDateTime(item.created_at)}</span>
                          </td>
                          <td>{formatBytes(item.size_bytes)}</td>
                          <td>
                            <span className={badge.className}>{badge.text}</span>
                          </td>
                          <td className="ws-num">
                            <button
                              type="button"
                              className="ws-link"
                              onClick={(event) => {
                                event.stopPropagation();
                                setSelectedId(item.id);
                              }}
                            >
                              查看
                            </button>
                            <span className="ws-link-sep">·</span>
                            <button
                              type="button"
                              className="ws-link"
                              onClick={(event) => {
                                event.stopPropagation();
                                setPendingDelete(item);
                              }}
                            >
                              删除
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ---- 上传 + 解析进度 ---- */}
          <div className="ws-stack">
            <div className="ws-panel">
              <div className="ws-p-head">
                <div>
                  <div className="ws-p-title">上传文件</div>
                  <p className="ws-p-sub">拖拽到下方区域，或点击选择已有文件</p>
                </div>
              </div>
              <div
                className={`ws-dropzone${dragActive ? ' is-active' : ''}`}
                role="button"
                tabIndex={0}
                aria-label="上传文件"
                onClick={openPicker}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    openPicker();
                  }
                }}
                onDrop={onDrop}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
              >
                <div className="ws-drop-icon">
                  <Icon name="upload" size={26} />
                </div>
                <div className="ws-p-title">拖拽文件到此处，或点击选择</div>
                <p className="ws-field-type">支持 xlsx、csv ｜ 单文件上限 50MB</p>
                <button
                  type="button"
                  className="ws-btn ws-btn--outline ws-btn--sm"
                  onClick={(event) => {
                    event.stopPropagation();
                    openPicker();
                  }}
                >
                  选择文件
                </button>
                {uploading ? (
                  <div className="ws-progress" style={{ width: '100%' }}>
                    <span style={{ width: `${uploadPercent}%` }} />
                  </div>
                ) : null}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.csv"
                className="ws-hidden-input"
                onChange={(event) => submitFile(event.target.files?.[0])}
              />
            </div>

            <div className="ws-panel">
              <div className="ws-p-head">
                <div>
                  <div className="ws-p-title">解析进度</div>
                  <p className="ws-p-sub">
                    {detail ? `${detail.original_name} · ${detail.stage_text}` : '尚未选择文件'}
                  </p>
                </div>
                <span className="ws-badge">{detail ? `${detail.parse_progress}%` : '—'}</span>
              </div>
              <div className="ws-progress">
                <span style={{ width: `${detail?.parse_progress ?? 0}%` }} />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= ② 数据体检 ================= */}
      <section className="ws-sec">
        <header className="ws-sec-head">
          <span className="ws-sec-num">02</span>
          <div className="ws-sec-titles">
            <h2 className="ws-sec-title">数据体检</h2>
            <p className="ws-sec-sub">当前选中文件的 Sheet 结构、解析处理结论、字段类型与缺失情况</p>
          </div>
          {detail ? (
            <span className="ws-badge ws-badge--ok">
              <Icon name="file" size={13} />
              {detail.original_name}
            </span>
          ) : null}
        </header>

        {!detail ? (
          <p className="ws-empty">请先在区块 01 选择一份文件。</p>
        ) : !isParsed ? (
          <p className="ws-empty">
            {detail.parse_status === 'failed'
              ? `该文件解析失败：${detail.error ?? '未知原因'}`
              : `文件正在解析（${detail.parse_progress}%），完成后将展示结构与字段说明。`}
          </p>
        ) : (
          <div className="ws-stack">
            {/* ---- Sheet 结构与字段说明 ---- */}
            <div className="ws-panel">
              <div className="ws-p-head">
                <div>
                  <div className="ws-p-title">Sheet 结构与字段说明</div>
                  <p className="ws-p-sub">
                    {currentSheet
                      ? `已识别 ${sheets.length} 个 Sheet · 表头位于第 ${currentSheet.header_row} 行 · ` +
                        `共 ${currentSheet.row_count} 行 × ${currentSheet.col_count} 列`
                      : '正在读取 Sheet 结构…'}
                    {detail.encoding ? ` · 编码 ${detail.encoding.toUpperCase()}` : ''}
                  </p>
                </div>
                {sheets.length > 0 ? (
                  <div className="ws-segmented" role="tablist" aria-label="Sheet 切换">
                    {sheets.map((sheet) => (
                      <button
                        key={sheet.index}
                        type="button"
                        role="tab"
                        aria-selected={sheet.index === sheetIndex}
                        className={sheet.index === sheetIndex ? 'is-active' : undefined}
                        onClick={() => setSheetIndex(sheet.index)}
                      >
                        {sheet.name}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>

              {/* 解析处理信息行（F-4.2-4/5、AC-4.2-4/6 的呈现落点） */}
              {currentSheet ? (
                <div className="ws-note-line">
                  <Icon name="info" size={14} />
                  <div>
                    <strong>解析处理</strong>：{currentSheet.header_note}
                  </div>
                </div>
              ) : null}

              {fields.length === 0 ? (
                <p className="ws-empty">正在读取字段说明…</p>
              ) : (
                <div className="ws-table-wrap" style={{ marginTop: 'var(--sp-5)' }}>
                  <table className="ws-table">
                    <thead>
                      <tr>
                        <th>字段名</th>
                        <th>数据类型</th>
                        <th>示例值</th>
                        <th className="ws-num">缺失率</th>
                        <th>推断依据</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fields.map((field) => {
                        const highMissing = field.missing_rate >= highMissingThreshold;
                        return (
                          <tr key={field.name}>
                            <td className="ws-strong">
                              {field.name}
                              {field.sensitive ? (
                                <span className="ws-tag ws-tag--shield" title={`敏感字段（${field.sensitive_kind ?? '未知类型'}），分析前自动脱敏`}>
                                  <Icon name="shield" size={11} />
                                  分析前脱敏
                                </span>
                              ) : null}
                            </td>
                            <td>{field.dtype}</td>
                            <td className="ws-muted">
                              {field.samples.length > 0 ? field.samples.join(' / ') : '—'}
                            </td>
                            <td className={`ws-num${highMissing ? ' ws-up' : ''}`}>
                              {formatPercent(field.missing_rate)}
                            </td>
                            <td className="ws-muted">{field.infer_basis}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="ws-grid ws-grid--2">
              {/* ---- 缺失值处理策略（F8 / AC-4.2-5） ---- */}
              <div className="ws-panel">
                <div className="ws-p-head">
                  <div>
                    <div className="ws-p-title">缺失值处理策略</div>
                    <p className="ws-p-sub">
                      {quality && quality.missing_cells > 0
                        ? `当前文件共 ${quality.missing_cells} 个缺失单元格 · ${quality.high_missing_fields} 个高缺失字段`
                        : '当前文件未检出缺失值'}
                    </p>
                  </div>
                  <div className="ws-segmented" role="tablist" aria-label="缺失值处理策略">
                    {(Object.keys(POLICY_TEXT) as MissingPolicy[]).map((policy) => (
                      <button
                        key={policy}
                        type="button"
                        role="tab"
                        aria-selected={detail.missing_policy === policy}
                        className={detail.missing_policy === policy ? 'is-active' : undefined}
                        disabled={policyMutation.isPending}
                        onClick={() => policyMutation.mutate(policy)}
                      >
                        {POLICY_TEXT[policy].label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="ws-note-line">
                  <Icon name="info" size={14} />
                  <div>
                    当前策略：<strong>{POLICY_TEXT[detail.missing_policy].label}</strong> ——{' '}
                    {POLICY_TEXT[detail.missing_policy].desc}
                  </div>
                </div>
              </div>

              {/* ---- 数据质量小结（F3 quality / stat-strip 五项） ---- */}
              <div className="ws-panel">
                <div className="ws-p-head">
                  <div>
                    <div className="ws-p-title">数据质量小结</div>
                    <p className="ws-p-sub">体检结论，决定这份文件可以用于哪些分析</p>
                  </div>
                </div>
                <div className="ws-stat-strip">
                  <div className="ws-stat">
                    <span className="ws-stat-k">总行数</span>
                    <span className="ws-stat-v">{quality?.row_total ?? 0}</span>
                  </div>
                  <div className="ws-stat">
                    <span className="ws-stat-k">字段数</span>
                    <span className="ws-stat-v">{quality?.col_total ?? 0}</span>
                  </div>
                  <div className="ws-stat">
                    <span className="ws-stat-k">缺失单元格</span>
                    <span className="ws-stat-v">{quality?.missing_cells ?? 0}</span>
                  </div>
                  <div className="ws-stat">
                    <span className="ws-stat-k">高缺失字段</span>
                    <span className="ws-stat-v ws-accent">{quality?.high_missing_fields ?? 0}</span>
                  </div>
                  <div className="ws-stat">
                    <span className="ws-stat-k">合并单元格</span>
                    <span className="ws-stat-v">{quality?.merged_cells ?? 0}</span>
                  </div>
                </div>
                <div className="ws-note-line">
                  <Icon name="shield" size={14} />
                  <div>
                    {worstField && worstField.missing_rate >= highMissingThreshold
                      ? `「${worstField.name}」缺失 ${formatPercent(worstField.missing_rate)}，建议剔除后再做汇总类分析；`
                      : ''}
                    含敏感字段的列将在分析前自动脱敏（手机号 / 身份证 / 邮箱 / 银行卡）
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* ================= ③ 数据预览 ================= */}
      <section className="ws-sec">
        <header className="ws-sec-head">
          <span className="ws-sec-num">03</span>
          <div className="ws-sec-titles">
            <h2 className="ws-sec-title">数据预览</h2>
            <p className="ws-sec-sub">按页查看明细，用于确认字段与数值是否符合预期</p>
          </div>
          {previewData ? (
            <div className="ws-pager">
              <button
                type="button"
                className="ws-btn ws-btn--outline ws-btn--sm"
                disabled={previewData.page <= 1 || previewQuery.isFetching}
                onClick={() => setPage((prev) => Math.max(1, prev - 1))}
              >
                <Icon name="arrow-left" size={14} />
                上一页
              </button>
              <button
                type="button"
                className="ws-btn ws-btn--outline ws-btn--sm"
                disabled={previewData.page >= previewData.total_pages || previewQuery.isFetching}
                onClick={() => setPage((prev) => prev + 1)}
              >
                下一页
                <Icon name="arrow-right" size={14} />
              </button>
              <span className="ws-field-type">
                第 {previewData.page} / {previewData.total_pages} 页 · 每页 {previewData.per_page} 行 · 共{' '}
                {previewData.total_rows} 行
              </span>
            </div>
          ) : null}
        </header>

        {!isParsed ? (
          <p className="ws-empty">文件解析完成后可在此按页查看明细数据。</p>
        ) : !previewData ? (
          <p className="ws-empty">正在读取数据…</p>
        ) : previewData.columns.length === 0 ? (
          <p className="ws-empty">该 Sheet 没有可预览的数据行。</p>
        ) : (
          <div className="ws-panel">
            <div className="ws-table-wrap">
              <table className={`ws-table ws-table--preview${previewQuery.isFetching ? ' is-loading' : ''}`}>
                <thead>
                  <tr>
                    <th className="ws-rowno">#</th>
                    {previewData.columns.map((column, index) => (
                      <th key={`${column}-${index}`}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {previewData.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      <td className="ws-rowno">
                        {(previewData.page - 1) * previewData.per_page + rowIndex + 1}
                      </td>
                      {previewData.columns.map((_, columnIndex) => {
                        const cell = row[columnIndex];
                        return (
                          <td key={columnIndex}>
                            {cell === null || cell === undefined ? (
                              <span className="ws-missing">（缺失）</span>
                            ) : (
                              cell
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* ================= 折叠说明 ================= */}
      <details className="ws-details">
        <summary>解析规则与边界处理</summary>
        <ul>
          <li>空文件、加密文件、损坏文件、编码异常、超出大小上限：受理阶段即拒绝并给出具体原因，或解析阶段失败并在本条警示中说明。</li>
          <li>表头不在第一行：自动在前 10 行内识别表头，并把「已跳过前 N 行说明性内容」写入解析处理信息行。</li>
          <li>多级表头：按「上级/下级」拼接为单级列名（如「销售维度/部门」），保证后续分析可直接引用。</li>
          <li>合并单元格：取合并区域左上角的值向下填充展开，不出现错位或空列。</li>
          <li>全空行与全空列：自动剔除，并在解析处理信息行中如实告知剔除数量。</li>
          <li>CSV 编码：按 UTF-8 → GB18030（覆盖 GBK/GB2312）→ Big5 的顺序 strict 解码探测；带 BOM 的文件优先按 BOM 判定。</li>
          <li>缺失值策略（提示/填充/剔除）只记录口径，在分析期作用于送进沙箱的数据副本，不改动上传原件，因此可随时来回切换。</li>
        </ul>
      </details>

      {/* ================= 删除确认弹窗（不可恢复操作二次确认） ================= */}
      {pendingDelete ? (
        <div className="ws-modal-mask" role="presentation">
          <div className="ws-modal" role="dialog" aria-modal="true" aria-labelledby="ws-delete-title">
            <div className="ws-modal-body">
              <span className="ws-modal-icon">
                <Icon name="trash" size={22} />
              </span>
              <h2 id="ws-delete-title" className="ws-modal-title">
                删除文件？
              </h2>
              <p className="ws-modal-text">
                即将删除「{pendingDelete.original_name}」及其全部解析结果（Sheet 结构与字段说明）。
                该操作不可恢复。
              </p>
            </div>
            <div className="ws-modal-actions">
              <button
                type="button"
                className="ws-btn ws-btn--outline"
                onClick={() => setPendingDelete(null)}
                disabled={deleteMutation.isPending}
              >
                取消
              </button>
              <button
                type="button"
                className="ws-btn ws-btn--danger"
                onClick={() => deleteMutation.mutate(pendingDelete.id)}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default Workspace;
