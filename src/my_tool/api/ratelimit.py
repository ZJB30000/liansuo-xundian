"""阶段5（提示词9）同用户并发限流：一个账号同时最多 2 个对话请求在处理。

背景：glm-4-flash 是免费额度，一个用户开多个页面/脚本并发刷 /api/chat，
等于烧光所有人的额度。限流器放在聊天入口：超限直接 429 拒绝（不排队——
SSE 是长连接，排队只会让请求越积越多）。

进程内存实现（dict + 锁）：单进程 uvicorn 部署规模下够用；多 worker 部署时
应换成 Redis 计数（接口不变，替换实现即可）。
"""

import threading
from collections import defaultdict


class PerUserRateLimiter:
    """按用户计数的并发闸门：acquire 拿额度（拿不到返回 False），release 归还。"""

    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent
        self._counts: dict = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, key) -> bool:
        """尝试占一个并发额度；已满返回 False（调用方据此回 429）。"""
        with self._lock:
            if self._counts[key] >= self.max_concurrent:
                return False
            self._counts[key] += 1
            return True

    def release(self, key) -> None:
        """归还额度；计数归零时清掉键，避免长期运行的字典无限膨胀。"""
        with self._lock:
            if self._counts[key] <= 1:
                self._counts.pop(key, None)
            else:
                self._counts[key] -= 1


# 默认闸门：每用户并发 2（模块级单例，测试可整体替换）
rate_limiter = PerUserRateLimiter(max_concurrent=2)
