/**
 * tools/e2e_m2_files.mjs —— M2 数据接入与文件解析 · 浏览器端到端验证
 *
 * 用途
 * ----
 * 用真实 Chrome 走查「上传 → 异步解析 → Sheet 切换 → 字段说明 → 数据质量 →
 * 分页预览 → 缺失值策略 → 拒收 → 删除二次确认」全链路，验证 AC-4.2-1~6 与
 * AC-4.7-2。这些断言横跨「前端交互 + 轮询时序 + 后端解析产物」三层，
 * 静态检查（tsc/eslint）与接口级测试（pytest）都无法覆盖其组合行为，
 * 因此本脚本是 M2 的验收证据之一。
 *
 * 与前序脚本的关系
 * ----------------
 *   e2e_m1_auth.mjs   只覆盖「登录/守卫/登出」，不涉及数据；
 *   本脚本          **自带**用户注册（复用 M1 的登录页交互），
 *                    因此可独立运行，不依赖 e2e_m1_auth.mjs 的执行结果。
 *
 * 样本来源
 * --------
 *   样本由 tools/fixtures/make_e2e_fixture.py 生成（确定性、可人工复核），
 *   本脚本在启动时调用该脚本把样本写到 tmp/ 目录，用完即删。
 *   样本的每一项预期值都在该 py 文件的模块 docstring 中写明，本脚本的断言与之严格对应。
 *
 * 前置条件
 * --------
 *   1. 后端已启动（默认 http://127.0.0.1:8000），且使用独立测试库；
 *   2. 前端开发服务器已启动（默认 http://127.0.0.1:5173）；
 *   3. 工作空间虚拟环境存在（默认 ../../.venv，用于生成样本）。
 *
 * 运行方式
 * --------
 *   node tools/e2e_m2_files.mjs
 *
 * 可选环境变量
 * ------------
 *   E2E_BASE           前端地址，默认 http://127.0.0.1:5173
 *   E2E_CHROME         Chrome 可执行文件路径
 *   E2E_PYTHON         样本生成所用 python，默认 <工作空间根>/.venv/Scripts/python.exe
 *   E2E_USER_ACCOUNT / E2E_USER_PASSWORD   用户账号（默认带时间戳自动注册）
 *
 * 覆盖用例
 * --------
 *   C1  注册并自动登录 → 进入 /workspace，工作台页头与「上传文件」按钮就位
 *   C2  样本生成（前置）
 *   C3  F1 上传 xlsx：文件入列、上传后自动选中、F2 徽标转为「已解析 · 2 Sheets」
 *   C4  F5 解析处理信息行：表头识别在第 2 行 / 多级表头拼接 / 合并单元格展开（AC-4.2-4/6）
 *   C5  F5 Sheet 切换：空表被跳过（3 Sheet 样本只出 2 个），切 Sheet 后结构与字段同步
 *   C6  F6 字段说明：多级表头列名、数据类型、缺失率、敏感字段脱敏标记（AC-4.2-6、AC-4.7-2）
 *   C7  F3 数据质量小结五项与样本预期完全一致
 *   C8  F7 数据预览：分页、页码文案、合并单元格向下填充、缺失值渲染为「（缺失）」（AC-4.2-5）
 *   C9  F8 缺失值策略切换：提示 → 剔除 → 提示，说明文案随之更新
 *   C10 CSV 编码探测：GBK 文件识别为 GB18030（AC-4.2-6）
 *   C11 F1 拒收非法格式：红色警示条给出具体原因（AC-4.2-3）
 *   C12 F4 删除二次确认：取消不生效、确认后列表与详情区同步清空（AC-4.2-2）
 */

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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
/** 仓库根（excel-agent/），用于定位样本生成脚本与临时目录 */
const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url));

const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:5173';
const CHROME_PATH =
  process.env.E2E_CHROME ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';

/** 样本生成所用解释器：工作空间根 .venv（与后端运行环境一致，保证 openpyxl 可用） */
const PYTHON =
  process.env.E2E_PYTHON ?? path.resolve(REPO_ROOT, '..', '.venv', 'Scripts', 'python.exe');

/** 样本落盘目录：放在仓库 tmp/ 下（.gitignore 已忽略），运行结束即清理 */
const FIXTURE_DIR = path.join(REPO_ROOT, 'tmp', 'e2e-m2-fixtures');

/** 用户账号（默认带时间戳，保证每次运行都是全新账号、文件列表从空开始） */
const USER_ACCOUNT = process.env.E2E_USER_ACCOUNT ?? `e2e_m2_${Date.now()}@example.com`;
const USER_PASSWORD = process.env.E2E_USER_PASSWORD ?? 'Passw0rd123';

/** 用户端应用内首页（UI/UX 3.4） */
const USER_HOME = '/workspace';

/** 样本文件名（与 make_e2e_fixture.py 严格对应） */
const SAMPLE_XLSX = 'e2e_sales.xlsx';
const SAMPLE_CSV = 'e2e_cities_gbk.csv';
const SAMPLE_TXT = 'e2e_invalid.txt';

