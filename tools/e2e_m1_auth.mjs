/**
 * tools/e2e_m1_auth.mjs —— M1 认证与权限 · 浏览器端到端验证
 *
 * 用途
 * ----
 * 用真实 Chrome 走查「双端登录 → 守卫分流 → 登出」链路，验证 UI/UX 3.2 守卫矩阵
 * 与 AC-4.1-1~5。静态检查与后端接口测试都**无法**覆盖这类前端跳转逻辑，
 * 因此本脚本是 M1 的验收证据之一。
 *
 * 关于「应用内路由」的说明
 * ------------------------
 * 五入口中的 user.html / admin.html 是**外壳页**；加载后 React Router 会按 UI/UX 3.4
 * 跳到应用内路由（用户端 /workspace，管理端 /dashboard）。因此本脚本断言的是
 * 「进入了正确端的应用内路由」，而非停留在 .html 文件名上。
 *
 * 前置条件
 * --------
 *   1. 后端已启动（默认 http://127.0.0.1:8000），且使用独立测试库；
 *   2. 前端开发服务器已启动（默认 http://127.0.0.1:5173）；
 *   3. 管理员账号已由 scripts/init_admin.py 创建（普通用户由脚本自动注册）。
 *
 * 运行方式
 * --------
 *   node tools/e2e_m1_auth.mjs
 *
 * 覆盖用例
 * --------
 *   C1 未登录访问 /user.html   → 跳 /login.html?from=user 且显示「请先登录」
 *   C2 未登录访问 /admin.html  → 跳 /admin-login.html?from=admin 且显示「请先登录管理后台」
 *   C3 用户端注册 → 自动登录 → 进入用户端应用，顶栏显示账号
 *   C4 普通用户访问 /admin.html → 跳 /admin-login.html?denied=1 显示「无权限访问管理后台」
 *   C5 普通用户在管理端登录    → 提示「该账号为普通用户，无权访问管理后台」（UI/UX 6.2）
 *   C6 越权令牌（本地 role 被篡改为 admin）访问 /user.html → 跳 /login.html?denied=1
 *   C7 登出                    → 清除登录态并回到 /login.html（AC-4.1-5）
 *   C8 管理员登录管理端        → 进入管理端应用，顶栏显示「管理员」
 *   C9 错误密码登录            → 停留在登录页并显示「账号或密码错误」（AC-4.1-3）
 *                                 —— 回归用例：401 拦截器不得劫持公开认证接口的失败响应
 */

import { createRequire } from 'node:module';

/**
 * 加载 puppeteer。
 *
 * 说明：
 *   本脚本位于仓库内，而 puppeteer 安装在**托管 Node 工作空间**的 node_modules 下。
 *   ESM 的 `import` 不读 NODE_PATH 环境变量，因此这里采用「先常规解析、
 *   失败则从工作空间目录用 createRequire 解析」的双路策略，
 *   使脚本既能被本地已装 puppeteer 的环境直接运行，也能在托管环境中运行。
 *
 * @returns {Promise<typeof import('puppeteer')>} puppeteer 模块
 */
async function loadPuppeteer() {
  try {
    const mod = await import('puppeteer');
    return mod.default ?? mod;
  } catch {
    const workspaceModules =
      process.env.NODE_MODULES_WORKSPACE ??
      'C:/Users/Administrator/.workbuddy/binaries/node/workspace/';
    const requireFromWorkspace = createRequire(`${workspaceModules.replace(/\/?$/, '/')}index.js`);
    return requireFromWorkspace('puppeteer');
  }
}

const puppeteer = await loadPuppeteer();

// ------------------------------------------------------------
// 配置
// ------------------------------------------------------------
const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:5173';
const CHROME_PATH =
  process.env.E2E_CHROME ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';

/** 普通用户（脚本自动注册，账号带时间戳避免重复） */
const USER_ACCOUNT = process.env.E2E_USER_ACCOUNT ?? `e2e${Date.now()}@example.com`;
const USER_PASSWORD = process.env.E2E_USER_PASSWORD ?? 'Passw0rd123';

/** 管理员（需由 scripts/init_admin.py 预先创建） */
const ADMIN_ACCOUNT = process.env.E2E_ADMIN_ACCOUNT ?? 'admin@example.com';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'Admin12345';

/** 各端应用内首页（UI/UX 3.4） */
const USER_HOME = '/workspace';
const ADMIN_HOME = '/dashboard';

// ------------------------------------------------------------
// 断言工具
// ------------------------------------------------------------
let passed = 0;
const failures = [];

/**
 * 记录一条断言结果。
 *
 * @param {string} name 用例名
 * @param {boolean} ok 是否通过
 * @param {string} detail 失败明细
 */
function check(name, ok, detail = '') {
  if (ok) {
    passed += 1;
    console.log(`  [PASS] ${name}`);
  } else {
    failures.push(`${name}${detail ? ` —— ${detail}` : ''}`);
    console.log(`  [FAIL] ${name}${detail ? ` —— ${detail}` : ''}`);
  }
}

