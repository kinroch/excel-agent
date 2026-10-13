/**
 * pages/user/Sessions.tsx —— 会话管理（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「会话管理 /sessions」；UI/UX 6.6。
 * 将在 M5「会话与反馈」里程碑实现（S2~S5：列表 / 详情 / 重命名 / 删除）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 会话管理视图。
 *
 * @returns 占位卡片 JSX
 */
export function Sessions(): ReactElement {
  return (
    <StageNotice
      title="会话管理"
      milestone="M5"
      features="F-4.6-1~5（会话 CRUD/多轮上下文/检索）"
      endpoints="S2~S5"
      acceptance="AC-4.6-2、AC-4.6-3"
      note="落地内容：会话列表与检索、重命名、删除（Modal 二次确认，不可恢复操作强制确认）、绑定数据集标识。"
    />
  );
}

export default Sessions;