/**
 * 样本预期指标（与 make_e2e_fixture.py 的模块 docstring 一一对应）。
 *
 * 集中定义在此处而非散落在断言里，便于评审时一次核对全部数值口径。
 */
const EXPECT = {
  fileLevel: {
    rowTotal: 64,
    colTotal: 9,
    missingCells: 34,
    highMissingFields: 1,
    mergedCells: 4,
  },
  sheetSales: { name: '销售明细', rows: 60, cols: 6, headerRow: 2, sheetCount: 2 },
  sheetCity: { name: '部门对照', rows: 4, cols: 3, headerRow: 1 },
  csv: { name: 'e2e_cities_gbk', rows: 10, cols: 4, encoding: 'GB18030' },
};

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
 * @param {string} prefix 路径前缀（如 '/workspace'）
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
 * 通过隐藏的 file input 上传文件。
 *
 * 说明：
 *   用 CDP 直接给 input 设文件，会触发 change 事件，效果与用户点选一致；
 *   且**不受 accept 属性限制**，因此这里可以故意上传 .txt 来验证前端拒收分支。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} filePath 本地文件绝对路径
 * @returns {Promise<boolean>} 是否成功设置文件
 */
async function uploadFile(page, filePath) {
  const input = await page.$('input.ws-hidden-input');
  if (!input) return false;
  await input.uploadFile(filePath);
  return true;
}

// ---- 以下选择器辅助函数都按「语义容器」定位，避免受页面其它区块同名类名干扰 ----

/**
 * 读取文件列表中某个文件所在行的文本。
 *
 * 说明：
 *   页面里有多张 `.ws-table`（文件列表 / 字段说明 / 数据预览），
 *   因此一律以「表头含解析状态」定位文件列表表，再按文件名命中行，
 *   不使用 `querySelector` 的「第一个匹配」这种脆弱写法。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} filename 文件名
 * @returns {Promise<string | null>} 行文本；不存在返回 null
 */
async function fileRowText(page, filename) {
  return page.evaluate((name) => {
    const tables = [...document.querySelectorAll('.ws-table')];
    const listTable = tables.find((t) => t.querySelector('thead')?.innerText.includes('解析状态'));
    if (!listTable) return null;
    const row = [...listTable.querySelectorAll('tbody tr')].find((r) => r.innerText.includes(name));
    return row ? row.innerText : null;
  }, filename);
}

/**
 * 等待文件列表中某个文件的解析徽标出现指定文案。
 *
 * 说明：
 *   解析是**异步**的（F1 返回 202 后在后台线程解析），且完成时间不确定，
 *   因此这里用轮询式等待而不是固定 sleep —— 既快又不 flaky。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} filename 文件名
 * @param {string} expected 徽标应包含的文案
 * @param {number} timeout 超时毫秒
 */
async function waitForBadge(page, filename, expected, timeout = 30000) {
  await page.waitForFunction(
    (name, exp) => {
      const tables = [...document.querySelectorAll('.ws-table')];
      const listTable = tables.find((t) => t.querySelector('thead')?.innerText.includes('解析状态'));
      if (!listTable) return false;
      const row = [...listTable.querySelectorAll('tbody tr')].find((r) => r.innerText.includes(name));
      if (!row) return false;
      const badge = row.querySelector('.ws-badge');
      return !!badge && badge.innerText.includes(exp);
    },
    { timeout },
    filename,
    expected,
  );
}

/**
 * 点击文件列表中某个文件所在行的操作按钮（查看 / 删除）。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} filename 文件名
 * @param {string} action 按钮文案
 * @returns {Promise<boolean>} 是否点到
 */
async function clickRowAction(page, filename, action) {
  return page.evaluate(
    (name, act) => {
      const tables = [...document.querySelectorAll('.ws-table')];
      const listTable = tables.find((t) =>
        t.querySelector('thead')?.innerText.includes('解析状态'),
      );
      if (!listTable) return false;
      const row = [...listTable.querySelectorAll('tbody tr')].find((r) => r.innerText.includes(name));
      if (!row) return false;
      const btn = [...row.querySelectorAll('button')].find((b) => b.innerText.trim() === act);
      if (!btn) return false;
      btn.click();
      return true;
    },
    filename,
    action,
  );
}

/**
 * 读取某个面板（以面板标题唯一定位）的全部文本。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} title 面板标题（如「Sheet 结构与字段说明」）
 * @returns {Promise<string | null>} 面板文本
 */
async function panelText(page, title) {
  return page.evaluate((t) => {
    const panels = [...document.querySelectorAll('.ws-panel')];
    const hit = panels.find((p) => p.querySelector('.ws-p-title')?.innerText.trim() === t);
    return hit ? hit.innerText : null;
  }, title);
}

/**
 * 读取「Sheet 切换」分段控件的按钮文案数组。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<string[]>} Sheet 名列表
 */
async function sheetTabs(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('.ws-segmented[aria-label="Sheet 切换"] button')].map((b) =>
      b.innerText.trim(),
    ),
  );
}

