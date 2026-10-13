/**
 * pages/user/Workspace.tsx —— 数据工作台（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「数据工作台 /workspace」；UI/UX 6.3 user.html · ai-workspace。
 * 将在 M2「文件接入与解析」里程碑实现（F1~F8 接口、三区块布局、上传进度与预览）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 数据工作台视图。
 *
 * @returns 占位卡片 JSX
 */
export function Workspace(): ReactElement {
  return (
    <StageNotice
      title="数据工作台"
      milestone="M2"
      features="F-4.2-1~9（上传/校验/解析/Sheet 结构/字段说明/预览/缺失值策略）"
      endpoints="F1~F8"
      acceptance="AC-4.2-1~6"
      note="落地内容：工作台三区块（数据集列表 / 上传区 / 体检与预览）、50MB 上限校验、解析进度轮询、多 Sheet 与合并单元格展开。"
    />
  );
}

export default Workspace;
