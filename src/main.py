# -*- coding: utf-8 -*-
"""流量看板入口。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.main_window import run_app  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_app())
