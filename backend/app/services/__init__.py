# -*- coding: utf-8 -*-
"""
服务层包：业务编排与事务边界。

分层规约（技术文档 3.1）：
    - 本层持有**事务边界**：所有写操作在服务层显式开事务并提交；
    - api 层不得直接操作数据库，必须经由服务层；
    - agents 层仅被本层与 workers 层调用。

M0 阶段仅建立包结构。各服务模块将随里程碑落地：
    M1 auth_service      M2 file_service / parse_service
    M3 task_service      M4 report_service
    M5 session_service   M6 stats_service / admin_service / audit_service
"""
