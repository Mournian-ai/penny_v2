from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QSlider, QTextEdit, QCheckBox, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QKeyEvent
import asyncio
import logging

from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import UILogEvent, TTSSpeakingStateEvent, PTTRecordingStateEvent, SpeakRequestEvent
from penny_v2.services.tts_service import TTSService
from penny_v2.services.audio_service import AudioService
from penny_v2.vtuber.vtuber_manager import VTuberManagerService
from penny_v2.services.ptt_controller import PTTController

logger = logging.getLogger(__name__)

class QtDashboard(QMainWindow):
    log_received = pyqtSignal(str, str)  # level, message
    tts_state_changed = pyqtSignal(bool)
    ptt_state_changed = pyqtSignal(bool)

    def __init__(self, event_bus: EventBus, tts_service: TTSService,
                 audio_service: AudioService, vtuber_manager: VTuberManagerService, settings):
        super().__init__()
        self.setWindowTitle("Penny V2 Dashboard (Qt)")
        self.setGeometry(100, 100, 900, 600)

        self.event_bus = event_bus
        self.tts_service = tts_service
        self.audio_service = audio_service
        self.vtuber_manager = vtuber_manager
        self.settings = settings

        self.ptt_enabled = False
        self.ptt_active = False
        self.is_muted = False
        self.ptt_controller = PTTController(event_bus, audio_service, settings)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self._setup_ui()
        self._connect_custom_signals()
        self._register_event_subscriptions()
        self.installEventFilter(self)

    def _setup_ui(self):
        main_layout = QVBoxLayout()
        controls_layout = QHBoxLayout()
        bottom_layout = QHBoxLayout()

        self.talk_light = QLabel("●")
        self.talk_light.setStyleSheet("color: gray; font-size: 24px;")
        self.status_label = QLabel("Status: Idle")

        controls_layout.addWidget(self.talk_light)
        controls_layout.addWidget(self.status_label)

        self.ptt_button = QPushButton("Start PTT")
        self.ptt_button.clicked.connect(self.on_ptt_button_clicked)
        controls_layout.addWidget(self.ptt_button)

        self.mute_button = QPushButton("Mute Penny")
        self.mute_button.clicked.connect(self.on_mute_button_clicked)
        controls_layout.addWidget(self.mute_button)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(20)
        self.volume_slider.setValue(int(self.tts_service.volume_db_reduction))
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        bottom_layout.addWidget(QLabel("Volume (dB Reduction):"))
        bottom_layout.addWidget(self.volume_slider)

        self.collab_checkbox = QCheckBox("Collab Mode")
        self.collab_checkbox.setChecked(self.tts_service.collab_mode)
        self.collab_checkbox.stateChanged.connect(self.on_collab_checkbox_toggled)
        bottom_layout.addWidget(self.collab_checkbox)

        self.vtuber_button = QPushButton("Toggle VTuber")
        self.vtuber_button.clicked.connect(self.on_vtuber_toggle_clicked)
        bottom_layout.addWidget(self.vtuber_button)

        self.test_button = QPushButton("Test Speak")
        self.test_button.clicked.connect(self.on_test_button_clicked)
        bottom_layout.addWidget(self.test_button)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: #1e1e1e; color: lightgray; font-family: Consolas;")

        main_layout.addLayout(controls_layout)
        main_layout.addWidget(self.log_output)
        main_layout.addLayout(bottom_layout)

        self.central_widget.setLayout(main_layout)

    def _connect_custom_signals(self):
        self.log_received.connect(self._update_log_output)
        self.tts_state_changed.connect(self._update_tts_status_display)
        self.ptt_state_changed.connect(self._update_ptt_button_display)

    def _register_event_subscriptions(self):
        self.event_bus.subscribe(UILogEvent, self._handle_ui_log_event)
        self.event_bus.subscribe(TTSSpeakingStateEvent, self._handle_tts_state_event)
        self.event_bus.subscribe(PTTRecordingStateEvent, self._handle_ptt_state_event)

    def _handle_ui_log_event(self, event: UILogEvent):
        self.log_received.emit(event.level, event.message)

    def _handle_tts_state_event(self, event: TTSSpeakingStateEvent):
        self.tts_state_changed.emit(event.is_speaking)

    def _handle_ptt_state_event(self, event: PTTRecordingStateEvent):
        self.ptt_state_changed.emit(event.is_recording)

    def _update_log_output(self, level: str, message: str):
        self.log_output.append(f"[{level}] {message}")

    def _update_tts_status_display(self, is_speaking: bool):
        if is_speaking:
            self.status_label.setText("Status: Speaking...")
            self.talk_light.setStyleSheet("color: red; font-size: 24px;")
        else:
            self.status_label.setText("Status: Idle")
            self.talk_light.setStyleSheet("color: gray; font-size: 24px;")

    def _update_ptt_button_display(self, is_recording: bool):
        self.ptt_active = is_recording
        self.ptt_button.setText("Stop PTT" if is_recording else "Start PTT")
        self.ptt_button.setStyleSheet("background-color: red;" if is_recording else "")

    def on_ptt_button_clicked(self):
        self.ptt_enabled = not self.ptt_enabled
        self.ptt_button.setText("Stop PTT" if self.ptt_enabled else "Start PTT")
        self.ptt_controller.set_enabled(self.ptt_enabled)
        
    def on_mute_button_clicked(self):
        try:
            self.is_muted = not self.is_muted
            self.tts_service.set_is_muted(self.is_muted)
            self.mute_button.setText("Unmute Penny" if self.is_muted else "Mute Penny")
        except Exception as e:
            logger.error("Error toggling mute: %s", e, exc_info=True)

    def on_volume_changed(self, value: int):
        try:
            self.tts_service.set_volume_reduction(float(value))
        except Exception as e:
            logger.warning("Volume adjustment failed: %s", e, exc_info=True)

    def on_collab_checkbox_toggled(self):
        try:
            enabled = self.collab_checkbox.isChecked()
            self.tts_service.toggle_collab_mode(enabled)
        except Exception as e:
            logger.warning("Collab mode toggle failed: %s", e, exc_info=True)

    def on_vtuber_toggle_clicked(self):
        try:
            if self.vtuber_manager.is_active():
                self.vtuber_manager.stop()
                self.vtuber_button.setText("Launch VTuber")
            else:
                asyncio.create_task(self.vtuber_manager.start())
                self.vtuber_button.setText("Hide VTuber")
        except Exception as e:
            logger.error("VTuber toggle failed: %s", e, exc_info=True)

    def on_test_button_clicked(self):
        try:
            asyncio.create_task(self.event_bus.publish(SpeakRequestEvent(text="Hello, this is a test of the text to speech system.")))
        except Exception as e:
            logger.warning("Test speak failed: %s", e, exc_info=True)

    def eventFilter(self, obj, event):
        if not self.ptt_enabled:
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            asyncio.create_task(self.ptt_controller.handle_key_press(event.key()))
            logger.info(f"[EventFilter] Key event: {event.key()}")
            return True

        elif event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            asyncio.create_task(self.ptt_controller.handle_key_release(event.key()))
            return True

        return super().eventFilter(obj, event)
