# 参考资料 · claude-data-analysis

| 项目 | 内容 |
| --- | --- |
| 仓库 | https://github.com/liangdabiao/claude-data-analysis |
| 作者 | liangdabiao |
| 许可证 | MIT（可借鉴思路与表述，直接复用代码需保留版权声明） |
| 归档日期 | 2026-10-13 |
| 归档原因 | 需求方指定的重要参考代码（M1 阶段补充提供） |

---

## 1. 该仓库是什么

**基于 Claude Code 的斜杠命令 / 子智能体 / hooks 套件**，用于让 Claude Code 充当数据分析助手。它**不是 Web 应用**：

- ❌ 无后端服务、无 REST 接口、无数据库、无用户体系与认证
- ✅ 全部能力由 `.claude/agents/`（子智能体定义）、`.claude/commands/`（斜杠命令）、`.claude/settings.json`（hooks）构成
- Agent 框架是 **Claude Code 自身**，非 AgentScope
- 运行形态：在终端里输入 `/analyze data.csv exploratory`

目录结构：

```
claude-data-analysis/
├── .claude/
│   ├── agents/        # 6 个子智能体定义
│   ├── commands/      # 斜杠命令定义
│   ├── hooks/         # 自动化脚本
│   └── settings.json
├── data_storage/      # 数据文件
├── visualizations/    # 图表产物
├── generated_code/    # 生成的分析代码
├── analysis_reports/  # 分析报告
└── docs/
```

### 子智能体清单（6 个）

| 子智能体 | 职责 |
| --- | --- |
| `data-explorer` | 数据探索与统计分析 |
| `visualization-specialist` | 数据可视化生成 |
| `code-generator` | 分析代码生成 |
| `report-writer` | 综合分析报告撰写 |
| `quality-assurance` | 数据质量校验 |
| `hypothesis-generator` | 研究假设与洞察生成 |

---

## 2. 与本项目的映射关系

### 2.1 结论：对 M1（认证与权限）**无参考价值**

该仓库完全不存在用户、角色、令牌、权限等概念，M1 的实现依据仍为《开发技术文档 V1.0.1》5.4 / 8.1 与 PRD AC-4.1-1~5。

### 2.2 对后续里程碑的参考点

| 参考仓库模块 | 本项目对应 | 可借鉴内容 | 生效里程碑 |
| --- | --- | --- | --- |
| `data-explorer` | IntentAgent + AnalysisAgent | 探索性分析的提示词组织方式；分析类型枚举（exploratory / statistical / predictive / complete）可参考意图分类的取值设计 | M3 |
| `visualization-specialist` | ChartAgent | 图表类型选择与「按数据特征选图」的表述；**中文图表字体处理经验**（README 与 CLAUDE.md 均特别强调「画图注意中文字体问题」） | M3 / M4 |
| `report-writer` | ReportAgent | 报告结构组织（结论 → 关键数字 → 图表 → 明细） | M4 |
| `quality-assurance` | 结果校验器（沙箱产出校验） | 数据质量检查项的组织方式 | M3 |
| `code-generator` | AnalysisAgent 的 pandas 代码生成 | 代码生成与「可复现」要求（保存分析代码与文档） | M3 |
| `hypothesis-generator` | 无对应（P2 演进项） | 本期不实现 | — |
| `.claude/commands/*` | 前端对话入口 | 意图到命令的映射思路（本项目由 IntentAgent 承担） | M3 |

### 2.3 使用纪律

1. 本项目技术栈**已锁定**（FastAPI + SQLite + AgentScope，技术文档 1.3「选型不变更原则」），参考仓库的做法**不得替换任何选型**；
2. 借鉴范围限于**提示词设计思路、分析类型枚举、报告要素组织、图表中文适配经验**；
3. 直接复用其代码片段时须遵守 MIT 许可（保留版权声明），并改写为符合本项目分层规约与中文注释规范的形式。

---

## 3. 待抓取的落地材料（M3 前完成）

| 材料 | 用途 | 计划 |
| --- | --- | --- |
| `.claude/agents/data-explorer.md` | IntentAgent 提示词参考 | M3 启动前抓取归档 |
| `.claude/agents/visualization-specialist.md` | ChartAgent 匹配规则与中文图表经验 | M3 启动前抓取归档 |
| `.claude/agents/report-writer.md` | ReportAgent 报告结构参考 | M4 启动前抓取归档 |
| `.claude/CLAUDE.md` | 已抓取（要点见下） | ✅ 已完成 |

### 已抓取要点（CLAUDE.md）

- 「详细的分析代码和相关文档需要保存在项目中，便于查看分析过程与数据验证结果」→ 对应本项目 F-4.3-8「取数过程对照」强制呈现；
- 「报告和文档请尽量用中文」→ 与本项目全中文交付一致；
- 「visualization 画图注意中文字体问题」→ **M4 报告静态图元必须处理中文字体**（Matplotlib 默认字体不含中文，需显式配置），此点已记入 M4 待办。