/**
 * 切换 Sheet（点击对应分段按钮）。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} name Sheet 名
 * @returns {Promise<boolean>} 是否点到
 */
async function clickSheetTab(page, name) {
  return page.evaluate((target) => {
    const buttons = [...document.querySelectorAll('.ws-segmented[aria-label="Sheet 切换"] button')];
    const btn = buttons.find((b) => b.innerText.trim() === target);
    if (!btn) return false;
    btn.click();
    return true;
  }, name);
}

/**
 * 读取字段说明表的行数据（每行 5 列：字段名 / 数据类型 / 示例值 / 缺失率 / 推断依据）。
 *
 * 说明：
 *   按表头含「推断依据」定位，避免与文件列表表、预览表混淆。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<string[][]>} 行数组
 */
async function fieldRows(page) {
  return page.evaluate(() => {
    const tables = [...document.querySelectorAll('.ws-table')];
    const t = tables.find((el) => el.querySelector('thead')?.innerText.includes('推断依据'));
    if (!t) return [];
    return [...t.querySelectorAll('tbody tr')].map((tr) =>
      [...tr.querySelectorAll('td')].map((td) => td.innerText.trim().replace(/\s+/g, ' ')),
    );
  });
}

/**
 * 读取数据质量小结的 stat-strip 键值对。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<Record<string, string>>} { 指标名: 数值文本 }
 */
async function statStrip(page) {
  return page.evaluate(() => {
    const out = {};
    for (const stat of document.querySelectorAll('.ws-stat')) {
      const key = stat.querySelector('.ws-stat-k')?.innerText.trim();
      const value = stat.querySelector('.ws-stat-v')?.innerText.trim();
      if (key) out[key] = value;
    }
    return out;
  });
}

/**
 * 读取预览表某单元格的文本。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {number} rowIdx 行下标（0 起，不含表头）
 * @param {number} colIdx 列下标（0 起，第 0 列为行号）
 * @returns {Promise<string | null>} 单元格文本
 */
async function previewCell(page, rowIdx, colIdx) {
  return page.evaluate(
    (r, c) => {
      const table = document.querySelector('.ws-table--preview');
      if (!table) return null;
      const row = table.querySelectorAll('tbody tr')[r];
      if (!row) return null;
      const cell = row.querySelectorAll('td')[c];
      return cell ? cell.innerText.trim() : null;
    },
    rowIdx,
    colIdx,
  );
}

/**
 * 读取预览表当前页的行数与列数。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<{rows: number, cols: number, headers: string[]}>} 表格规模与表头
 */
async function previewShape(page) {
  return page.evaluate(() => {
    const table = document.querySelector('.ws-table--preview');
    if (!table) return { rows: 0, cols: 0, headers: [] };
    return {
      rows: table.querySelectorAll('tbody tr').length,
      cols: table.querySelectorAll('thead th').length,
      headers: [...table.querySelectorAll('thead th')].map((th) => th.innerText.trim()),
    };
  });
}

/**
 * 读取预览区页码文案（形如「第 1 / 2 页 · 每页 50 行 · 共 60 行」）。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<string | null>} 页码文案
 */
async function pagerText(page) {
  return page.evaluate(() => {
    const pager = document.querySelector('.ws-pager');
    return pager ? pager.innerText.replace(/\s+/g, ' ').trim() : null;
  });
}

/**
 * 点击预览区的翻页按钮。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} label 按钮文案（上一页 / 下一页）
 * @returns {Promise<boolean>} 是否点到
 */
async function clickPager(page, label) {
  return page.evaluate((text) => {
    const pager = document.querySelector('.ws-pager');
    if (!pager) return false;
    const btn = [...pager.querySelectorAll('button')].find((b) => b.innerText.includes(text));
    if (!btn) return false;
    btn.click();
    return true;
  }, label);
}

/**
 * 读取预览区翻页按钮的禁用态。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} label 按钮文案
 * @returns {Promise<boolean | null>} true 表示禁用
 */
async function pagerDisabled(page, label) {
  return page.evaluate((text) => {
    const pager = document.querySelector('.ws-pager');
    if (!pager) return null;
    const btn = [...pager.querySelectorAll('button')].find((b) => b.innerText.includes(text));
    return btn ? btn.disabled : null;
  }, label);
}

/**
 * 点击缺失值策略分段控件。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} label 策略文案（提示 / 填充 / 剔除）
 * @returns {Promise<boolean>} 是否点到
 */
async function clickPolicy(page, label) {
  return page.evaluate((text) => {
    const buttons = [
      ...document.querySelectorAll('.ws-segmented[aria-label="缺失值处理策略"] button'),
    ];
    const btn = buttons.find((b) => b.innerText.trim() === text);
    if (!btn) return false;
    btn.click();
    return true;
  }, label);
}

/**
 * 读取当前缺失值策略分段控件中处于激活态的按钮文案。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<string | null>} 激活按钮文案
 */
