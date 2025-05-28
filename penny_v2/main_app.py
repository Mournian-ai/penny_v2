# penny_v2/main_app.py
import asyncio
import logging
import signal
import sys
import os
import json # Added
import time # Added
from typing import Optional, List, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from qasync import QEventLoop # For integrating asyncio with Qt

from penny_v2.config import settings
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import AppShutdownEvent, UILogEvent
from penny_v2.services.context_manager import ContextManager
from penny_v2.services.twitch_token_refresh import TwitchTokenManager # Added

# Import all your services
from penny_v2.services.qt_ui_service import QtDashboard
from penny_v2.services.api_client_service import APIClientService
from penny_v2.services.streaming_openai_service import StreamingOpenAIService
from penny_v2.services.audio_service import AudioService
from penny_v2.services.tts_service import TTSService
from penny_v2.services.twitch_eventsub_service import TwitchEventSubService
from penny_v2.services.twitch_chat_service import TwitchChatService
from penny_v2.services.interaction_service import InteractionService
from penny_v2.services.vtuber.vtuber_manager import VTuberManagerService
from penny_v2.services.transcribe_service import TranscribeService
from penny_v2.services.listening_service import ListeningService
from penny_v2.services.vision.vision_service import VisionService
from penny_v2.services.ptt_controller import PTTController

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("penny_v2.log", mode='a')
    ]
)
logger = logging.getLogger(__name__)
os.environ["QT_OPENGL"] = "software"
SETTINGS_FILE = "settings.json" # Added

