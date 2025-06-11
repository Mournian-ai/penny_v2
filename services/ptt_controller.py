# penny_v2/services/ptt_controller.py
import asyncio
import logging
from PyQt6.QtCore import Qt

from penny_v2.core.event_bus import EventBus
from penny_v2.config import AppConfig
from penny_v2.services.audio_service import AudioService

logger = logging.getLogger(__name__)

class PTTController:
    def __init__(self, event_bus: EventBus, audio_service: AudioService, settings: AppConfig):
        self.event_bus = event_bus
        self.audio_service = audio_service
        self.settings = settings

        self.enabled: bool = False   # Controlled by UI toggle
        self.active: bool = False    # True while key is held

        # Resolve PTT key from .env settings (defaults to Caps Lock)
        self._ptt_key = self._resolve_key(settings.PTT_KEY or "caps lock")

    def _resolve_key(self, key_name: str) -> int:
        key_map = {
            "caps lock": Qt.Key.Key_CapsLock,
            "ctrl": Qt.Key.Key_Control,
            "shift": Qt.Key.Key_Shift,
            "alt": Qt.Key.Key_Alt,
            "space": Qt.Key.Key_Space,
        }
        resolved = key_map.get(key_name.strip().lower(), Qt.Key.Key_CapsLock)
        logger.info(f"[PTT] Resolved PTT key '{key_name}' -> Qt key {resolved}")
        return resolved

    def set_enabled(self, value: bool):
        """Enable or disable PTT system-wide."""
        self.enabled = value
        logger.info(f"[PTT] Push-to-Talk {'enabled' if value else 'disabled'}")

        # Safety: stop if active when disabling
        if not value and self.active:
            self.active = False
            asyncio.create_task(self.audio_service.stop_ptt_recording())

    async def handle_key_press(self, key: int):
        if not self.enabled or self.active:
            logger.debug(f"[PTT] Ignoring key press (enabled={self.enabled}, active={self.active})")
            return
        if key == self._ptt_key:
            self.active = True
            logger.info(f"[PTT] '{self.settings.PTT_KEY}' pressed — starting recording")
            await self.audio_service.start_ptt_recording()
        else:
            logger.debug(f"[PTT] Key {key} does not match PTT key {self._ptt_key}")

    async def handle_key_release(self, key: int):
        if not self.enabled or not self.active:
            return
        if key == self._ptt_key:
            self.active = False
            logger.info(f"[PTT] '{self.settings.PTT_KEY}' released — stopping recording")
            await self.audio_service.stop_ptt_recording()
