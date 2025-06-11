# penny_v2/vtuber/vtuber_manager.py
import asyncio
import logging
from penny_v2.vtuber.vtuber_window import QtVTuberWindow
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import AudioRMSVolumeEvent, AppShutdownEvent

logger = logging.getLogger(__name__)

class VTuberManagerService:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.vtuber_window_instance: QtVTuberWindow | None = None
        self._start_lock = asyncio.Lock()
        self._subscribed = False

    async def start(self):
        if self.vtuber_window_instance:
            logger.info("VTuberManagerService already active.")
            if not self.vtuber_window_instance.isVisible():
                self.vtuber_window_instance.show()
            return

        async with self._start_lock:
            if self.vtuber_window_instance:
                return  # double-checked after acquiring lock

            logger.info("VTuberManagerService starting...")

            # Subscribe only once
            if not self._subscribed:
                self.event_bus.subscribe_async(AudioRMSVolumeEvent, self.handle_audio_rms_volume)
                self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
                self._subscribed = True

            try:
                self.vtuber_window_instance = QtVTuberWindow()
                self.vtuber_window_instance.show()
                logger.info("VTuberWindow created and shown.")
            except Exception as e:
                logger.error(f"Failed to initialize VTuberWindow: {e}", exc_info=True)
                self.vtuber_window_instance = None

    def stop(self):
        logger.info("VTuberManagerService stopping...")

        if self._subscribed:
            self.event_bus.unsubscribe(AudioRMSVolumeEvent, self.handle_audio_rms_volume)
            self.event_bus.unsubscribe(AppShutdownEvent, self.handle_shutdown)
            self._subscribed = False

        if self.vtuber_window_instance:
            logger.info("Closing VTuber window...")
            self.vtuber_window_instance.close()
            self.vtuber_window_instance = None

        logger.info("VTuberManagerService stopped.")

    async def handle_audio_rms_volume(self, event: AudioRMSVolumeEvent):
        if self.vtuber_window_instance:
            try:
                # pyqtSignal is thread-safe for emit
                self.vtuber_window_instance.update_volume_signal.emit(event.rms_volume)
            except Exception as e:
                logger.warning(f"Failed to emit RMS volume to VTuber window: {e}")

    async def handle_shutdown(self, event: AppShutdownEvent):
        logger.info("Handling AppShutdownEvent — closing VTuber.")
        self.stop()

    def toggle(self):
        if self.vtuber_window_instance and self.vtuber_window_instance.isVisible():
            logger.info("Hiding VTuber window.")
            self.vtuber_window_instance.hide()
        elif self.vtuber_window_instance:
            logger.info("Showing existing VTuber window.")
            self.vtuber_window_instance.show()
        else:
            if self._start_lock.locked():
                logger.info("VTuber window creation already in progress.")
                return

            logger.info("Creating VTuber window via toggle.")
            async def _start_safely():
                async with self._start_lock:
                    await self.start()

            asyncio.create_task(_start_safely())

    def is_active(self) -> bool:
        return self.vtuber_window_instance is not None and self.vtuber_window_instance.isVisible()
