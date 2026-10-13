/**
 * pages/IndexPage.tsx —— 交付入口页（五入口中的 index.html）
 *
 * 功能说明：
 *   1. 作为部署后的默认访问页，提供进入「用户端」与「管理端」的入口；
 *   2. 展示后端健康检查结果，便于部署验收时一眼确认服务与数据库状态（M0 判据）；
 *   3. 全部样式取自设计令牌，不使用任何硬编码色值。
 *
 * 对应：技术文档 2.2 容器视图（index.html 交付入口）。
 */

import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

import { Icon } from '@/icons/Icon';

import './index-page.css';

/** 健康检查返回的数据结构 */
interface HealthData {
  status: string;
  version: string;
  database: boolean;
}

/** 后端健康检查接口地址（与 core/config 的 api_prefix 一致） */
const HEALTH_URL = '/api/v1/health';

/**
 * 入口页组件。
 *
 * @returns 入口页 JSX
 */
export function IndexPage(): ReactElement {
  // 后端连通状态：null = 检测中
  const [health, setHealth] = useState<HealthData | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // 探测后端健康检查接口；失败仅提示，不阻断页面渲染
    fetch(HEALTH_URL)
      .then((resp) => resp.json())
      .then((body: { code: number; data: HealthData | null }) => {
        if (body.code === 0 && body.data) setHealth(body.data);
        else setFailed(true);
      })
      .catch(() => setFailed(true));
  }, []);

  return (
    <div className="index-page">
      <header className="index-page__hero">
        <h1 className="index-page__title">Excel 对话式数据分析智能体</h1>
        <p className="index-page__desc">
          通过自然语言对话，完成 Excel 数据的读取、分析、统计与可视化。
        </p>
      </header>

      <div className="index-page__cards">
        {/* 用户端入口 */}
        <a className="entry-card" href="/user.html">
          <Icon name="workspace" size={28} />
          <div className="entry-card__title">用户端</div>
          <div className="entry-card__desc">上传数据集 · 对话式分析 · 生成与导出报告</div>
          <span className="entry-card__action">
            进入用户端 <Icon name="arrow-right" size={16} />
          </span>
        </a>

        {/* 管理端入口 */}
        <a className="entry-card" href="/admin.html">
          <Icon name="dashboard" size={28} />
          <div className="entry-card__title">管理后台</div>
          <div className="entry-card__desc">全局看板 · 用户管理 · 会话记录与审计</div>
          <span className="entry-card__action">
            进入管理后台 <Icon name="arrow-right" size={16} />
          </span>
        </a>

        {/* 独立登录入口 */}
        <a className="entry-card" href="/login.html">
          <Icon name="users" size={28} />
          <div className="entry-card__title">登录 / 注册</div>
          <div className="entry-card__desc">用户端账号登录与注册（M1 阶段接入认证）</div>
          <span className="entry-card__action">
            前往登录 <Icon name="arrow-right" size={16} />
          </span>
        </a>
      </div>

      {/* 服务状态条：部署验收辅助信息 */}
      <footer className="index-page__status">
        <span>构建阶段：<strong>M0 环境与骨架</strong></span>
        <span>
          后端状态：
          {health
            ? ` 已连通（v${health.version}，数据库${health.database ? '正常' : '不可用'}）`
            : failed
              ? ' 未连通（请确认后端已启动于 127.0.0.1:8000）'
              : ' 检测中…'}
        </span>
      </footer>
    </div>
  );
}

export default IndexPage;
