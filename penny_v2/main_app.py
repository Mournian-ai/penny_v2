# penny_v2/main_app.py
import asyncio
import logging
import signal
import sys
import os
from typing import Optional, List, Any
# import keyboard # Commenting out PTT keyboard listener - needs review
import threading

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from qasync import QEventLoop # For integrating asyncio with Qt

from penny_v2.config import settings
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import AppShutdownEvent, UILogEvent
from penny_v2.services.context_manager import ContextManager # Added

# Import all your services
from penny_v2.services.qt_ui_service import QtDashboard
from penny_v2.services.api_client_service import APIClientService
from penny_v2.services.streaming_openai_service import StreamingOpenAIService
from penny_v2.services.audio_service import AudioService
from penny_v2.services.tts_service import TTSService
from penny_v2.services.twitch_eventsub_service import TwitchEventSubService
from penny_v2.services.twitch_chat_service import TwitchChatService
from penny_v2.services.interaction_service import InteractionService
from penny_v2.vtuber.vtuber_manager import VTuberManagerService
from penny_v2.services.transcribe_service import TranscribeService
from penny_v2.services.listening_service import ListeningService
from penny_v2.vision.vision_service import VisionService
# from penny_v2.services.ai_service import AIService # Removed

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
        self.context_manager = ContextManager() # Added

        # --- Initialize Core Services ---
        self.api_client_service = APIClientService(event_bus=self.event_bus, settings=settings)
        self.tts_service = TTSService(event_bus=self.event_bus, settings=settings)
        self.audio_service = AudioService(event_bus=self.event_bus, settings=settings)
        from penny_v2.services.ptt_controller import PTTController
        self.ptt_controller = PTTController(self.event_bus, self.audio_service, settings)
        # self._start_ptt_listener_thread() # Keep commented out - see notes
        self.vtuber_manager_service = VTuberManagerService(event_bus=self.event_bus)
        self.transcribe_service = TranscribeService(self.event_bus, settings)
        self.listening_service = ListeningService(self.event_bus, settings)
        self.vision_service = VisionService(event_bus=self.event_bus, settings=settings)

        # --- Initialize AI Service ---
        self.ai_service = StreamingOpenAIService(
            event_bus=self.event_bus,
            settings=settings,
            context_manager=self.context_manager # Passed context_manager
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
            self.twitch_chat_service, # Added
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
                logger.warning(f"asyncio.loop.add_signal_handler for {signal.Signals(sig_val).name} not supported on this platform. Relying on KeyboardInterrupt or UI close.")
                if sig_val == signal.SIGINT:
                    signal.signal(signal.SIGINT,
                        lambda s, f: asyncio.ensure_future(self._signal_triggered_shutdown(signal.Signals(s)), loop=self.loop)
                    )

    def _start_ptt_listener_thread(self):
        logger.warning("PTT listener thread is disabled. 'keyboard' library can conflict with Qt and requires root/admin. Use Qt's event filter (only works when focused).")
        pass # Keep disabled

    def _handle_about_to_quit(self):
        logger.info("QApplication.aboutToQuit signal received. Initiating shutdown if not already in progress.")
        if not self._shutting_down:
            asyncio.ensure_future(self.shutdown(triggered_by_signal=False, source_description="QApplication.quit"), loop=self.loop)

    async def _signal_triggered_shutdown(self, sig: signal.Signals):
        logger.info(f"OS Signal {sig.name} received. Initiating shutdown...")
        if not self._shutting_down:
            await self.shutdown(triggered_by_signal=True, source_description=f"OS Signal {sig.name}")

    def _configure_event_logging(self):
        pass

    async def start_services(self):
        logger.info("Starting all services...")
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
                try:
                    stop_method()
                    logger.info(f"Synchronous stop for {service_name} completed.")
                except Exception as e:
                    logger.error(f"Error during synchronous stop of {service_name}: {e}", exc_info=True)

        if stop_tasks:
            logger.info(f"Awaiting {len(stop_tasks)} asynchronous service stop tasks...")
            results = await asyncio.gather(*stop_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                task_name = stop_tasks[i].get_name()
                if isinstance(result, Exception):
                    logger.error(f"Error stopping service via task {task_name}: {result}", exc_info=result)
                else:
                    logger.info(f"Asynchronous stop task {task_name} completed successfully.")
        else:
            logger.info("No asynchronous service stop tasks to await.")
        logger.info("All service stop routines have been initiated and awaited if async.")

    async def shutdown(self, triggered_by_signal: bool = False, source_description: str = "unknown"):
        if self._shutting_down:
            logger.info(f"Shutdown already in progress (requested by {source_description}). Ignoring additional request.")
            return
        self._shutting_down = True

        logger.info(f"Penny V2 application shutdown initiated by {source_description}...")
        await self.stop_services()

        if self.ui_service and self.ui_service.isVisible():
            logger.info("Closing main UI window...")
            if triggered_by_signal:
                 self.ui_service.close()

        logger.info("Checking for any other remaining asyncio tasks...")
        current_task = asyncio.current_task()
        tasks_to_cancel = [
            task for task in asyncio.all_tasks(loop=self.loop)
            if task is not current_task and not task.done()
        ]
        if tasks_to_cancel:
            logger.info(f"Cancelling {len(tasks_to_cancel)} other outstanding tasks explicitly...")
            for task in tasks_to_cancel: task.cancel()
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            logger.info("All fallback tasks processed after cancellation request.")
        else:
            logger.info("No other outstanding tasks needed explicit cancellation.")

        if self.loop.is_running():
            logger.info("Requesting asyncio event loop (qasync) to stop.")
            self.loop.stop()
        else:
            logger.info("Asyncio event loop (qasync) was not running when shutdown requested its stop.")
        logger.info(f"Penny V2 async shutdown sequence initiated by {source_description} complete. Loop should now terminate.")

    def run(self):
        logger.info("Penny V2 (Qt) Application starting up...")
        asyncio.ensure_future(self.start_services(), loop=self.loop)
        self.ui_service.show()
        exit_code = 0
        try:
            logger.info("Starting QEventLoop (run_forever)...")
            with self.loop:
                self.loop.run_forever()
            logger.info("QEventLoop (run_forever) has returned.")
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received by run(). Initiating shutdown...")
            if not self._shutting_down:
                if self.loop.is_running():
                    self.loop.run_until_complete(self.shutdown(triggered_by_signal=True, source_description="KeyboardInterrupt"))
                else:
                    logger.error("KeyboardInterrupt: Loop stopped before async shutdown could complete.")
            logger.info("KeyboardInterrupt shutdown process finished.")
        except SystemExit as e:
            logger.info(f"SystemExit caught in run() with code: {e.code}")
            exit_code = e.code
        finally:
            logger.info("Run method's finally block executing.")
            if not self._shutting_down: logger.warning("Loop exited without shutdown sequence.")
            if hasattr(self.loop, 'close') and not self.loop.is_closed():
                 logger.info("Closing QEventLoop explicitly in run() finally.")
                 self.loop.close()
            else:
                logger.info("QEventLoop already closed or close method not found/needed by context manager.")
            logger.info(f"Penny V2 (Qt) Application exiting with code {exit_code}.")

if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app_instance = PennyV2QtApp()
    app_instance.run()
