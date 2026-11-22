"""测试引导：unittest discover 会把本目录放进 sys.path[0]，
因此测试模块用普通 ``import _bootstrap`` 即可拿到仓库根，
不依赖 tests 是否是包，也兼容 ``python3 -I`` 隔离模式。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
