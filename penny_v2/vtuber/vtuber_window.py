# penny_v2/vtuber/vtuber_window.py
from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QPalette
from PIL import Image
from PIL.ImageQt import ImageQt
import os
import json
import random
import logging

from penny_v2.config import settings
from penny_v2.utils.helpers import get_asset_path

logger = logging.getLogger(__name__)

class QtVTuberWindow(QWidget):
    update_volume_signal = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Penny VTuber")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        green = QColor(0, 255, 0)
        self.current_rms_volume = 0.0
        self.current_mouth_shape_index = 0
        self.old_pos = None
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, green)
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self._load_assets()
        self._setup_ui()
        self._schedule_blink()
        self._update_mouth_animation()

        self.update_volume_signal.connect(self.update_volume_for_mouth)

    def _load_assets(self):
        try:
            scale = settings.VTUBER_IMAGE_SCALE_FACTOR

            def load_img(name, scale=1.0):
                pil_img = Image.open(get_asset_path(name)).convert("RGBA")
                w, h = int(pil_img.width * scale), int(pil_img.height * scale)
                pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
                return QPixmap.fromImage(ImageQt(pil_img))

            base_scale = settings.VTUBER_IMAGE_SCALE_FACTOR
            eye_scale = settings.VTUBER_EYE_SCALE_FACTOR
            mouth_scale = settings.VTUBER_MOUTH_SCALE_FACTOR

            self.base_image = load_img(settings.VTUBER_BASE_IMAGE, base_scale)
            self.eye_open_left = load_img(settings.VTUBER_EYE_OPEN_LEFT, eye_scale)
            self.eye_open_right = load_img(settings.VTUBER_EYE_OPEN_RIGHT, eye_scale)
            self.eye_closed_left = load_img(settings.VTUBER_EYE_CLOSED_LEFT, eye_scale)
            self.eye_closed_right = load_img(settings.VTUBER_EYE_CLOSED_RIGHT, eye_scale)
            self.mouth_shapes = [load_img(shape, mouth_scale) for shape in settings.VTUBER_MOUTH_SHAPES]

        except Exception as e:
            logger.error(f"Failed to load VTuber assets: {e}")
            self.close()

    def _setup_ui(self):
        eye_l = settings.VTUBER_LEFT_EYE_POS
        eye_r = settings.VTUBER_RIGHT_EYE_POS
        mouth = settings.VTUBER_MOUTH_POS

        logger.info("VTuber assets loaded.")

        self.canvas = QLabel(self)
        self.canvas.setPixmap(self.base_image)
        self.canvas.setGeometry(0, 0, self.base_image.width(), self.base_image.height())
        self.setFixedSize(self.base_image.size())

        self.left_eye = QLabel(self)
        self.left_eye.setPixmap(self.eye_open_left)
        self.left_eye.move(*eye_l)
        self.left_eye.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.right_eye = QLabel(self)
        self.right_eye.setPixmap(self.eye_open_right)
        self.right_eye.move(*eye_r)
        self.right_eye.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.mouth = QLabel(self)
        if self.mouth_shapes:
            self.mouth.setPixmap(self.mouth_shapes[0])
            mouth_pixmap = self.mouth_shapes[0]
            adjusted_x = mouth[0] - mouth_pixmap.width() // 2
            adjusted_y = mouth[1] - mouth_pixmap.height() // 2
            self.mouth.move(adjusted_x, adjusted_y)
            self.mouth.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
    def _schedule_blink(self):
        delay = random.randint(2000, 7000)
        QTimer.singleShot(delay, self._perform_blink)

    def _perform_blink(self):
        self.left_eye.setPixmap(self.eye_closed_left)
        self.right_eye.setPixmap(self.eye_closed_right)
        QTimer.singleShot(150, self._unblink)

    def _unblink(self):
        self.left_eye.setPixmap(self.eye_open_left)
        self.right_eye.setPixmap(self.eye_open_right)
        self._schedule_blink()

    def _update_mouth_animation(self):
        if not self.mouth_shapes:
            return

        num_shapes = len(self.mouth_shapes)
        volume_normalized = min(max(self.current_rms_volume / 70.0, 0.0), 1.0)
        index = int(volume_normalized * (num_shapes - 1))

        if index != self.current_mouth_shape_index:
            self.mouth.setPixmap(self.mouth_shapes[index])
            self.current_mouth_shape_index = index

        QTimer.singleShot(50, self._update_mouth_animation)

    def update_volume_for_mouth(self, rms_volume: float):
        self.current_rms_volume = rms_volume

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.old_pos is not None:
            new_pos = event.globalPosition().toPoint() - self.old_pos
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = None
            event.accept()
