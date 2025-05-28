# Updated qt_ui_service.py with Memory Viewer tab functionality
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QSlider, QTextEdit, QCheckBox, QApplication, QPlainTextEdit, QTabWidget,
    QListWidget, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QKeyEvent
import asyncio
import logging
import json
import os
import aiohttp

from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    UILogEvent, TTSSpeakingStateEvent, PTTRecordingStateEvent,
    SpeakRequestEvent, TwitchMessageEvent, TwitchUserEvent
)
from penny_v2.services.tts_service import TTSService
from penny_v2.services.audio_service import AudioService
from penny_v2.vtuber.vtuber_manager import VTuberManagerService
from penny_v2.services.ptt_controller import PTTController
from penny_v2.services.listening_service import ListeningService
from penny_v2.vision.window_manager import WindowManager
from penny_v2.vision.vision_service import VisionService
from penny_v2.config import AppConfig

logger = logging.getLogger(__name__)
SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {"window": {"x": 100, "y": 100, "width": 1280, "height": 720}}

class QtDashboard(QMainWindow):
    log_received = pyqtSignal(str, str)
    tts_state_changed = pyqtSignal(bool)
    ptt_state_changed = pyqtSignal(bool)

    def __init__(self, event_bus: EventBus, tts_service: TTSService,
                 audio_service: AudioService, listening_service: ListeningService, vtuber_manager: VTuberManagerService, vision_service: VisionService ,settings: AppConfig):
        super().__init__()
        self.setWindowTitle("Penny Dashboard")
        self.resize(1280, 720)

        self.event_bus = event_bus
        self.tts_service = tts_service
        self.audio_service = audio_service
        self.vtuber_manager = vtuber_manager
        self.settings = settings
        self.listening_service = listening_service
        self.ptt_controller = PTTController(event_bus, audio_service, settings)
        self.fastapi_url_main = self.settings.FASTAPI_URL_MAIN.rstrip("/")
        self.window_manager = WindowManager()
        self._setup_ui()
        self.init_vision_tab()
        self._connect_signals()
        self._register_events()

        self.installEventFilter(self)
        self.settings = self._load_settings()
        self.move(self.settings["window"]["x"], self.settings["window"]["y"])
        self.resize(self.settings["window"]["width"], self.settings["window"]["height"])
        self.vision_service = vision_service
    def _setup_ui(self):
        self.tab_widget = QTabWidget()

        # System Log tab
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        log_layout = QVBoxLayout()
        log_layout.addWidget(QLabel("System Log"))
        log_layout.addWidget(self.log_output)
        log_tab = QWidget()
        log_tab.setLayout(log_layout)
        self.tab_widget.addTab(log_tab, "System Log")

        # Twitch Events tab
        self.chat_box = QPlainTextEdit("Twitch chat will appear here...")
        self.chat_box.setReadOnly(True)
        self.events_box = QTextEdit()
        self.events_box.setReadOnly(True)
        self.events_box.setFixedHeight(200)

        twitch_layout = QVBoxLayout()
        twitch_layout.addWidget(QLabel("Twitch Chat"))
        twitch_layout.addWidget(self.chat_box)
        twitch_layout.addWidget(QLabel("Recent Events"))
        twitch_layout.addWidget(self.events_box)
        twitch_tab = QWidget()
        twitch_tab.setLayout(twitch_layout)
        self.tab_widget.addTab(twitch_tab, "Twitch + Events")

        # Memory Viewer tab
        self.memory_list = QListWidget()
        self.memory_query = QLineEdit()
        self.memory_input = QTextEdit()
        self.memory_add_button = QPushButton("Store Memory")
        self.memory_refresh_button = QPushButton("Query Memories")
        self.memory_status = QLabel()

        self.memory_add_button.clicked.connect(self._store_memory)
        self.memory_refresh_button.clicked.connect(self._query_memory)

        memory_layout = QVBoxLayout()
        memory_layout.addWidget(QLabel("Query"))
        memory_layout.addWidget(self.memory_query)
        memory_layout.addWidget(self.memory_refresh_button)
        memory_layout.addWidget(QLabel("Memory Matches"))
        memory_layout.addWidget(self.memory_list)
        memory_layout.addWidget(QLabel("New Memory Text"))
        memory_layout.addWidget(self.memory_input)
        memory_layout.addWidget(self.memory_add_button)
        memory_layout.addWidget(self.memory_status)
        memory_tab = QWidget()
        memory_tab.setLayout(memory_layout)
        self.tab_widget.addTab(memory_tab, "Memory Viewer")

        # Controls
        self.ptt_button = QPushButton("Start PTT")
        self.ptt_button.clicked.connect(self._toggle_ptt)

        self.listen_button = QPushButton("Start Passive Listening")
        self.listen_button.clicked.connect(self._toggle_passive_listening)

        self.mute_button = QPushButton("Mute Penny")
        self.mute_button.clicked.connect(self._toggle_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(20)
        self.volume_slider.setValue(int(self.tts_service.volume_db_reduction))
        self.volume_slider.valueChanged.connect(self._update_volume)

        self.test_button = QPushButton("Test Speak")
        self.test_button.clicked.connect(self._test_speak)

        self.collab_button = QPushButton("Enable Collab Mode")
        self.collab_button.setCheckable(True)
        self.collab_button.setChecked(self.tts_service.collab_mode)
        self.collab_button.clicked.connect(self._toggle_collab_mode)

        self.vtuber_button = QPushButton("Toggle VTuber")
        self.vtuber_button.clicked.connect(self._toggle_vtuber)
        self.vtuber_config_button = QPushButton("Configure VTuber Parts")
        self.vtuber_config_button.clicked.connect(self._open_vtuber_config)
        self.status_label = QLabel("Status: Idle")
        self.talk_light = QLabel("🔇")
        self.talk_light.setStyleSheet("font-size: 24px; color: gray;")

        controls_layout = QVBoxLayout()
        controls_layout.addWidget(self.ptt_button)
        controls_layout.addWidget(self.listen_button)
        controls_layout.addWidget(self.mute_button)
        controls_layout.addWidget(QLabel("Volume (dB Reduction):"))
        controls_layout.addWidget(self.volume_slider)
        controls_layout.addWidget(self.test_button)
        controls_layout.addWidget(self.collab_button)
        controls_layout.addWidget(self.vtuber_button)
        controls_layout.addWidget(self.vtuber_config_button)
        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(self.talk_light)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tab_widget)
        main_layout.addLayout(controls_layout)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
    
    def init_vision_tab(self):
        self.vision_tab = QWidget()
        v_layout = QVBoxLayout()
        self.vision_tab.setLayout(v_layout)

        self.vision_window_list = QListWidget()
        self.vision_refresh_btn = QPushButton("Refresh Window List")
        self.vision_set_btn = QPushButton("Set Window for Vision")

        v_layout.addWidget(QLabel("Pick a window to reposition for 1920x1080 vision capture:"))
        v_layout.addWidget(self.vision_window_list)
        v_layout.addWidget(self.vision_refresh_btn)
        v_layout.addWidget(self.vision_set_btn)
        self.vision_toggle_btn = QPushButton("Enable Vision Loop")
        v_layout.addWidget(self.vision_toggle_btn)
        self.vision_toggle_btn.clicked.connect(self.toggle_vision_loop)
        self.tab_widget.addTab(self.vision_tab, "Vision Targeting") 

        # Connect buttons
        self.vision_refresh_btn.clicked.connect(self.populate_window_list)
        self.vision_set_btn.clicked.connect(self.set_selected_window)

    def toggle_vision_loop(self):
        if self.vision_service.is_running():
            self.vision_service.stop()
            self.vision_toggle_btn.setText("Enable Vision Loop")
            self.log_received.emit("info", "Vision loop disabled.")
        else:
            self.vision_service.toggle()
            self.vision_toggle_btn.setText("Disable Vision Loop")
            self.log_received.emit("info", "Vision loop enabled.")

    def populate_window_list(self):
        self.vision_window_list.clear()
        windows = self.window_manager.list_visible_windows()
        self.vision_window_list.addItems(windows)

    def set_selected_window(self):
        selected = self.vision_window_list.currentItem()
        if selected:
            title = selected.text()
            success = self.window_manager.move_and_resize_window(title)
            msg = f"✅ Moved: {title}" if success else f"❌ Failed to move: {title}"
            self.log_received.emit("info", msg)

    async def _store_memory_http(self, text):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.fastapi_url_main}/memory/store", json={"text": text}) as resp:
                    return await resp.json()
        except Exception as e:
            return {"stored": False, "error": str(e)}

    def _store_memory(self):
        text = self.memory_input.toPlainText().strip()
        if not text:
            self.memory_status.setText("Please enter memory text.")
            return
        asyncio.create_task(self._async_store_memory(text))

    async def _async_store_memory(self, text):
        result = await self._store_memory_http(text)
        if result.get("stored"):
            self.memory_status.setText("✅ Memory stored.")
            self.memory_input.clear()
        else:
            self.memory_status.setText(f"❌ Failed: {result.get('error')}")

    async def _query_memory_http(self, query):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.fastapi_url_main}/memory/query", json={"query": query}) as resp:
                    return await resp.json()
        except Exception as e:
            return {"matches": [], "error": str(e)}

    def _query_memory(self):
        query = self.memory_query.text().strip()
        if not query:
            self.memory_status.setText("Enter a query string.")
            return
        asyncio.create_task(self._async_query_memory(query))

    async def _async_query_memory(self, query):
        result = await self._query_memory_http(query)
        self.memory_list.clear()
        if "matches" in result:
            for match in result["matches"]:
                self.memory_list.addItem(f"{match['text']} [user={match['user']}, cat={match['category']}]")
            self.memory_status.setText("✅ Query complete.")
        else:
            self.memory_status.setText(f"❌ Query failed: {result.get('error')}")

    def _connect_signals(self):
        self.log_received.connect(self._update_log_output)
        self.tts_state_changed.connect(self._update_tts_status)
        self.ptt_state_changed.connect(self._update_ptt_status)

    def _register_events(self):
        self.event_bus.subscribe(UILogEvent, self._on_log)
        self.event_bus.subscribe(TTSSpeakingStateEvent, self._on_tts_state)
        self.event_bus.subscribe(PTTRecordingStateEvent, self._on_ptt_state)
        self.event_bus.subscribe(TwitchMessageEvent, self._on_chat_message)
        self.event_bus.subscribe(TwitchUserEvent, self._on_user_event)

    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return DEFAULT_SETTINGS

    def save_settings(self):
        geo = self.frameGeometry()
        new_window_settings = {
            "window": {
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height()
            }
        }

        # Load existing settings
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except Exception as e:
                print(f"[Settings] Failed to read settings.json: {e}")
                settings = {}
        else:
            settings = {}

        # Update only the window section
        settings.update(new_window_settings)

        # Save back merged settings
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)

    def _on_log(self, event: UILogEvent):
        self.log_received.emit(event.level, event.message)

    def _on_tts_state(self, event: TTSSpeakingStateEvent):
        self.tts_state_changed.emit(event.is_speaking)

    def _on_ptt_state(self, event: PTTRecordingStateEvent):
        self.ptt_state_changed.emit(event.is_recording)

    def _on_chat_message(self, event: TwitchMessageEvent):
        self.chat_box.appendPlainText(f"{event.username}: {event.message}")

    def _on_user_event(self, event: TwitchUserEvent):
        from datetime import datetime
        timestamp = datetime.now().strftime('%H:%M:%S')
        et = event.event_type.lower()
        user = event.username
        details = event.details

        color_map = {
            "channel.follow": "#00bcd4",
            "channel.subscribe": "#4caf50",
            "channel.subscription.gift": "#ff9800",
            "channel.subscription.message": "#8bc34a",
            "channel.raid": "#f44336",
        }
        color = color_map.get(et, "#ccc")

        if et == "channel.follow":
            msg = f"❤️ <b>{user}</b> followed"
        elif et == "channel.subscribe":
            msg = f"🌟 <b>{user}</b> subscribed"
        elif et == "channel.subscription.gift":
            count = details.get("total", 1)
            msg = f"🎁 <b>{user}</b> gifted <b>{count}</b> subs"
        elif et == "channel.subscription.message":
            months = details.get("cumulative_months", "several")
            msg = f"🔁 <b>{user}</b> resubscribed for <b>{months}</b> months"
        elif et == "channel.raid":
            viewers = details.get("viewer_count", 0)
            msg = f"🚀 <b>{user}</b> is raiding with <b>{viewers}</b> viewers"
        else:
            msg = f"❔ <b>{et}</b> from <b>{user}</b> | {details}"

        formatted = f'<span style="color:{color}">[{timestamp}] {msg}</span>'
        self.events_box.append(formatted)

    def _update_log_output(self, level: str, message: str):
        self.log_output.appendPlainText(f"[{level}] {message}")

    def _update_tts_status(self, is_speaking: bool):
        if is_speaking:
            self.status_label.setText("Status: Speaking...")
            self.talk_light.setText("🔊")
            self.talk_light.setStyleSheet("font-size: 24px; color: orange;")
        else:
            self.status_label.setText("Status: Idle")
            self.talk_light.setText("🔇")
            self.talk_light.setStyleSheet("font-size: 24px; color: gray;")

    def _update_ptt_status(self, is_recording: bool):
        self.ptt_button.setText("Stop PTT" if is_recording else "Start PTT")
        self.ptt_button.setStyleSheet("background-color: red;" if is_recording else "")
        if is_recording:
            self.status_label.setText("Status: Listening...")
            self.talk_light.setText("🎤")
            self.talk_light.setStyleSheet("font-size: 24px; color: green;")

    def _toggle_ptt(self):
        new_state = not self.audio_service.is_ptt_enabled()
        self.audio_service.set_ptt_enabled(new_state)
        self.ptt_controller.set_enabled(new_state)
        self.ptt_button.setText("Stop PTT" if new_state else "Start PTT")

    def _toggle_passive_listening(self):
        if self.listening_service.is_listening():
            asyncio.create_task(self.listening_service.stop_listening())
            self.listen_button.setText("Start Passive Listening")
        else:
            asyncio.create_task(self.listening_service.start_listening())
            self.listen_button.setText("Stop Passive Listening")

    def _toggle_mute(self):
        self.tts_service.set_is_muted(not self.tts_service.is_muted)
        self.mute_button.setText("Unmute Penny" if self.tts_service.is_muted else "Mute Penny")

    def _update_volume(self, value: int):
        self.tts_service.set_volume_reduction(float(value))

    def _test_speak(self):
        asyncio.create_task(self.event_bus.publish(
            SpeakRequestEvent(text="Hello, this is a test of the text to speech system.")
        ))

    def _toggle_collab_mode(self):
        enabled = not self.tts_service.collab_mode
        self.tts_service.toggle_collab_mode(enabled)
        self.collab_button.setText("Disable Collab Mode" if enabled else "Enable Collab Mode")
        self.collab_button.setChecked(enabled)

    def _toggle_vtuber(self):
        if self.vtuber_manager.is_active():
            self.vtuber_manager.stop()
            self.vtuber_button.setText("Launch VTuber")
        else:
            asyncio.create_task(self.vtuber_manager.start())
            self.vtuber_button.setText("Hide VTuber")
            
    def _open_vtuber_config(self):
        from penny_v2.vtuber.vtuber_config_window import VTuberConfigWindow
        self.vtuber_config_window = VTuberConfigWindow()
        self.vtuber_config_window.show()

    def eventFilter(self, obj, event):
        if not self.audio_service.is_ptt_enabled():
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            asyncio.create_task(self.ptt_controller.handle_key_press(event.key()))
            return True
        elif event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            asyncio.create_task(self.ptt_controller.handle_key_release(event.key()))
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        self.save_settings()
        event.accept()