class PennyV2QtApp:
    def __init__(self):
        self.qt_app = QApplication.instance()
        if not self.qt_app:
            self.qt_app = QApplication(sys.argv)

        self.loop = QEventLoop(self.qt_app)
        asyncio.set_event_loop(self.loop)

        self.event_bus = EventBus()
        self._services: List[Any] = []
        self._shutting_down = False

        # --- Instantiate Context Manager ---
        self.context_manager = ContextManager()

        # --- Instantiate Token Manager --- # Added Section
        self.token_manager = TwitchTokenManager(settings=settings)

        # --- Initialize Core Services ---
        self.api_client_service = APIClientService(event_bus=self.event_bus, settings=settings)
        self.tts_service = TTSService(event_bus=self.event_bus, settings=settings)
        self.audio_service = AudioService(event_bus=self.event_bus, settings=settings)
        self.ptt_controller = PTTController(self.event_bus, self.audio_service, settings)
        self.vtuber_manager_service = VTuberManagerService(event_bus=self.event_bus)
        self.transcribe_service = TranscribeService(self.event_bus, settings)
        self.listening_service = ListeningService(self.event_bus, settings)
        self.vision_service = VisionService(event_bus=self.event_bus, settings=settings)

        # --- Initialize AI Service ---
        self.ai_service = StreamingOpenAIService(
            event_bus=self.event_bus,
            settings=settings,
            context_manager=self.context_manager
        )

        # --- Initialize Twitch Integration Services ---
        self.twitch_eventsub_service = TwitchEventSubService(
            event_bus=self.event_bus,
            settings=settings
        )
        self.twitch_chat_service = TwitchChatService(
            event_bus=self.event_bus,
            api_client_service=self.api_client_service,
            settings=settings
        )

        # --- Initialize Interaction Logic Service ---
        self.interaction_service = InteractionService(
            event_bus=self.event_bus,
            settings=settings,
            api_client=self.api_client_service
        )

        # --- Initialize UI Service (QtDashboard) ---
        self.ui_service = QtDashboard(
            event_bus=self.event_bus,
            tts_service=self.tts_service,
            audio_service=self.audio_service,
            listening_service=self.listening_service,
            vtuber_manager=self.vtuber_manager_service,
            vision_service=self.vision_service,
            settings=settings,
        )

        # --- Populate list of services ---
        self._services = [
            self.api_client_service,
            self.ai_service,
            self.tts_service,
            self.audio_service,
            self.interaction_service,
            self.transcribe_service,
            self.listening_service,
            self.twitch_eventsub_service,
            self.twitch_chat_service,
            self.vision_service,
        ]

        self._configure_signal_handlers()
        self._configure_event_logging()
        self.qt_app.aboutToQuit.connect(self._handle_about_to_quit)

    def _configure_signal_handlers(self):
        """Configures OS signal handlers for graceful shutdown."""
        for sig_val in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig_val,
                    lambda s=sig_val: asyncio.create_task(self._signal_triggered_shutdown(s), name=f"SignalShutdown-{s.name}")
                )
            except (NotImplementedError, AttributeError):
                logger.warning(f"asyncio.loop.add_signal_handler for {signal.Signals(sig_val).name} not supported. Relying on KeyboardInterrupt or UI close.")
                if sig_val == signal.SIGINT:
                    signal.signal(signal.SIGINT,
                        lambda s, f: asyncio.ensure_future(self._signal_triggered_shutdown(signal.Signals(s)), loop=self.loop)
                    )

    def _handle_about_to_quit(self):
        """Connected to QApplication.aboutToQuit."""
        logger.info("QApplication.aboutToQuit signal received. Initiating shutdown.")
        if not self._shutting_down:
            asyncio.ensure_future(self.shutdown(triggered_by_signal=False, source_description="QApplication.quit"), loop=self.loop)

    async def _signal_triggered_shutdown(self, sig: signal.Signals):
        logger.info(f"OS Signal {sig.name} received. Initiating shutdown...")
        if not self._shutting_down:
            await self.shutdown(triggered_by_signal=True, source_description=f"OS Signal {sig.name}")

    def _configure_event_logging(self):
        pass

    # --- Added Token Refresh Logic ---
    def _load_token_expiry(self) -> dict:
        """Loads token expiry times from settings.json."""
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("tokens", {})
        except Exception as e:
            logger.error(f"Failed to load token expiry from {SETTINGS_FILE}: {e}")
        return {}

    async def _periodic_token_refresh(self, check_interval_minutes=60, refresh_threshold_hours=6):
        """Periodically checks and refreshes tokens based on expiry time."""
        logger.info(f"Starting expiry-based token refresh (check every {check_interval_minutes} min, refresh if < {refresh_threshold_hours}h).")
        threshold_seconds = refresh_threshold_hours * 60 * 60

        while not self._shutting_down:
            try:
                # Wait for the interval BEFORE checking, but check shutdown before/after.
                await asyncio.sleep(check_interval_minutes * 60)
                if self._shutting_down: break

                logger.info("Checking token expiry...")
                expiry_data = self._load_token_expiry()
                now = int(time.time())

                app_expires_at = expiry_data.get("TWITCH_APP_TOKEN_EXPIRES_AT", 0)
                chat_expires_at = expiry_data.get("TWITCH_CHAT_TOKEN_EXPIRES_AT", 0)

                # Check App Token
                if app_expires_at <= (now + threshold_seconds):
                    logger.info("App token expires soon (or unknown/past). Refreshing now.")
                    await self.token_manager.refresh_app_token()
                else:
                    logger.info(f"App token OK. Expires in { (app_expires_at - now) / 3600:.1f} hours.")

                # Check Chat Token
                if chat_expires_at <= (now + threshold_seconds):
                    logger.info("Chat token expires soon (or unknown/past). Refreshing now.")
                    await self.token_manager.refresh_chat_token()
                else:
                     logger.info(f"Chat token OK. Expires in { (chat_expires_at - now) / 3600:.1f} hours.")

            except asyncio.CancelledError:
                logger.info("Token refresh task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error during token expiry check/refresh: {e}", exc_info=True)
                await asyncio.sleep(60 * 5) # Wait 5 mins on error before next check

    async def start_services(self):
        """Starts all services, including an initial token refresh."""
        logger.info("Starting all services...")

        # --- STEP 1: Perform INITIAL Refresh ---
        logger.info("Performing initial Twitch App Token refresh...")
        await self.token_manager.refresh_app_token()
        logger.info("Performing initial Twitch Chat Token refresh...")
        await self.token_manager.refresh_chat_token()
        logger.info("Initial token refreshes complete.")

        # --- STEP 2: Start Services ---
        start_tasks = []
        for service in self._services:
            if hasattr(service, 'start'):
                service_name = service.__class__.__name__
                logger.info(f"Preparing to start {service_name}...")
                start_tasks.append(asyncio.create_task(service.start(), name=f"Start-{service_name}"))

        if start_tasks:
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                task_name = start_tasks[i].get_name()
                if isinstance(result, Exception):
                    logger.error(f"Error starting service via task {task_name}: {result}", exc_info=result)
                else:
                    logger.info(f"Service task {task_name} initiated.")
        logger.info("All service start routines attempted.")

    async def stop_services(self):
        """Stops all registered services gracefully."""
        logger.info("Stopping all services...")
        await self.event_bus.publish(AppShutdownEvent())
        await asyncio.sleep(0.1)

        stop_tasks = []
        services_to_stop = [s for s in reversed(self._services) if hasattr(s, 'stop')]

        for service in services_to_stop:
            service_name = service.__class__.__name__
            logger.info(f"Initiating stop for service: {service_name}")
            stop_method = getattr(service, 'stop')
            if asyncio.iscoroutinefunction(stop_method):
                stop_tasks.append(asyncio.create_task(stop_method(), name=f"Stop-{service_name}"))
            else:
                try: stop_method()
                except Exception as e: logger.error(f"Sync stop error {service_name}: {e}", exc_info=True)

        if stop_tasks:
            logger.info(f"Awaiting {len(stop_tasks)} async stop tasks...")
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        logger.info("All service stop routines initiated.")

    async def shutdown(self, triggered_by_signal: bool = False, source_description: str = "unknown"):
        """Handles the complete application shutdown sequence."""
        if self._shutting_down: return
        self._shutting_down = True

        logger.info(f"Penny V2 shutdown initiated by {source_description}...")
        await self.stop_services()

        if self.ui_service and self.ui_service.isVisible() and triggered_by_signal:
             self.ui_service.close()

        logger.info("Checking for remaining asyncio tasks...")
        current_task = asyncio.current_task()
        tasks_to_cancel = [ t for t in asyncio.all_tasks(loop=self.loop) if t is not current_task and not t.done() ]
        if tasks_to_cancel:
            logger.info(f"Cancelling {len(tasks_to_cancel)} tasks...")
            for task in tasks_to_cancel: task.cancel()
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        else: logger.info("No tasks to cancel.")

        if self.loop.is_running(): self.loop.stop()
        logger.info("Penny V2 shutdown complete.")

    def run(self):
        """Starts the application and its event loop."""
        logger.info("Penny V2 (Qt) Application starting up...")
        asyncio.ensure_future(self.start_services(), loop=self.loop)
        asyncio.ensure_future(self._periodic_token_refresh(), loop=self.loop) # Added refresh task
        self.ui_service.show()
        exit_code = 0
        try:
            logger.info("Starting QEventLoop...")
            with self.loop: self.loop.run_forever()
            logger.info("QEventLoop returned.")
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt. Shutting down...")
            if not self._shutting_down: self.loop.run_until_complete(self.shutdown(True, "KeyboardInterrupt"))
        except SystemExit as e:
            logger.info(f"SystemExit: {e.code}")
            exit_code = e.code
        finally:
            logger.info("Run finally block.")
            if not self._shutting_down: logger.warning("Loop exited unexpectedly.")
            if hasattr(self.loop, 'close') and not self.loop.is_closed(): self.loop.close()
            logger.info(f"Penny V2 exiting (Code: {exit_code}).")

if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app_instance = PennyV2QtApp()
    app_instance.run()
