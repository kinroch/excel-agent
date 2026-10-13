# -*- coding: utf-8 -*-
"""
应用包入口（backend/app）。

分层约定（技术文档 3.1）：
    api      路由层：只做协议转换（参数校验 → 调服务 → 组装响应），不做业务判断
    services 服务层：业务编排与事务边界
    agents   领域层：AgentScope 多智能体链路，仅被 services / workers 调用
    infra    基础设施层：数据库、LLM 网关、沙箱、队列、文件存储，无业务语义
    core     公共内核：配置、错误码、统一响应体、安全、日志
    workers  异步任务消费者
"""

__version__ = "0.1.0"
