# -*- coding: utf-8 -*-
"""
登录限流基础设施（内存级滑窗计数器）

依据文档
--------
《开发技术文档 V1.0.1》5.1「接口规约」：登录接口按 IP + 账号 5 次/分钟（防爆破）。

功能说明
--------
提供一个线程安全的**失败计数滑窗**，供 api 层在登录接口上做防爆破拦截：

    1. `is_blocked(key)`  —— 进入登录逻辑前的**预检查**：窗口内失败次数是否已达上限；
    2. `record_failure(key)` —— 登录失败后**登记**一次失败；
    3. `reset(key)`       —— 登录成功后**清零**，避免合法用户被自身历史失败拖累。

关键设计（三条，评审重点）
--------------------------
① **为什么只统计「失败」而不是「全部请求」**
   限流目的是防爆破（猜密码），而非限制正常登录。若把成功请求也计入，
   用户在「登录 → 登出 → 再登录」的连续操作中会被莫名拦截，属误伤。
   因此采用「失败计数 + 成功清零」口径：攻击者猜错会被累积拦截，
   合法用户一旦登对立即解锁。

② **为什么用滑窗（时间戳队列）而不是固定窗口计数**
   固定窗口在边界处存在「双倍突发」漏洞（59 秒打满 5 次，下一秒又可打 5 次）。
   滑窗记录每次失败的精确时间戳，判定时只保留最近 window 秒内的记录，
   拦截更准确；代价是每个键存一个短列表，量级极小（上限即限流阈值）。

③ **为什么仍是内存级（与 security.TokenBlacklist 同口径）**
   本期单进程部署，内存即可满足。已明确记录的前提：**多进程/多实例部署时本限流不共享**，
   届时替换为 Redis 的 ZSET 滑窗或 Nginx limit_req（已登记为待确认项）。

线程安全
--------
uvicorn 的同步依赖在线程池中执行，故读写均以 threading.Lock 保护。

异常说明
--------
本模块不抛业务异常：`is_blocked()` 只返回布尔与建议等待秒数，
由调用方（api 层）决定抛出何种业务错误（1006 登录尝试过于频繁）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class LimitDecision:
    """限流判定结果。

    属性：
        blocked (bool): 是否已被拦截
        retry_after (int): 建议等待秒数（向上取整）；未拦截时为 0
        failure_count (int): 当前窗口内已记录的失败次数（便于日志与排障）
    """

    blocked: bool
    retry_after: int
    failure_count: int


class FailureRateLimiter:
    """基于「失败次数滑窗」的限流器。

    参数：
        limit (int): 窗口内允许的最大失败次数
        window_seconds (int): 滑动窗口长度（秒）

    内部结构：
        dict[str, deque[float]] —— 键为限流维度（如 "ip|username"），值为失败时间戳队列。
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        """初始化限流器。

        参数：
            limit (int): 窗口内允许的最大失败次数（达到该值即拦截）
            window_seconds (int): 窗口长度（秒）
        异常：
            ValueError: 参数非正数时抛出，避免配置错误导致限流失效
        """
        if limit <= 0 or window_seconds <= 0:
            raise ValueError(f"限流参数必须为正数：limit={limit} window={window_seconds}")
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _prune_locked(self, key: str, now: float) -> deque[float]:
        """清理该键下超出窗口的旧时间戳（调用前须持有锁）。

        参数：
            key (str): 限流维度键
            now (float): 当前时间戳
        返回：
            deque[float]: 清理后的时间戳队列（保证其中均为窗口内记录）
        """
        bucket = self._hits.get(key)
        if bucket is None:
            bucket = deque()
            self._hits[key] = bucket
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    # --------------------------------------------------------
    # 对外接口
    # --------------------------------------------------------
    def is_blocked(self, key: str) -> LimitDecision:
        """预检查：该维度在当前窗口内是否已达失败上限。

        参数：
            key (str): 限流维度键，约定由调用方拼装为 "ip|username"
        返回：
            LimitDecision: 含是否拦截、建议等待秒数、当前失败次数
        说明：
            本方法**不修改**任何状态（纯读），因此可安全地在登录业务逻辑之前调用。
        """
        now = time.time()
        with self._lock:
            bucket = self._prune_locked(key, now)
            count = len(bucket)
            if count < self._limit:
                return LimitDecision(blocked=False, retry_after=0, failure_count=count)
            # 最早一次失败 + 窗口 = 该次失败失效的时刻
            earliest = bucket[0]
            retry_after = max(1, int(self._window - (now - earliest) + 0.999))
            return LimitDecision(blocked=True, retry_after=retry_after, failure_count=count)

    def record_failure(self, key: str) -> None:
        """登记一次失败（登录校验未通过时调用）。

        参数：
            key (str): 限流维度键
        返回：
            无
        """
        now = time.time()
        with self._lock:
            bucket = self._prune_locked(key, now)
            bucket.append(now)

    def reset(self, key: str) -> None:
        """清零该维度的失败记录（登录成功时调用）。

        参数：
            key (str): 限流维度键
        返回：
            无
        说明：
            登录成功后立即清零，保证合法用户不会被历史失败次数误伤。
        """
        with self._lock:
            self._hits.pop(key, None)

    def size(self) -> int:
        """返回当前被跟踪的维度数量（测试与监控用）。

        返回：
            int: 维度键数量
        """
        with self._lock:
            return len(self._hits)


def build_key(ip: str, username: str) -> str:
    """拼装限流维度键。

    参数：
        ip (str): 客户端 IP（可能含未知标记）
        username (str): 登录账号（统一小写，避免大小写绕过）
    返回：
        str: 形如 "127.0.0.1|user@example.com"
    说明：
        技术文档 5.1 要求「按 IP + 账号」，故二者共同构成维度：
        同一 IP 猜不同账号、或同一账号被不同 IP 猜，都会分别累积计数。
    """
    return f"{(ip or 'unknown').strip()}|{(username or '').strip().lower()}"


# ------------------------------------------------------------
# 全局单例：登录限流器（5 次 / 60 秒，取自技术文档 5.1）
# ------------------------------------------------------------
LOGIN_LIMIT: int = 5
LOGIN_WINDOW_SECONDS: int = 60

login_limiter = FailureRateLimiter(limit=LOGIN_LIMIT, window_seconds=LOGIN_WINDOW_SECONDS)
