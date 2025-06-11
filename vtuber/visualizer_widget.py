
# penny_v2/vtuber/visualizer_widget.py
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QBrush
from PyQt6.QtCore import Qt, QTimer
import random

class VisualizerWidget(QWidget):
    def __init__(self, bar_count=20, parent=None):
        super().__init__(parent)
        self.bar_count = bar_count
        self.bar_values = [0.0] * bar_count
        self.setMinimumHeight(100)
        self.setMinimumWidth(400)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(33)  # ~30 FPS

    def update_bars(self, new_values):
        self.bar_values = new_values[:self.bar_count]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        center_x = width // 2
        bar_width = 10
        gap = 4

        max_bar_height = height * 0.8

        for i, value in enumerate(self.bar_values):
            # Clamp and scale height
            bar_height = min(max_bar_height, value * max_bar_height)
            x_offset = i * (bar_width + gap)
            y = (height - bar_height) / 2

            # Choose color based on value
            if value < 0.33:
                color = QColor(0, 255, 0)  # Green
            elif value < 0.66:
                color = QColor(255, 255, 0)  # Yellow
            else:
                color = QColor(255, 0, 0)  # Red

            painter.setBrush(QBrush(color, Qt.BrushStyle.SolidPattern))
            painter.setPen(Qt.PenStyle.NoPen)

            # Draw left bar
            painter.drawRect(center_x - x_offset - bar_width, int(y), bar_width, int(bar_height))
            # Draw right bar
            painter.drawRect(center_x + x_offset, int(y), bar_width, int(bar_height))