/**
 * 等待页面路径以指定前缀开头（用于断言跳转结果）。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} prefix 路径前缀（如 '/login.html'）
 * @param {number} timeout 超时毫秒
 * @returns {Promise<string>} 最终 URL
 */
async function waitForPathPrefix(page, prefix, timeout = 10000) {
  await page.waitForFunction(
    (p) => (window.location.pathname + window.location.search).startsWith(p),
    { timeout },
    prefix,
  );
  return page.url();
}

/**
 * 读取页面可见文本。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<string>} body 文本
 */
async function bodyText(page) {
  return page.evaluate(() => document.body.innerText);
}

/**
 * 清空浏览器存储（模拟全新访客）。
 *
 * @param {import('puppeteer').Page} page 页面
 */
async function clearStorage(page) {
  await page.evaluate(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
}

/**
 * 读取某个存储键的值。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} key 键名
 * @returns {Promise<string | null>} 值
 */
async function readKey(page, key) {
  return page.evaluate((k) => window.localStorage.getItem(k), key);
}

/**
 * 在登录页填写账号密码并点击提交（不等待跳转，由调用方决定等待什么）。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} account 账号
 * @param {string} password 密码
 */
async function fillAndSubmit(page, account, password) {
  await page.waitForSelector('#username');
  await page.type('#username', account);
  await page.type('#password', password);
  await page.click('.auth-submit');
}

// ------------------------------------------------------------
// 主流程
// ------------------------------------------------------------
async function main() {
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);

  try {
    // 先访问一次站点根，取得同源上下文以便操作 localStorage
    await page.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded' });
    await clearStorage(page);

    // ---------------- C1 未登录访问用户端 ----------------
    console.log('\n[C1] 未登录访问 /user.html');
    await page.goto(`${BASE}/user.html`, { waitUntil: 'domcontentloaded' });
    let url = await waitForPathPrefix(page, '/login.html');
    check('重定向到 /login.html', url.includes('/login.html'), url);
    check('携带 ?from=user', url.includes('from=user'), url);
    check('显示「请先登录」', (await bodyText(page)).includes('请先登录'));

    // ---------------- C2 未登录访问管理端 ----------------
    console.log('\n[C2] 未登录访问 /admin.html');
    await page.goto(`${BASE}/admin.html`, { waitUntil: 'domcontentloaded' });
    url = await waitForPathPrefix(page, '/admin-login.html');
    check('重定向到 /admin-login.html', url.includes('/admin-login.html'), url);
    check('携带 ?from=admin', url.includes('from=admin'), url);
    check('显示「请先登录管理后台」', (await bodyText(page)).includes('请先登录管理后台'));

    // ---------------- C3 注册 + 自动登录 ----------------
    console.log('\n[C3] 用户端注册并自动登录');
    await page.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded' });
    await clearStorage(page);
    await page.reload({ waitUntil: 'domcontentloaded' });

    // 切到注册面板
    await page.click('.auth-switch__link');
    await page.waitForSelector('#confirmPassword');

    await page.type('#username', USER_ACCOUNT);
    await page.type('#password', USER_PASSWORD);
    await page.type('#confirmPassword', USER_PASSWORD);

    // 强度条应点亮（8 位以上含字母数字 → 至少 2 段）
    const litSegments = await page.$$eval('.strength i.on', (nodes) => nodes.length);
    check('密码强度条已点亮（≥2 段）', litSegments >= 2, `点亮 ${litSegments} 段`);

    await page.click('.auth-submit');
    url = await waitForPathPrefix(page, USER_HOME, 20000);
    check('注册后自动登录并进入用户端应用', url.includes(USER_HOME), url);
    const userHomeText = await bodyText(page);
    check('顶栏显示登录账号', userHomeText.includes(USER_ACCOUNT), userHomeText.slice(0, 150));
    check('顶栏显示角色为普通用户', userHomeText.includes('普通用户'));

    const storedUser = await readKey(page, 'eda.auth');
    check('登录态写入 eda.auth', !!storedUser && storedUser.includes('"role":"user"'));

    // ---------------- C4 普通用户访问管理端（越权） ----------------
    console.log('\n[C4] 普通用户访问 /admin.html');
    await page.goto(`${BASE}/admin.html`, { waitUntil: 'domcontentloaded' });
    url = await waitForPathPrefix(page, '/admin-login.html');
    check('重定向到 /admin-login.html', url.includes('/admin-login.html'), url);
    check('携带 ?denied=1', url.includes('denied=1'), url);
    check('显示「无权限访问管理后台」', (await bodyText(page)).includes('无权限访问管理后台'));

    // ---------------- C5 普通用户在管理端登录 ----------------
    console.log('\n[C5] 普通用户从管理端登录入口登录');
    await page.goto(`${BASE}/admin-login.html`, { waitUntil: 'domcontentloaded' });
    await fillAndSubmit(page, USER_ACCOUNT, USER_PASSWORD);
    await page.waitForSelector('.auth-alert--error', { timeout: 20000 });
    const deniedText = await bodyText(page);
    check(
      '提示「该账号为普通用户，无权访问管理后台」',
      deniedText.includes('该账号为普通用户，无权访问管理后台'),
      deniedText.slice(0, 220),
    );
    check('仍停留在 /admin-login.html', page.url().includes('/admin-login.html'), page.url());
    check('未写入管理端登录态', (await readKey(page, 'eda.admin.auth')) === null);

    // ---------------- C6 越权令牌（篡改本地 role） ----------------
    console.log('\n[C6] 本地 role 被篡改为 admin 后访问 /user.html');
    await page.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded' });
    // 伪造：把用户端登录态的 role 改成 admin（模拟绕过登录页直接改存储）
    const tampered = await page.evaluate(() => {
      const raw = window.localStorage.getItem('eda.auth');
      if (!raw) return false;
      const parsed = JSON.parse(raw);
      parsed.role = 'admin';
      window.localStorage.setItem('eda.auth', JSON.stringify(parsed));
      return true;
    });
    check('已成功篡改本地 role（前置条件）', tampered);
    await page.goto(`${BASE}/user.html`, { waitUntil: 'domcontentloaded' });
    url = await waitForPathPrefix(page, '/login.html');
    check('篡改 role 被守卫拦截并跳登录页', url.includes('/login.html'), url);
    check('携带 ?denied=1', url.includes('denied=1'), url);

    // ---------------- C7 登出 ----------------
    console.log('\n[C7] 登出');
    await page.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded' });
    await clearStorage(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await fillAndSubmit(page, USER_ACCOUNT, USER_PASSWORD);
    await waitForPathPrefix(page, USER_HOME, 20000);

    await page.click('.user-menu__logout');
    url = await waitForPathPrefix(page, '/login.html', 20000);
    check('登出后回到 /login.html', url.includes('/login.html'), url);
    check('用户端登录态已清除（eda.auth）', (await readKey(page, 'eda.auth')) === null);

    await page.goto(`${BASE}/user.html`, { waitUntil: 'domcontentloaded' });
    url = await waitForPathPrefix(page, '/login.html');
    check('登出后访问受保护页需重新登录', url.includes('/login.html'), url);

    // ---------------- C8 管理员登录管理端 ----------------
    console.log('\n[C8] 管理员登录管理端');
    await page.goto(`${BASE}/admin-login.html`, { waitUntil: 'domcontentloaded' });
    await clearStorage(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    const adminLoginText = await bodyText(page);
    check('管理端无注册入口', !adminLoginText.includes('立即注册'));

    await fillAndSubmit(page, ADMIN_ACCOUNT, ADMIN_PASSWORD);
    url = await waitForPathPrefix(page, ADMIN_HOME, 20000);
    check('管理员登录成功并进入管理端应用', url.includes(ADMIN_HOME), url);
    const adminHomeText = await bodyText(page);
    check('顶栏显示管理员账号', adminHomeText.includes(ADMIN_ACCOUNT), adminHomeText.slice(0, 150));
    check('顶栏显示角色为管理员', adminHomeText.includes('管理员'));

    const storedAdmin = await readKey(page, 'eda.admin.auth');
    check('登录态写入 eda.admin.auth', !!storedAdmin && storedAdmin.includes('"role":"admin"'));
    check('管理端登录不污染用户端登录态', (await readKey(page, 'eda.auth')) === null);

    // ---------------- C9 错误密码（401 不得被拦截器劫持） ----------------
    console.log('\n[C9] 错误密码登录应停留在登录页并提示');
    await page.goto(`${BASE}/admin-login.html`, { waitUntil: 'domcontentloaded' });
    await clearStorage(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await fillAndSubmit(page, ADMIN_ACCOUNT, 'Wrong12345');
    await page.waitForSelector('.auth-alert--error', { timeout: 20000 });
    const wrongPwdText = await bodyText(page);
    check('显示「账号或密码错误」', wrongPwdText.includes('账号或密码错误'), wrongPwdText.slice(0, 200));
    check(
      '未被 401 拦截器跳转（仍停留在 /admin-login.html）',
      page.url().includes('/admin-login.html'),
      page.url(),
    );
  } finally {
    await browser.close();
  }

  // ------------------------------------------------------------
  // 汇总
  // ------------------------------------------------------------
  console.log('\n' + '='.repeat(62));
  if (failures.length === 0) {
    console.log(`[PASS] M1 浏览器端到端验证全部通过（共 ${passed} 项断言）`);
    console.log('='.repeat(62));
    return 0;
  }
  console.log(`[FAIL] ${failures.length} 项断言失败（通过 ${passed} 项）：`);
  failures.forEach((f) => console.log('  - ' + f));
  console.log('='.repeat(62));
  return 1;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error('\n[ERROR] E2E 执行异常：', error);
    process.exit(1);
  });
