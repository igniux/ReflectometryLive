"""Render the project's original R/layer SVG as PNG and Windows ICO assets."""
import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QImage, QPainter, QColor, QBrush, QPen
from PySide6.QtCore import QRectF, Qt
from PIL import Image

root = Path(__file__).resolve().parent
app = QApplication.instance() or QApplication([])
renderer = QSvgRenderer(str(root/"reflectometry.svg"))
if not renderer.isValid():
    raise RuntimeError("Invalid icon SVG")
canvas = QImage(512, 512, QImage.Format.Format_ARGB32)
canvas.fill(Qt.GlobalColor.transparent)
painter = QPainter(canvas)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
# A white backing keeps the mark's outline clear on either taskbar theme.
painter.setPen(QPen(Qt.PenStyle.NoPen))
painter.setBrush(QBrush(QColor("white")))
painter.drawRoundedRect(QRectF(1, 1, 510, 510), 80, 80)
renderer.render(painter, QRectF(0, 0, 512, 512))
painter.end()
canvas.save(str(root/"reflectometry.png"))
with Image.open(root/"reflectometry.png") as rendered:
    rendered.save(root/"reflectometry.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
print("Created reflectometry.png and reflectometry.ico")
