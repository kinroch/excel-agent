/**
 * pages/admin/Users.tsx —— 用户管理（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「用户管理 /users」；UI/UX 6.9。
 * 将在 M6「统计与管理后台」里程碑实现（AD1/AD2：用户列表 / 启停用）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 用户管理视图。
 *
 * @returns 占位卡片 JSX
 */
export function Users(): ReactElement {
  return (
    <StageNotice
      title="用户管理"
      milestone="M6"
      features="F-4.9-1、F-4.9-2（用户列表检索、启停用与审计）"
      endpoints="AD1 / AD2"
      acceptance="AC-4.9-1、AC-4.9-2"
      note="落地内容：用户列表（分页 + 状态筛选）、停用/启用二次确认、禁止操作管理员账号（6002）、操作写入审计日志。"
    />
  );
}

export default Users;
