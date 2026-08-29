from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    source = root / "chess_trainer" / "resources" / "knight.svg"
    target = root / "chess_trainer" / "resources" / "knight.ico"
    QApplication.instance() or QApplication(sys.argv)
    renderer = QSvgRenderer(QByteArray(source.read_bytes()))
    image = QImage(256, 256, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 256, 256))
    painter.end()
    if not image.save(str(target), "ICO"):
        raise RuntimeError("Qt could not create the Windows icon")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

