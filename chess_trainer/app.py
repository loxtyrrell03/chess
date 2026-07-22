from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import AppConfig, app_data_dir
from .runtime import RuntimeController
from .ui import ControlCentre


def _resource_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "resources" / name


def _configure_logging() -> None:
    log_path = app_data_dir() / "chess-trainer.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> int:
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Chess Trainer")
    app.setOrganizationName("loxtyrrell03")
    app.setQuitOnLastWindowClosed(False)

    lock = QLockFile(str(app_data_dir() / "control-centre.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Chess Trainer", "Chess Trainer is already running in the taskbar tray.")
        return 0

    config = AppConfig.load()
    runtime = RuntimeController(config)
    icon = QIcon(str(_resource_path("knight.svg")))
    window = ControlCentre(config, runtime, icon)
    window.hide()
    try:
        runtime.start()
        window.tray.showMessage(
            "Chess Trainer is running",
            "The dashboard will appear inside the active Chess.com or Lichess tab.",
        )
    except RuntimeError as exc:
        QMessageBox.critical(window, "Startup failed", str(exc))
    result = app.exec()
    runtime.stop()
    lock.unlock()
    return result