async function activePolicy(page) {
  return page.evaluate(() => {
    const btn = document.querySelector(
      '.ws-segmented[aria-label="缺失值处理策略"] button.is-active',
    );
    return btn ? btn.innerText.trim() : null;
  });
}

/**
 * 等待「Sheet 结构与字段说明」面板**完全就绪**。
 *
 * 为什么需要它（本脚本最容易写错的地方）：
 *   文件解析完成（F2 徽标变「已解析」）**不等于**区块 02 已渲染完毕。
 *   区块 02 还需要再发两次请求：F5（Sheet 列表）→ F6（字段说明）。
 *   若在徽标刚变绿时就断言，会读到「正在读取 Sheet 结构…」这种中间态，
 *   表现为一连串莫名其妙的前端断言失败（而实际功能完全正常）。
 *   因此所有针对区块 02 的断言都必须先经过本函数。
 *
 * 判据：Sheet 分段控件里出现了目标 Sheet 名 **且** 字段说明表已渲染出数据行。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} sheetName 期望的 Sheet 名
 * @param {number} timeout 超时毫秒
 */
async function waitForSheetPanel(page, sheetName, timeout = 25000) {
  await page.waitForFunction(
    (name) => {
      const buttons = [
        ...document.querySelectorAll('.ws-segmented[aria-label="Sheet 切换"] button'),
      ];
      if (!buttons.some((b) => b.innerText.trim() === name)) return false;
      const tables = [...document.querySelectorAll('.ws-table')];
      const fieldTable = tables.find((t) =>
        t.querySelector('thead')?.innerText.includes('推断依据'),
      );
      return !!fieldTable && fieldTable.querySelectorAll('tbody tr').length > 0;
    },
    { timeout },
    sheetName,
  );
}

/**
 * 读取「Sheet 结构与字段说明」面板内的解析处理信息行文本。
 *
 * 说明：
 *   页面上有多条 `.ws-note-line`（解析处理 / 当前策略 / 敏感脱敏提示），
 *   直接 `querySelector` 取第一条会随渲染时序而指向不同元素，因此按面板标题限定。
 *
 * @param {import('puppeteer').Page} page 页面
 * @returns {Promise<string | null>} 解析处理信息行文本
 */
async function sheetPanelNote(page) {
  return page.evaluate(() => {
    const panels = [...document.querySelectorAll('.ws-panel')];
    const panel = panels.find(
      (p) => p.querySelector('.ws-p-title')?.innerText.trim() === 'Sheet 结构与字段说明',
    );
    return panel?.querySelector('.ws-note-line')?.innerText ?? null;
  });
}

/**
 * 等待数据预览表出现指定列名。
 *
 * 说明：
 *   切换文件或 Sheet 后，F7 会重新请求；`placeholderData: keepPreviousData` 会先
 *   保留上一份数据（刻意不清空、不闪白）。这意味着「表格里有数据」不能作为
 *   「新数据已到位」的判据，必须等**列名**变成目标 Sheet 的列名。
 *
 * @param {import('puppeteer').Page} page 页面
 * @param {string} columnName 期望出现的列名
 * @param {number} timeout 超时毫秒
 */
async function waitForPreviewColumn(page, columnName, timeout = 25000) {
  await page.waitForFunction(
    (name) => {
      const table = document.querySelector('.ws-table--preview');
      if (!table) return false;
      return [...table.querySelectorAll('thead th')].some((th) => th.innerText.trim() === name);
    },
    { timeout },
    columnName,
  );
}

