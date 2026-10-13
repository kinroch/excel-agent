# -*- coding: utf-8 -*-
"""
pytest 全局配置（conftest）。

功能说明
--------
将 backend/ 目录加入模块搜索路径，使测试可直接 `import app.*`，
无需在 CI 中额外设置 PYTHONPATH。
"""

import sys
from pathlib import Path

# backend/ 目录（tests/ 的上一级）
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
