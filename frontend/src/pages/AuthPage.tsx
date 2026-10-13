/**
 * pages/AuthPage.tsx —— 双端登录 / 注册页（M1 已接入 A1/A2 接口）
 *
 * 功能说明
 * --------
 * 用户端登录页（login.html）与管理端登录页（admin-login.html）共用本组件，
 * 通过 `side` 属性切换端差异：
 *
 * | 差异项            | 用户端（side=user）                  | 管理端（side=admin）                  |
 * |-------------------|--------------------------------------|----------------------------------------|
 * | 注册入口          | 有（立即注册 / 返回登录）             | **无**（管理员不开放自助注册，UI/UX 6.2）|
 * | 登录身份说明      | 「仅普通用户可登录本页」              | 「管理员（唯一可登录角色）」            |
 * | 守卫提示文案      | 请先登录 / 无权限访问用户端           | 请先登录管理后台 / 无权限访问管理后台    |
 * | 登录成功跳转      | /user.html                           | /admin.html                             |
 * | 写入的存储键      | eda.auth                             | eda.admin.auth                          |
 *
 * 实现要点
 * --------
 * 1. **守卫提示**由 URL 参数驱动（?from= / ?denied=1），与原型 login.html 行为一致（UI/UX 3.2）；
 * 2. **密码强度条**（.strength 三段）口径与后端 `auth_service.is_strong_password` 对齐：
 *    「≥8 位且同时含字母与数字」是**准入底线**，达标后再按长度/复杂度点亮第 2、3 段；
 * 3. **账号格式**在前端先行校验（手机号或邮箱），避免明显错误输入白白发起一次请求；
 * 4. **异常文案统一取服务端 message**：附录 A 的文案即最终展示文案（AC-4.1-3 防枚举文案
 *    由后端保证「账号不存在」与「密码错误」完全一致，前端不做二次加工，避免破坏防枚举语义）；
 * 5. **注册成功后自动登录**：UI/UX 6.1 主按钮文案为「注册并进入 AI 数据分析」，
 *    故注册成功后立即调用 A2 完成登录并进入应用。
 */

import type { FormEvent, ReactElement } from 'react';
import { useMemo, useState } from 'react';

import { BizApiError } from '@/api/client';
import { register } from '@/api/auth';
import { Icon } from '@/icons/Icon';
import { AuthSideMismatchError, useAdminAuth, useUserAuth } from '@/stores/auth';
import type { AuthSide } from '@/stores/auth';

import './auth-page.css';

/** AuthPage 属性 */
export interface AuthPageProps {
  /** 端标识：管理端时隐藏注册入口，文案改为「管理后台」 */
  side: AuthSide;
}

/** 表单模式 */
type Mode = 'login' | 'register';

/** 应用入口页（登录成功后跳转） */
const APP_ENTRY = {
  user: '/user.html',
  admin: '/admin.html',
} as const;

/** 账号格式（与后端 auth_service 的同名正则保持一致） */
const PHONE_PATTERN = /^1[3-9]\d{9}$/;
// 注意：字符类内把 `-` 放在末尾即表示字面量，无需转义（eslint no-useless-escape）
const EMAIL_PATTERN = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

/**
 * 判断账号是否为手机号或邮箱（与后端口径一致）。
 *
 * @param account 账号输入
 * @returns 合法返回 true
 */
function isValidAccount(account: string): boolean {
  const value = account.trim();
  return PHONE_PATTERN.test(value) || EMAIL_PATTERN.test(value);
}

/**
 * 判断密码是否满足准入底线（≥8 位且同时含字母与数字）。
 *
 * @param password 密码输入
 * @returns 达标返回 true
 */
function isPasswordAcceptable(password: string): boolean {
  return password.length >= 8 && /[A-Za-z]/.test(password) && /\d/.test(password);
}

/** 密码强度评估结果 */
interface StrengthResult {
  /** 点亮段数：0~3 */
  level: number;
  /** 文案：弱 / 中 / 强 */
  label: string;
}

/**
 * 评估密码强度（用于三段强度条的展示）。
 *
 * 说明：
 *   第 1 段只要求「非空」，用于给出即时反馈；第 2 段对应后端准入底线（≥8 位且含字母数字）；
 *   第 3 段为额外加成（长度 ≥12，或同时含大写与特殊字符）。
 *   **准入判定以 isPasswordAcceptable 为准**，本函数只负责视觉呈现，两者不可混淆。
 *
 * @param password 密码输入
 * @returns 点亮段数与文案
 */
function evaluateStrength(password: string): StrengthResult {
  if (!password) {
    return { level: 0, label: '' };
  }
  if (!isPasswordAcceptable(password)) {
    return { level: 1, label: '弱' };
  }
  const hasUpperAndSymbol = /[A-Z]/.test(password) && /[^A-Za-z0-9]/.test(password);
  if (password.length >= 12 || hasUpperAndSymbol) {
    return { level: 3, label: '强' };
  }
  return { level: 2, label: '中' };
}

/**
 * 把异常转换为页面可展示的提示文案。
 *
 * @param error 捕获到的异常
 * @returns 展示文案
 */
function toNoticeMessage(error: unknown): string {
  // 角色与入口不符：前端守卫的入口约束（UI/UX 6.2）
  if (error instanceof AuthSideMismatchError) {
    return error.message;
  }
  // 业务错误：直接采用后端文案（附录 A），保持防枚举等语义不被前端改写
  if (error instanceof BizApiError) {
    return error.message;
  }
  if (error instanceof Error && error.message) {
    return `网络异常，请稍后重试（${error.message}）`;
  }
  return '网络异常，请稍后重试';
}

/**
 * 登录 / 注册页组件。
 *
 * @param props AuthPageProps
 * @returns 页面 JSX
 */
