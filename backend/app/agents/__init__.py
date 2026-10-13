# -*- coding: utf-8 -*-
"""
领域层包：AgentScope 多智能体分析链路（技术文档 3.2）。

四类智能体角色与职责：
    IntentAgent   意图解析 → intent_json（分析类型 / 度量 / 维度 / 过滤 / 是否需澄清）
    AnalysisAgent 数据分析 → plan_json + pandas 代码 + result_df（代码送沙箱执行）
    ChartAgent    可视化   → chart_type + ECharts option（含 match_reason 匹配依据）
    ReportAgent   报告生成 → 结构化报告（结论/关键数字/图表/来源/时间/口径/AI 标识）

orchestrator 负责串联为带重试环的流水线，仅被 services/task_service 与 workers 调用。

M0 阶段仅建立包结构与 prompts 目录，实际实现在 M3 里程碑。
"""
