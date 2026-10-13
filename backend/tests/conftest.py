# -*- coding: utf-8 -*-
"""
pytest 全局配置（conftest）。

功能说明
--------
1. 将 backend/ 目录加入模块搜索路径，使测试可直接 `import app.*`，
   无需在 CI 中额外设置 PYTHONPATH；
2. **测试库隔离**：在**任何 app 模块被导入之前**，把 `DATABASE_URL` 环境变量指向
   临时目录下的 SQLite 文件，避免测试产生的用户/会话数据写入开发库
   `backend/data/app.db`，也避免重复执行测试时因「账号已存在」而失败。

为什么必须在导入 app 之前设置
-----------------------------
`app/infra/db.py` 在**模块导入时**就创建了全局 Engine（模块级单例），
其连接串取自 `settings.database_url`。若先导入 app 再改环境变量，Engine 已经指向旧库，
隔离将完全失效。因此本文件把环境设置放在所有 `from app...` 之前（本文件先于测试模块被收集）。

副作用说明
----------
M0 的冒烟测试原先是直接读写开发库的，本文件生效后它们也会切换到临时库。
经核对，M0 用例只断言「表/索引数量、幂等性、响应体结构、健康检查」，与具体库文件无关，
因此该切换对 M0 用例是**安全且更规范**的（测试不应污染开发数据）。
"""

import os
import sys
import tempfile
from pathlib import Path

# backend/ 目录（tests/ 的上一级）
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ------------------------------------------------------------
# 测试库隔离（务必位于任何 app 导入之前）
# ------------------------------------------------------------
# 每次 pytest 会话创建一个独立的临时目录，进程退出后由操作系统回收。
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="eda_pytest_"))
_TEST_DB_PATH = _TEST_DB_DIR / "test.db"

# 直接赋值（而非 setdefault）：确保即使开发机导出了 DATABASE_URL，测试仍使用隔离库
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"

# 上传/导出/日志目录同样指向临时目录，避免测试往仓库内写文件
os.environ["UPLOAD_DIR"] = str(_TEST_DB_DIR / "uploads")
os.environ["EXPORT_DIR"] = str(_TEST_DB_DIR / "exports")
os.environ["LOG_DIR"] = str(_TEST_DB_DIR / "logs")

# 关闭调试模式，使日志级别与生产一致（减少测试输出噪声）
os.environ["DEBUG"] = "false"
