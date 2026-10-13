/**
 * StageNotice.tsx —— 里程碑占位提示组件（M0 骨架期专用）
 *
 * 功能说明：
 *   M0 阶段各业务视图尚未实现，统一使用本组件渲染「该视图将在 MX 阶段实现」的说明，
 *   并标注其对应的 PRD 功能点、接口编号与验收锚点，便于评审时逐屏对账。
 *
 * 说明：
 *   本组件仅用于骨架期，随对应里程碑落地后应从页面中移除（保留文件不影响）。
 */

import type { ReactElement } from 'react';

import './StageNotice.css';

/** StageNotice 属性 */
export interface StageNoticeProps {
  /** 视图标题 */
  title: string;
  /** 计划落地的里程碑（如 "M2"） */
  milestone: string;
  /** 承载的 PRD 功能点编号（如 "F-4.2-1~9"） */
  features: string;
  /** 对接接口编号（如 "F1~F8"） */
  endpoints: string;
  /** 验收锚点（如 "AC-4.2-1~6"） */
  acceptance: string;
  /** 补充说明（可选） */
  note?: string;
}

/**
 * 渲染里程碑占位卡片。
 *
 * @param props StageNoticeProps
 * @returns 占位卡片 JSX
 */
export function StageNotice({
  title,
  milestone,
  features,
  endpoints,
  acceptance,
  note,
}: StageNoticeProps): ReactElement {
  return (
    <section className="stage-notice" aria-label={`${title}（待实现）`}>
      <span className="stage-notice__badge">{milestone} 待实现</span>
      <h2 className="page-title">{title}</h2>
      <dl className="stage-notice__meta">
        <div>
          <dt>承载功能点</dt>
          <dd>{features}</dd>
        </div>
        <div>
          <dt>对接接口</dt>
          <dd>{endpoints}</dd>
        </div>
        <div>
          <dt>验收锚点</dt>
          <dd>{acceptance}</dd>
        </div>
      </dl>
      {note ? <p className="stage-notice__note">{note}</p> : null}
    </section>
  );
}

export default StageNotice;
