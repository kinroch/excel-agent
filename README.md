# Excel 对话式数据分析智能体

通过自然语言对话，完成 Excel 数据的读取、分析、统计与可视化。

## 项目定位

前后端分离的**双端 Web 应用**：

- **用户端**：数据工作台 → 对话式分析 → 分析报告与导出 → 会话管理 → 个人统计
- **管理后台**：全局看板 → 用户管理 → 会话记录（含审计日志）

核心链路为「四智能体协作」：意图解析 → 数据分析（沙箱执行）→ 可视化 → 报告生成。

## 技术栈（锁定，禁止擅自变更）

| 模块 | 选型 |
| --- | --- |
| 后端 | Python 3.12+ / FastAPI / SQLAlchemy 2.0 |
| 数据库 | SQLite 3（WAL 模式，`data/app.db`） |
| Agent 框架 | AgentScope（四角色编排，M3 引入） |
| 数据处理 | pandas / NumPy / openpyxl |
| 前端 | React 18 + TypeScript + Vite 5（五入口多页 SPA） |
| UI | 设计令牌自研组件（AntD 仅补充 Modal / DatePicker / Pagination） |
| 状态管理 | zustand + React Query |
| 图表 | ECharts 5.5.1 |
| 异步任务 | asyncio + 进程内内存队列 |

技术选型依据《开发技术文档 V1.0.1》1.3，如需替换须先修订 PRD 7.4 并经需求方确认。

## 目录结构

```
excel-agent/
├── backend/                      # 服务端（四层：api → services → agents/infra）
│   ├── app/
│   │   ├── main.py               # FastAPI 实例、中间件、异常处理器、生命周期
│   │   ├── core/                 # 配置 / 错误码全表 / 统一响应体 / 安全 / 日志
│   │   ├── api/                  # 路由层（M0 仅 health；后续按模块追加）
│   │   ├── services/             # 服务层（事务边界）
│   │   ├── agents/               # 领域层（AgentScope 多智能体 + prompts/）
│   │   ├── infra/                # 基础设施（db / storage；后续 llm_client / sandbox / taskqueue）
│   │   └── workers/              # 异步任务消费者
│   ├── db/schema.sql             # 建表 DDL（13 张表，幂等）
│   ├── scripts/init_db.py        # 建库与校验脚本
│   └── tests/                    # pytest
├── frontend/                     # 前端（五入口）
│   ├── index.html login.html user.html admin-login.html admin.html
│   ├── src/
│   │   ├── styles/               # tokens.css（唯一样式来源）+ base.css
│   │   ├── components/           # 自研组件库
│   │   ├── icons/Icon.tsx        # Lucide 风格 SVG 精灵（禁 emoji）
│   │   ├── pages/user|admin/     # 双端视图
│   │   ├── api/ stores/ router/ utils/
│   │   └── *.tsx                 # 五个入口脚本
│   └── antd-theme.ts             # AntD 令牌映射
├── deploy/                       # nginx.conf.example / eda-backend.service
├── scripts/                      # dev-backend.bat / dev-frontend.bat / init-db.bat
├── tools/check_tokens.py         # 设计令牌一致性校验（M0 AC 判据）
└── .github/workflows/ci.yml      # CI（ruff + pytest / tsc + eslint + build）
```

## 快速开始（开发环境）

```bash
# 1) 建库（幂等，可重复执行；加 --verify 与基线对账）
python backend/scripts/init_db.py --verify

# 2) 启动后端（默认 http://127.0.0.1:8000，接口文档 /api/v1/docs）
scripts/dev-backend.bat

# 3) 安装前端依赖并启动（默认 http://127.0.0.1:5173）
cd frontend && npm install
scripts/dev-frontend.bat
```

Windows 用户可直接双击 `scripts/` 下的批处理文件。

## 质量门禁

| 门禁 | 命令 | 标准 |
| --- | --- | --- |
| 后端静态检查 | `cd backend && ruff check .` | 零 error |
| 后端单元测试 | `cd backend && pytest -q` | 全部通过 |
| 建库幂等校验 | `python backend/scripts/init_db.py --verify` | 表与索引与基线一致 |
| 令牌一致性 | `python tools/check_tokens.py` | 基线令牌全一致 |
| 前端类型检查 | `cd frontend && npm run typecheck` | 零 error |
| 前端静态检查 | `cd frontend && npm run lint` | 零 error |
| 前端构建 | `cd frontend && npm run build` | 五入口构建成功 |

## 开发阶段状态

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 环境与骨架（仓库/CI、双端骨架、建库、令牌、部署脚本） | 已完成 |
| M1 | 认证与权限（A1~A4、JWT、双端守卫） | 待启动 |
| M2 | 文件接入与解析（F1~F8、工作台） | 待启动 |
| M3 | 多智能体分析链路（AgentScope、沙箱、SSE） | 待启动 |
| M4 | 可视化与报告（ChartAgent / ReportAgent / R1~R5） | 待启动 |
| M5 | 会话与反馈（S1~S5、敏感词、多轮修正） | 待启动 |
| M6 | 统计与管理后台（ST1/ST2、AD1~AD5、审计） | 待启动 |
| M7 | 合规加固与联调（脱敏、AI 标识、HTTPS、安全回归） | 待启动 |
| M8 | 评测与性能达标（8:1:1 评测、压测） | 待启动 |

开发遵循**阶段确认制**：每个里程碑完成并经验收后方可进入下一里程碑。

## 代码交付规范

- 全部代码**中文注释**，每个模块 / 函数头部注明功能、参数与异常；
- 分层规约：`api` 只做协议转换、`services` 持事务边界、`agents` 仅被 services/workers 调用、`infra` 无业务语义；
- **禁止硬编码色值与密钥**：样式一律引用 `tokens.css` 令牌，密钥走 `.env`；
- 提交信息格式：`M3: 沙箱超时杀灭与重试计数（F-4.3-9）`。
