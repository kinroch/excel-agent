/**
 * utils/format.ts —— 通用格式化工具（技术文档 6.1 utils 目录）
 *
 * 功能说明：
 *   集中放置「时间格式化 / 数值格式化 / 涨跌语义判定」等纯函数，
 *   供表格、KPI 卡、消息气泡等组件复用，避免各页面重复实现。
 *
 * 语义约定（对齐 UI/UX 4.1 与 6.6）：
 *   涨跌配色遵循国内市场惯例——涨红（--semantic-up）跌绿（--semantic-down），
 *   且必须带 +/- 符号，不单靠颜色传达语义（无障碍要求）。
 */

/**
 * 格式化 ISO 8601 时间为「YYYY-MM-DD HH:mm」。
 *
 * @param iso ISO 8601 时间字符串，如 "2026-10-13T10:55:10"
 * @returns 格式化后的本地时间；入参为空或非法时返回占位符 "—"
 */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n: number): string => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/**
 * 格式化数值为千分位字符串。
 *
 * @param value 数值
 * @param digits 小数位数，默认 0
 * @returns 千分位字符串；入参非有限数时返回 "—"
 */
export function formatNumber(value?: number | null, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * 格式化百分比（输入 0.85 → "85.0%"）。
 *
 * @param ratio 比率值（0~1）
 * @param digits 小数位数，默认 1
 * @returns 百分比字符串；入参非有限数时返回 "—"
 */
export function formatPercent(ratio?: number | null, digits = 1): string {
  if (ratio == null || !Number.isFinite(ratio)) return '—';
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** 涨跌方向 */
export type Trend = 'up' | 'down' | 'flat';

/**
 * 判定数值涨跌方向。
 *
 * @param delta 变化量，正数为涨、负数为跌
 * @param zeroTolerance 判定为零的容差，默认 1e-9
 * @returns 'up' | 'down' | 'flat'
 */
export function trendOf(delta?: number | null, zeroTolerance = 1e-9): Trend {
  if (delta == null || !Number.isFinite(delta)) return 'flat';
  if (delta > zeroTolerance) return 'up';
  if (delta < -zeroTolerance) return 'down';
  return 'flat';
}

/**
 * 生成带符号的涨跌文本（如 "+12.5%" / "-3.2%"）。
 *
 * @param delta 变化量（同 trendOf）
 * @param digits 小数位数，默认 1
 * @returns 带 +/- 前缀的字符串；非有限数返回 "—"
 */
export function formatDelta(delta?: number | null, digits = 1): string {
  if (delta == null || !Number.isFinite(delta)) return '—';
  const sign = delta > 0 ? '+' : delta < 0 ? '-' : '';
  return `${sign}${Math.abs(delta).toFixed(digits)}%`;
}

/**
 * 格式化字节数为可读大小（工作台文件列表的「大小」列）。
 *
 * @param bytes 字节数
 * @returns 形如 "2.4 MB" / "356 KB" / "820 B" 的字符串；入参非法时返回 "—"
 *
 * 口径说明：
 *   采用 1024 进制（KB/MB/GB）。<1KB 时直接展示字节数，避免出现 "0.4 KB" 这类
 *   对用户无信息量的读数；保留 1 位小数并去掉多余的 ".0"（2.4 MB / 3 MB）。
 */
export function formatBytes(bytes?: number | null): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'] as const;
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const rounded = Math.round(value * 10) / 10;
  return `${rounded} ${units[unitIndex]}`;
}
