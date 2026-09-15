"""CANTool 启动入口。

用法：
    python main.py           # 正常启动
    python main.py --sim     # 启动后默认选中"仿真回环"，无硬件时验证界面
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.bus import BACKEND_SIM, BusConfig  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    simulate = "--sim" in sys.argv
    app = QApplication([arg for arg in sys.argv if arg != "--sim"])
    app.setApplicationName("CANTool")
    app.setOrganizationName("cartool")

    window = MainWindow()
    if simulate:
        window.device_panel.set_config(BusConfig(backend=BACKEND_SIM))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
