# -*- coding: utf-8 -*-
"""
异步任务消费者包（技术文档 3.6 / ADR-002）。

analysis_worker.py 将作为队列消费者，从 infra/taskqueue 取出分析任务并驱动 agents/orchestrator，
在 M3 里程碑落地。M0 阶段仅建立包结构。
"""