// ------------------------------------------------------------
// 主流程
// ------------------------------------------------------------
async function main() {
  // ---------------- C0 前置：探活 ----------------
  console.log('\n[C0] 前置检查');
  try {
    const resp = await fetch(`${BASE}/api/v1/health`);
    const payload = await resp.json();
    check('后端健康检查可达', resp.ok && payload?.data?.status === 'ok', JSON.stringify(payload));
  } catch (error) {
    console.error(
      `\n[ERROR] 无法访问 ${BASE}/api/v1/health —— 请先启动后端（uvicorn）与前端（vite dev）。\n        原因：${error.message}`,
    );
    return 1;
  }

  // ---------------- C2 样本生成（先做，失败就不必开浏览器） ----------------
  console.log('\n[C2] 生成端到端样本');
  if (!fs.existsSync(PYTHON)) {
    console.error(`[ERROR] 未找到 python：${PYTHON}\n        可通过 E2E_PYTHON 指定。`);
    return 1;
  }
  try {
    execFileSync(
      PYTHON,
      [path.join(REPO_ROOT, 'tools', 'fixtures', 'make_e2e_fixture.py'), '--out', FIXTURE_DIR],
      { stdio: 'inherit' },
    );
    check('样本生成成功', fs.existsSync(path.join(FIXTURE_DIR, SAMPLE_XLSX)));
  } catch (error) {
    console.error('[ERROR] 样本生成失败：', error.message);
    return 1;
  }

  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(20000);

  try {
    // ---------------- C1 注册并进入工作台 ----------------
    console.log('\n[C1] 注册新用户并进入数据工作台');
    await page.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded' });
    await clearStorage(page);
    await page.reload({ waitUntil: 'domcontentloaded' });

    await page.click('.auth-switch__link');
    await page.waitForSelector('#confirmPassword');
    await page.type('#username', USER_ACCOUNT);
    await page.type('#password', USER_PASSWORD);
    await page.type('#confirmPassword', USER_PASSWORD);
    await page.click('.auth-submit');

    let url = await waitForPathPrefix(page, USER_HOME, 25000);
    check('注册后进入 /workspace', url.includes(USER_HOME), url);
    // 列表首屏会先渲染「正在加载文件列表…」，必须等它落地再判空态，否则会误报
    await page.waitForFunction(
      () => !document.body.innerText.includes('正在加载文件列表'),
      { timeout: 15000 },
    );
    const workspaceText = await bodyText(page);
    check('页头显示「数据工作台」', workspaceText.includes('数据工作台'));
    check('存在「上传文件」主按钮', workspaceText.includes('上传文件'));
    check('空列表给出引导文案', workspaceText.includes('暂无文件'), workspaceText.replace(/\s+/g, ' ').slice(0, 200));
    check('可分析文件计数就位', /已上传 \d+ 个可分析文件/.test(workspaceText.replace(/\s+/g, ' ')));

    // ---------------- C3 上传 xlsx ----------------
    console.log('\n[C3] F1 上传 xlsx 并等待异步解析');
    check('隐藏 file input 存在', await uploadFile(page, path.join(FIXTURE_DIR, SAMPLE_XLSX)));
    // 等到「文件名入列」或「警示条出现」二者之一：前者是成功路径，后者能直接
    // 把后端的拒绝原因打出来，避免只看到一句干巴巴的 waitForFunction 超时。
    await page
      .waitForFunction(
        (name) => document.body.innerText.includes(name) || !!document.querySelector('.ws-notice--warn'),
        { timeout: 25000 },
        SAMPLE_XLSX,
      )
      .catch(() => {});
    const uploadNotice = await page
      .$eval('.ws-notice--warn', (el) => el.innerText.replace(/\s+/g, ' '))
      .catch(() => null);
    check('上传被受理（未被拒收）', uploadNotice === null, uploadNotice ?? '');
    check('文件出现在文件列表', (await fileRowText(page, SAMPLE_XLSX)) !== null);
    await waitForBadge(page, SAMPLE_XLSX, '已解析');
    const badgeText = await fileRowText(page, SAMPLE_XLSX);
    check(
      `徽标为「已解析 · ${EXPECT.sheetSales.sheetCount} Sheets」`,
      (badgeText ?? '').includes(`已解析 · ${EXPECT.sheetSales.sheetCount} Sheets`),
      badgeText ?? '',
    );
    check(
      '上传后自动选中该文件（区块 02 标题呈现文件名）',
      ((await panelText(page, 'Sheet 结构与字段说明')) ?? '').length > 0,
    );

    // ---------------- C4 解析处理信息行 ----------------
    console.log('\n[C4] F5 解析处理信息行（表头/多级表头/合并单元格）');
    await waitForSheetPanel(page, EXPECT.sheetSales.name);
    const sheetPanel = (await panelText(page, 'Sheet 结构与字段说明')) ?? '';
    check(
      `副标题给出 Sheet 数与表头位置（含「已识别 ${EXPECT.sheetSales.sheetCount} 个 Sheet」）`,
      sheetPanel.includes(`已识别 ${EXPECT.sheetSales.sheetCount} 个 Sheet`) &&
        sheetPanel.includes(`表头位于第 ${EXPECT.sheetSales.headerRow} 行`),
      sheetPanel.slice(0, 200),
    );
    check(
      `副标题给出行列规模（共 ${EXPECT.sheetSales.rows} 行 × ${EXPECT.sheetSales.cols} 列）`,
      sheetPanel.includes(`共 ${EXPECT.sheetSales.rows} 行 × ${EXPECT.sheetSales.cols} 列`),
      sheetPanel.slice(0, 200),
    );
    const noteText = (await sheetPanelNote(page)) ?? '';
    check(
      '提示「表头识别在第 2 行，已跳过前 1 行说明性内容」',
      noteText.includes('表头识别在第 2 行，已跳过前 1 行说明性内容'),
      noteText,
    );
    check(
      '提示「检测到多级表头，已按「上级/下级」合并为单级列名」',
      noteText.includes('检测到多级表头，已按「上级/下级」合并为单级列名'),
      noteText,
    );
    check(
      `提示合并单元格已向下填充展开（共 ${EXPECT.fileLevel.mergedCells} 个区域）`,
      noteText.includes(`合并单元格已向下填充展开（共 ${EXPECT.fileLevel.mergedCells} 个区域）`),
      noteText,
    );

    // ---------------- C5 Sheet 切换 ----------------
    console.log('\n[C5] F5 Sheet 列表与切换（含空表跳过）');
    const tabs = await sheetTabs(page);
    check(
      `Sheet 分段控件列出 ${EXPECT.sheetSales.sheetCount} 个 Sheet`,
      tabs.length === EXPECT.sheetSales.sheetCount,
      JSON.stringify(tabs),
    );
    check('包含「销售明细」与「部门对照」', tabs.includes('销售明细') && tabs.includes('部门对照'), JSON.stringify(tabs));
    check('空表 Sheet 已被跳过', !tabs.includes('空表'), JSON.stringify(tabs));

    check('切换到「部门对照」', await clickSheetTab(page, EXPECT.sheetCity.name));
    await waitForSheetPanel(page, EXPECT.sheetCity.name);
    const cityPanel = (await panelText(page, 'Sheet 结构与字段说明')) ?? '';
    check(
      `第二 Sheet 规模为 ${EXPECT.sheetCity.rows} 行 × ${EXPECT.sheetCity.cols} 列`,
      cityPanel.includes(`共 ${EXPECT.sheetCity.rows} 行 × ${EXPECT.sheetCity.cols} 列`),
      cityPanel.slice(0, 200),
    );
    check('第二 Sheet 解析处理信息行为「表头位于第 1 行」', cityPanel.includes('表头位于第 1 行'));

    check('切回「销售明细」', await clickSheetTab(page, EXPECT.sheetSales.name));
    await waitForSheetPanel(page, EXPECT.sheetSales.name);

    // ---------------- C6 字段说明 ----------------
    console.log('\n[C6] F6 字段说明（类型 / 缺失率 / 敏感脱敏标记）');
    const fields = await fieldRows(page);
    check(
      `字段说明列出 ${EXPECT.sheetSales.cols} 个字段`,
      fields.length === EXPECT.sheetSales.cols,
      JSON.stringify(fields.map((f) => f[0])),
    );
    const fieldNameOf = (index) => fields[index]?.[0] ?? '';
    check('多级表头上级列名已拼接（销售维度/部门）', fieldNameOf(0).includes('销售维度/部门'), fieldNameOf(0));
    check('多级表头上级列名已拼接（指标/客户手机号）', fieldNameOf(4).includes('指标/客户手机号'), fieldNameOf(4));
    check('销售额推断为「小数」', fields[2]?.[1] === '小数', fields[2]?.[1] ?? '');
    check('订单数推断为「整数」', fields[3]?.[1] === '整数', fields[3]?.[1] ?? '');
    check(
      '敏感字段（客户手机号）标注「分析前脱敏」',
      fieldNameOf(4).includes('分析前脱敏'),
      fieldNameOf(4),
    );
    check(
      '敏感字段按文本处理（未被推断为整数）',
      fields[4]?.[1] === '字符串',
      fields[4]?.[1] ?? '',
    );
    check('敏感字段示例值已脱敏（形如 130****0000）', /1\d{2}\*{4}\d{4}/.test(fields[4]?.[2] ?? ''), fields[4]?.[2] ?? '');
    check(
      '高缺失字段（备注）缺失率为 45.0% 且被标红',
      (fields[5]?.[3] ?? '') === '45.0%',
      fields[5]?.[3] ?? '',
    );
    check('低缺失字段（客户手机号）缺失率为 11.7%', (fields[4]?.[3] ?? '') === '11.7%', fields[4]?.[3] ?? '');

    // ---------------- C7 数据质量小结 ----------------
    console.log('\n[C7] F3 数据质量小结（五项与样本预期一致）');
    const stats = await statStrip(page);
    check(`总行数 = ${EXPECT.fileLevel.rowTotal}`, stats['总行数'] === String(EXPECT.fileLevel.rowTotal), JSON.stringify(stats));
    check(`字段数 = ${EXPECT.fileLevel.colTotal}`, stats['字段数'] === String(EXPECT.fileLevel.colTotal), JSON.stringify(stats));
    check(
      `缺失单元格 = ${EXPECT.fileLevel.missingCells}`,
      stats['缺失单元格'] === String(EXPECT.fileLevel.missingCells),
      JSON.stringify(stats),
    );
    check(
      `高缺失字段 = ${EXPECT.fileLevel.highMissingFields}`,
      stats['高缺失字段'] === String(EXPECT.fileLevel.highMissingFields),
      JSON.stringify(stats),
    );
    check(
      `合并单元格 = ${EXPECT.fileLevel.mergedCells}`,
      stats['合并单元格'] === String(EXPECT.fileLevel.mergedCells),
      JSON.stringify(stats),
    );

    // ---------------- C8 数据预览与分页 ----------------
    console.log('\n[C8] F7 数据预览与分页');
    await waitForPreviewColumn(page, '销售维度/部门');
    const totalPages = Math.ceil(EXPECT.sheetSales.rows / 50);
    const pager1 = (await pagerText(page)) ?? '';
    check(
      `页码文案为「第 1 / ${totalPages} 页 · 每页 50 行 · 共 ${EXPECT.sheetSales.rows} 行」`,
      pager1.includes(`第 1 / ${totalPages} 页`) &&
        pager1.includes('每页 50 行') &&
        pager1.includes(`共 ${EXPECT.sheetSales.rows} 行`),
      pager1,
    );
    check('首页时「上一页」按钮禁用', (await pagerDisabled(page, '上一页')) === true);
    const shape = await previewShape(page);
    check(
      `预览表头为 行号 + ${EXPECT.sheetSales.cols} 列`,
      shape.cols === EXPECT.sheetSales.cols + 1,
      JSON.stringify(shape.headers),
    );
    check(
      `预览表给出 ${EXPECT.sheetSales.cols} 列名（多级拼接）`,
      shape.headers.includes('销售维度/部门') && shape.headers.includes('指标/备注'),
      JSON.stringify(shape.headers),
    );
    check('首页渲染 50 行', shape.rows === 50, `实际 ${shape.rows} 行`);
    check('行号自 1 起', (await previewCell(page, 0, 0)) === '1');
    check(
      '合并单元格向下填充生效（第 2 行部门同为「华东大区」）',
      (await previewCell(page, 1, 1)) === '华东大区',
      (await previewCell(page, 1, 1)) ?? 'null',
    );
    check(
      '缺失单元格渲染为「（缺失）」灰字',
      (await previewCell(page, 0, 6)) === '（缺失）',
      (await previewCell(page, 0, 6)) ?? 'null',
    );

    check('点击「下一页」', await clickPager(page, '下一页'));
    await page.waitForFunction(
      (p) => document.querySelector('.ws-pager')?.innerText.includes(`第 ${p} /`),
      { timeout: 15000 },
      totalPages,
    );
    const pager2 = (await pagerText(page)) ?? '';
    check(`翻至「第 ${totalPages} / ${totalPages} 页」`, pager2.includes(`第 ${totalPages} / ${totalPages} 页`), pager2);
    check('末页「下一页」按钮禁用', (await pagerDisabled(page, '下一页')) === true);
    check('末页行号从 51 起', (await previewCell(page, 0, 0)) === '51', (await previewCell(page, 0, 0)) ?? 'null');
    check('末页渲染 10 行', (await previewShape(page)).rows === EXPECT.sheetSales.rows - 50);

    // ---------------- C9 缺失值策略 ----------------
    console.log('\n[C9] F8 缺失值处理策略切换');
    check('默认策略为「提示」', (await activePolicy(page)) === '提示', (await activePolicy(page)) ?? 'null');
    let policyPanel = (await panelText(page, '缺失值处理策略')) ?? '';
    check('策略说明为「标注缺失率」口径', policyPanel.includes('在字段说明中标注缺失率'), policyPanel.slice(0, 160));
    check(
      `面板摘要给出缺失规模（${EXPECT.fileLevel.missingCells} 个缺失单元格）`,
      policyPanel.includes(`${EXPECT.fileLevel.missingCells} 个缺失单元格`),
      policyPanel.slice(0, 160),
    );

    check('切换为「剔除」', await clickPolicy(page, '剔除'));
    await page.waitForFunction(
      () =>
        document
          .querySelector('.ws-segmented[aria-label="缺失值处理策略"] button.is-active')
          ?.innerText.trim() === '剔除',
      { timeout: 15000 },
    );
    check('激活态切到「剔除」', (await activePolicy(page)) === '剔除');
    policyPanel = (await panelText(page, '缺失值处理策略')) ?? '';
    check('说明更新为「剔除含缺失值的整行」', policyPanel.includes('剔除含缺失值的整行'), policyPanel.slice(0, 200));

    check('切回「提示」', await clickPolicy(page, '提示'));
    await page.waitForFunction(
      () =>
        document
          .querySelector('.ws-segmented[aria-label="缺失值处理策略"] button.is-active')
          ?.innerText.trim() === '提示',
      { timeout: 15000 },
    );
    check('激活态回到「提示」（策略可反复切换）', (await activePolicy(page)) === '提示');

    // ---------------- C10 CSV 编码探测 ----------------
    console.log('\n[C10] CSV 编码探测（GBK → GB18030）');
    check('上传 GBK 编码 CSV', await uploadFile(page, path.join(FIXTURE_DIR, SAMPLE_CSV)));
    await waitForBadge(page, SAMPLE_CSV, '已解析');
    const csvRow = await fileRowText(page, SAMPLE_CSV);
    check('CSV 徽标为「已解析 · 1 Sheet」', (csvRow ?? '').includes('已解析 · 1 Sheet'), csvRow ?? '');

    check('选中 CSV 文件', await clickRowAction(page, SAMPLE_CSV, '查看'));
    await waitForSheetPanel(page, EXPECT.csv.name);
    await waitForPreviewColumn(page, '城市');
    const csvPanel = (await panelText(page, 'Sheet 结构与字段说明')) ?? '';
    check(
      `Sheet 面板标注编码 ${EXPECT.csv.encoding}`,
      csvPanel.includes(EXPECT.csv.encoding),
      csvPanel.slice(0, 200),
    );
    check(
      `CSV 规模为 ${EXPECT.csv.rows} 行 × ${EXPECT.csv.cols} 列`,
      csvPanel.includes(`共 ${EXPECT.csv.rows} 行 × ${EXPECT.csv.cols} 列`),
      csvPanel.slice(0, 200),
    );
    check(
      '解析处理信息行标注「编码自动识别为 GB18030」',
      csvPanel.includes('编码自动识别为 GB18030'),
      csvPanel.slice(0, 240),
    );
    const csvFields = await fieldRows(page);
    check(
      'CSV 中文列名未乱码',
      csvFields.length === EXPECT.csv.cols && csvFields[0]?.[0] === '城市',
      JSON.stringify(csvFields.map((f) => f[0])),
    );
    check(
      `CSV 的 Sheet 名取文件主干（${EXPECT.csv.name}）`,
      (await sheetTabs(page)).includes(EXPECT.csv.name),
      JSON.stringify(await sheetTabs(page)),
    );
    check(
      'CSV 数据未出现「（缺失）」误判',
      (await previewCell(page, 0, 1)) === '上海',
      (await previewCell(page, 0, 1)) ?? 'null',
    );

    // ---------------- C11 拒收非法格式 ----------------
    console.log('\n[C11] F1 拒收非法格式并给出具体原因');
    await uploadFile(page, path.join(FIXTURE_DIR, SAMPLE_TXT));
    await page.waitForFunction(
      (hint) => document.querySelector('.ws-notice--warn')?.innerText.includes(hint),
      { timeout: 15000 },
      '不支持的文件格式',
    );
    const notice = await page.$eval('.ws-notice--warn', (el) => el.innerText);
    check('红色警示条给出拒绝原因', notice.includes('不支持的文件格式，仅支持 xlsx、csv'), notice.replace(/\s+/g, ' '));
    check('被拒文件未进入文件列表', (await fileRowText(page, SAMPLE_TXT)) === null);

    // ---------------- C12 删除二次确认 ----------------
    console.log('\n[C12] F4 删除二次确认（AC-4.2-2）');
    check('点击 CSV 行的「删除」', await clickRowAction(page, SAMPLE_CSV, '删除'));
    await page.waitForSelector('.ws-modal', { timeout: 15000 });
    const modalText = await page.$eval('.ws-modal', (el) => el.innerText);
    check('弹出居中确认弹窗', modalText.includes('删除文件？'), modalText.replace(/\s+/g, ' ').slice(0, 120));
    check('弹窗写明影响范围（含文件名）', modalText.includes(SAMPLE_CSV), modalText.replace(/\s+/g, ' ').slice(0, 200));
    check('弹窗声明不可恢复', modalText.includes('该操作不可恢复'));

    check('点击「取消」不执行删除', await page.evaluate(() => {
      const btn = [...document.querySelectorAll('.ws-modal-actions button')].find(
        (b) => b.innerText.trim() === '取消',
      );
      if (!btn) return false;
      btn.click();
      return true;
    }));
    await page.waitForFunction(() => !document.querySelector('.ws-modal'), { timeout: 10000 });
    check('弹窗关闭且文件仍在列表中', (await fileRowText(page, SAMPLE_CSV)) !== null);

    check('再次点击「删除」', await clickRowAction(page, SAMPLE_CSV, '删除'));
    await page.waitForSelector('.ws-modal', { timeout: 15000 });
    check('点击「确认删除」', await page.evaluate(() => {
      const btn = document.querySelector('.ws-btn--danger');
      if (!btn) return false;
      btn.click();
      return true;
    }));
    await page.waitForFunction(
      (name) => !document.body.innerText.includes(name),
      { timeout: 20000 },
      SAMPLE_CSV,
    );
    check('文件已从列表移除', (await fileRowText(page, SAMPLE_CSV)) === null);
    check('弹窗已关闭', (await page.$('.ws-modal')) === null);
    check('xlsx 文件不受影响', (await fileRowText(page, SAMPLE_XLSX)) !== null);

    // 删除的是「当前选中」的 CSV，选中项应自动回落到仍在列表中的 xlsx，
    // 而不是让区块 02/03 停留在空态（这是交互连贯性的一部分）。
    await waitForSheetPanel(page, EXPECT.sheetSales.name);
    const afterDeletePanel = (await panelText(page, 'Sheet 结构与字段说明')) ?? '';
    check('选中项回落到 xlsx 的「销售明细」Sheet', afterDeletePanel.includes(EXPECT.sheetSales.name));
  } finally {
    await browser.close();
    // 清理样本目录（失败不致命：tmp/ 已被 .gitignore 忽略）
    try {
      fs.rmSync(FIXTURE_DIR, { recursive: true, force: true });
    } catch {
      /* 忽略：仅临时目录 */
    }
  }

  // ------------------------------------------------------------
  // 汇总
  // ------------------------------------------------------------
  console.log('\n' + '='.repeat(62));
  if (failures.length === 0) {
    console.log(`[PASS] M2 浏览器端到端验证全部通过（共 ${passed} 项断言）`);
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
