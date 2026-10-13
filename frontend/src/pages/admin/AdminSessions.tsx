/**
 * pages/admin/AdminSessions.tsx —— 会话记录（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「会话记录 /sessions（admin）」；UI/UX 6.10。
 * 将在 M6「统计与管理后台」里程碑实现（AD3/AD4：会话列表 / 会话详情）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 管理端会话记录视图。
 *
 * @returns 占位卡片 JSX
 */
export function AdminSessions(): ReactElement {
  return (
    <StageNotice
      title="会话记录"
      milestone="M6"
      features="F-4.9-3、F-4.9-4（全量会话检索、会话详情查看）"
      endpoints="AD3 / AD4"
      acceptance="AC-4.9-3"
      note="落地内容：全量会话列表（按用户/时间检索）、会话详情（脱敏后下发）、合规提示条、查看行为写入审计日志。"
    />
  );
}

export default AdminSessions;