export function AuthPage({ side }: AuthPageProps): ReactElement {
  const isAdmin = side === 'admin';
  /** 按端取对应的登录态 store（两端隔离，互不授权） */
  const authStore = isAdmin ? useAdminAuth : useUserAuth;

  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [notice, setNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);

  /** 守卫提示（?from= 请先登录；?denied=1 无权限访问），仅解析一次 */
  const guard = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return {
      needLogin: params.has('from'),
      denied: params.has('denied'),
    };
  }, []);

  const strength = evaluateStrength(password);

  /**
   * 切换登录 / 注册面板，并清空易冲突的状态。
   *
   * @param next 目标模式
   */
  function switchMode(next: Mode): void {
    setMode(next);
    setNotice('');
    setPassword('');
    setConfirmPassword('');
  }

  /**
   * 执行登录（A2），成功后跳转对应端入口。
   *
   * @param account 账号
   * @param pwd 密码
   */
  async function doLogin(account: string, pwd: string): Promise<void> {
    await authStore.getState().login({ username: account, password: pwd, remember });
    // 使用整页跳转（五入口为独立 HTML 页面，非 SPA 内部路由）
    window.location.href = APP_ENTRY[side];
  }

  /**
   * 表单提交处理。
   *
   * @param event 表单提交事件
   */
  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setNotice('');

    const account = username.trim();

    // ---- 前端预校验（减少无效请求；最终判定仍以后端为准） ----
    if (!isValidAccount(account)) {
      setNotice('请输入正确的手机号或邮箱');
      return;
    }
    if (!password) {
      setNotice('请输入密码');
      return;
    }

    if (mode === 'register') {
      if (!isPasswordAcceptable(password)) {
        setNotice('密码强度不足，需 8 位以上且同时包含字母与数字');
        return;
      }
      if (password !== confirmPassword) {
        setNotice('两次输入的密码不一致');
        return;
      }
    }

    setSubmitting(true);
    try {
      if (mode === 'register') {
        // A1 注册：成功后再走 A2 自动登录（UI/UX 6.1「注册并进入 AI 数据分析」）
        await register({ username: account, password });
      }
      await doLogin(account, password);
    } catch (error) {
      setNotice(toNoticeMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={handleSubmit} noValidate>
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

        {/* ---------- 守卫 / 越权提示（URL 参数驱动，UI/UX 3.2） ---------- */}
        {guard.needLogin ? (
          <div className="auth-alert auth-alert--warn" role="alert">
            <Icon name="warning" size={16} />
            <span>{isAdmin ? '请先登录管理后台' : '请先登录'}</span>
          </div>
        ) : null}
        {guard.denied ? (
          <div className="auth-alert auth-alert--warn" role="alert">
            <Icon name="warning" size={16} />
            <span>
              {isAdmin ? '无权限访问管理后台' : '无权限访问用户端'}
              <em className="auth-alert__hint">当前登录身份与该端不符，已返回登录入口</em>
            </span>
          </div>
        ) : null}

        {/* ---------- 登录身份说明（AC-4.1-4：双端角色区分） ---------- */}
        <p className="auth-role-hint">
          {isAdmin
            ? '登录身份：管理员（唯一可登录角色）· 本页不提供自助注册'
            : '登录身份：普通用户 · 管理员请前往管理端登录页'}
          {!isAdmin ? (
            <a className="auth-role-hint__link" href="/admin-login.html">
              管理端入口
            </a>
          ) : (
            <a className="auth-role-hint__link" href="/login.html">
              用户端入口
            </a>
          )}
        </p>

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

        {/* 密码强度条（仅注册模式，组件规范 .strength：3 段 4px 圆角条） */}
        {mode === 'register' ? (
          <div className="auth-strength">
            <div className="strength" aria-hidden="true">
              {[1, 2, 3].map((segment) => (
                <i key={segment} className={segment <= strength.level ? 'on' : undefined} />
              ))}
            </div>
            <span className="auth-strength__label">密码强度：{strength.label || '—'}</span>
          </div>
        ) : null}

        {/* 确认密码（仅注册模式） */}
        {mode === 'register' ? (
          <label className="auth-field" htmlFor="confirmPassword">
            <span className="auth-field__label">确认密码</span>
            <input
              id="confirmPassword"
              className="auth-field__input"
              type="password"
              autoComplete="new-password"
              placeholder="请再次输入密码"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </label>
        ) : null}

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

        <button className="auth-submit" type="submit" disabled={submitting}>
          {submitting
            ? '处理中…'
            : mode === 'login'
              ? isAdmin
                ? '登录管理后台'
                : '登录并进入 AI 数据分析'
              : '注册并进入 AI 数据分析'}
          {submitting ? null : <Icon name="arrow-right" size={16} />}
        </button>

        {/* 模式切换：管理端不提供注册入口（UI/UX 6.2） */}
        {!isAdmin ? (
          <div className="auth-switch">
            {mode === 'login' ? (
              <>
                还没有账号？
                <button type="button" className="auth-switch__link" onClick={() => switchMode('register')}>
                  立即注册
                </button>
              </>
            ) : (
              <>
                已有账号？
                <button type="button" className="auth-switch__link" onClick={() => switchMode('login')}>
                  返回登录
                </button>
              </>
            )}
          </div>
        ) : null}

        {/* 异常提示（含 AC-4.1-3 防枚举文案、AC-4.9-2 停用提示、重复注册与越权提示） */}
        {notice ? (
          <p className="auth-alert auth-alert--error" role="alert">
            <Icon name="warning" size={16} />
            <span>{notice}</span>
          </p>
        ) : null}

        <p className="auth-back">
          <a href="/">← 返回入口页</a>
        </p>
      </form>
    </div>
  );
}

export default AuthPage;
