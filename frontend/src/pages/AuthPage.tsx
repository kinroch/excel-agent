/**
 * pages/AuthPage.tsx —— 双端登录 / 注册页骨架（M0 骨架占位）
 *
 * 功能说明：
 *   1. 用户端登录页（login.html）与管理端登录页（admin-login.html）共用同一组件，
 *      通过 side 属性切换：管理端无注册入口（UI/UX 6.2）；
 *   2. M0 阶段仅实现表单结构与令牌化样式，**不发起任何认证请求**；
 *   3. 提交时在页面内给出「M1 接入」提示，避免误导评审者认为功能已完成。
 *
 * 对应：技术文档 6.3 守卫矩阵；UI/UX 6.1 / 6.2；
 *      将在 M1「认证与权限」里程碑接入 A1(注册) / A2(登录) 接口。
 */

import type { FormEvent, ReactElement } from 'react';
import { useState } from 'react';

import { Icon } from '@/icons/Icon';

import './auth-page.css';

/** AuthPage 属性 */
export interface AuthPageProps {
  /** 端标识：管理端时隐藏注册入口，文案改为「管理后台」 */
  side: 'user' | 'admin';
}

/** 表单模式 */
type Mode = 'login' | 'register';

/**
 * 登录 / 注册页组件。
 *
 * @param props AuthPageProps
 * @returns 页面 JSX
 */
export function AuthPage({ side }: AuthPageProps): ReactElement {
  const isAdmin = side === 'admin';
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [notice, setNotice] = useState('');

  /**
   * 表单提交处理（M0：不发起请求，仅提示）。
   *
   * @param event 表单提交事件
   */
  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    // 【M1 接入点】此处应调用 A1/A2 接口，成功后将登录态写入 stores/auth
    const action = mode === 'login' ? '登录' : '注册';
    const code = mode === 'login' ? 'A2' : 'A1';
    setNotice(`${action}功能将在 M1「认证与权限」阶段接入（${code} 接口）。`);
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={handleSubmit}>
        <div className="auth-card__brand">
          <div className="auth-card__logo" aria-hidden="true">
            EDA
          </div>
          <h1 className="auth-card__title">
            {isAdmin ? '管理后台登录' : mode === 'login' ? '登录' : '注册'}
          </h1>
          <p className="page-subtitle">
            {isAdmin ? '仅限管理员账号访问' : 'Excel 对话式数据分析智能体'}
          </p>
        </div>

        {/* 账号输入 */}
        <label className="auth-field" htmlFor="username">
          <span className="auth-field__label">账号</span>
          <input
            id="username"
            className="auth-field__input"
            type="text"
            autoComplete="username"
            placeholder="手机号或邮箱"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>

        {/* 密码输入 */}
        <label className="auth-field" htmlFor="password">
          <span className="auth-field__label">密码</span>
          <input
            id="password"
            className="auth-field__input"
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            placeholder={mode === 'register' ? '8 位以上，需含字母与数字' : '请输入密码'}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>

        {/* 保持登录（仅登录模式） */}
        {mode === 'login' ? (
          <label className="auth-checkbox" htmlFor="remember">
            <input
              id="remember"
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            <span>保持登录状态（7 天）</span>
          </label>
        ) : null}

        <button className="auth-submit" type="submit">
          {mode === 'login' ? '登录' : '注册'}
          <Icon name="arrow-right" size={16} />
        </button>

        {/* 模式切换：管理端不提供注册入口（UI/UX 6.2） */}
        {!isAdmin ? (
          <div className="auth-switch">
            {mode === 'login' ? (
              <>
                还没有账号？
                <button type="button" className="auth-switch__link" onClick={() => setMode('register')}>
                  立即注册
                </button>
              </>
            ) : (
              <>
                已有账号？
                <button type="button" className="auth-switch__link" onClick={() => setMode('login')}>
                  返回登录
                </button>
              </>
            )}
          </div>
        ) : null}

        {/* 阶段提示（M0 骨架期专用，M1 落地后移除） */}
        {notice ? <p className="auth-notice">{notice}</p> : null}

        <p className="auth-back">
          <a href="/">← 返回入口页</a>
        </p>
      </form>
    </div>
  );
}

export default AuthPage;
