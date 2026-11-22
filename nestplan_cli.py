#!/usr/bin/env python3
"""隔离模式启动器：python3 -I nestplan_cli.py report。

脚本所在目录会进入 sys.path[0]，因此无需 PYTHONPATH，
也不会从当前工作目录或环境变量里加载任何第三方代码。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nestplan.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
