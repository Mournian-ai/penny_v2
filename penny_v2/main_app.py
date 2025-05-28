# penny_v2/main_app.py
import asyncio
import logging
import signal
import sys
import os
from typing import Optional, List, Any
import keyboard
import threading

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from qasync import QEventLoop # For integrating asyncio with Qt

from penny_v2.config import settings
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import AppShutdownEvent, UILogEvent

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

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s", # Added funcName
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

        # --- Initialize Core Services ---
        self.api_client_service = APIClientService(event_bus=self.event_bus, settings=settings)
        self.tts_service = TTSService(event_bus=self.event_bus, settings=settings)
        self.audio_service = AudioService(event_bus=self.event_bus, settings=settings)
        from penny_v2.services.ptt_controller import PTTController
        self.ptt_controller = PTTController(self.event_bus, self.audio_service, settings)
        self._start_ptt_listener_thread()
        self.vtuber_manager_service = VTuberManagerService(event_bus=self.event_bus)
        self.transcribe_service = TranscribeService(self.event_bus, settings)
        self.listening_service = ListeningService(self.event_bus, settings)
        self.vision_service = VisionService(event_bus=self.event_bus, settings=settings)
        
        # --- Initialize AI Service ---
        self.ai_service = StreamingOpenAIService(
            event_bus=self.event_bus,
            settings=settings
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
            self.vision_service,
        ]

        self._configure_signal_handlers()
        self._configure_event_logging()
        # Connect Qt's aboutToQuit signal for graceful application exit
        self.qt_app.aboutToQuit.connect(self._handle_about_to_quit)

    def _configure_signal_handlers(self):
        """Configures OS signal handlers for graceful shutdown."""
        for sig_val in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig_val,
                    lambda s=sig_val: asyncio.create_task(self._signal_triggered_shutdown(s), name=f"SignalShutdown-{s.name}")
                )
            except (NotImplementedError, AttributeError): # AttributeError for older Python/Windows
                logger.warning(f"asyncio.loop.add_signal_handler for {signal.Signals(sig_val).name} not supported on this platform. Relying on KeyboardInterrupt or UI close.")
                # Fallback for SIGINT on Windows if add_signal_handler isn't robust
                if sig_val == signal.SIGINT:
                    signal.signal(signal.SIGINT,
                        lambda s, f: asyncio.ensure_future(self._signal_triggered_shutdown(signal.Signals(s)), loop=self.loop)
                    )

    def _start_ptt_listener_thread(self):
        print("[PTT] Starting background listener thread...")
        thread = threading.Thread(
            target=listen_for_capslock,
            args=(self.ptt_controller, self.loop),
            daemon=True
        )
        thread.start()

    def _handle_about_to_quit(self):
        """
        Connected to QApplication.aboutToQuit.
        Initiates async shutdown from the Qt side.
        """
        logger.info("QApplication.aboutToQuit signal received. Initiating shutdown if not already in progress.")
        if not self._shutting_down:
            # Schedule the async shutdown. Do not block the Qt event handler.
            # The shutdown() method will eventually call self.loop.stop().
            asyncio.ensure_future(self.shutdown(triggered_by_signal=False, source_description="QApplication.quit"), loop=self.loop)

    async def _signal_triggered_shutdown(self, sig: signal.Signals):
        logger.info(f"OS Signal {sig.name} received. Initiating shutdown...")
        if not self._shutting_down:
            await self.shutdown(triggered_by_signal=True, source_description=f"OS Signal {sig.name}")

    def _configure_event_logging(self):
        """(Optional) Logs all events published to the event bus for debugging."""
        # original_publish = self.event_bus.publish
        # async def new_publish(event: Any):
        # logger.debug(f"[EVENT_BUS_TRACE] Publishing event: {type(event).__name__} - {event}")
        # await original_publish(event)
        # self.event_bus.publish = new_publish
        pass # Keep disabled unless debugging event flow

    async def start_services(self):
        logger.info("Starting all services...")
        if hasattr(self.twitch_eventsub_service, 'start'):
            logger.info("Starting TwitchEventSubService...")
            try:
                await self.twitch_eventsub_service.start()
                logger.info("TwitchEventSubService started.")
            except Exception as e:
                logger.error("Error starting TwitchEventSubService:", exc_info=True)
        if hasattr(self.twitch_chat_service, 'start'):
            logger.info("Starting TwitchChatService after EventSub refresh...")
            try:
                await self.twitch_chat_service.start()
            except Exception as e:
                logger.error(f"Error starting TwitchChatService: {e}", exc_info=True)

        # Step 3: Start the rest in parallel
        other_services = [
            self.api_client_service,
            self.ai_service,
            self.tts_service,
            self.audio_service,
            self.interaction_service,
            self.transcribe_service,
            self.vision_service,
        ]

        start_tasks = []
        for service in other_services:
            if hasattr(service, 'start'):
                service_name = service.__class__.__name__
                start_tasks.append(asyncio.create_task(service.start(), name=f"Start-{service_name}"))

        if start_tasks:
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                task_name = start_tasks[i].get_name()
                if isinstance(result, Exception):
                    logger.error(f"Error starting service via task {task_name}: {result}", exc_info=result)

        logger.info("All service start routines attempted.")


    async def stop_services(self):
        logger.info("Stopping all services...")
        await self.event_bus.publish(AppShutdownEvent())
        await asyncio.sleep(0.1) # Give services a moment to react to AppShutdownEvent

        stop_tasks = []
        services_to_stop = [s for s in reversed(self._services) if hasattr(s, 'stop')]

        for service in services_to_stop:
            service_name = service.__class__.__name__
            logger.info(f"Initiating stop for service: {service_name}")
            if asyncio.iscoroutinefunction(service.stop):
                stop_tasks.append(asyncio.create_task(service.stop(), name=f"Stop-{service_name}"))
            else:
                try:
                    service.stop()
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
            logger.info("Closing main UI window (if Qt hasn't already started)...")
            # self.ui_service.close() # Usually triggers aboutToQuit, which calls this shutdown again.
                                     # Avoid direct call if aboutToQuit is the primary driver.
                                     # If shutdown is called by signal, then closing UI is okay.
            if triggered_by_signal: # If shutdown was not from UI closing itself
                 self.ui_service.close()


        logger.info("Checking for any other remaining asyncio tasks...")
        current_task = asyncio.current_task()
        tasks_to_cancel = [
            task for task in asyncio.all_tasks(loop=self.loop)
            if task is not current_task and not task.done()
        ]
        if tasks_to_cancel:
            logger.info(f"Cancelling {len(tasks_to_cancel)} other outstanding tasks explicitly...")
            for task in tasks_to_cancel:
                logger.debug(f"  Cancelling task: {task.get_name()} ({task.get_coro().__qualname__ if hasattr(task.get_coro(), '__qualname__') else task.get_coro()})")
                task.cancel()
            
            done, pending = await asyncio.wait(tasks_to_cancel, timeout=2.0, return_when=asyncio.ALL_COMPLETED)
            if pending:
                logger.warning(f"{len(pending)} fallback tasks did not complete cancellation within timeout.")
                for task in pending:
                    logger.warning(f"    - Still pending: {task.get_name()}")
            else:
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
            # Using 'with self.loop:' ensures loop.close() is called on exit if qasync supports it.
            # If your qasync version/setup doesn't use 'with', ensure loop.close() in finally.
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
            # This block runs after run_forever() returns (i.e., after loop.stop() has been called).
            # Ensure shutdown has fully completed. If shutdown didn't run (e.g. loop stopped for other reasons),
            # it's hard to do async cleanup here. The goal is that shutdown() runs *before* this.
            if not self._shutting_down:
                 logger.warning("Loop exited without shutdown sequence. _shutting_down is False.")
                 # This indicates an abnormal loop termination.
                 # Attempt a synchronous cleanup if possible, but async operations will fail.
            
            # loop.close() is typically handled by 'with self.loop:'
            # If not using 'with', or for belt-and-suspenders:
            if hasattr(self.loop, 'close') and not self.loop.is_closed():
                 logger.info("Closing QEventLoop explicitly in run() finally.")
                 self.loop.close()
            else:
                logger.info("QEventLoop already closed or close method not found/needed by context manager.")

            logger.info(f"Penny V2 (Qt) Application exiting with code {exit_code}.")
            # QApplication manages the actual process exit after its event loop finishes.
            # sys.exit(exit_code) might be redundant if Qt handles it, but can ensure exit.
            # If QApplication.quit() was called, it should lead to a clean exit.

def listen_for_capslock(ptt_controller, loop):
    print("[PTT] Global Caps Lock listener started.")
    while True:
        keyboard.wait("caps lock")
        if ptt_controller.enabled:
            print("[PTT] Caps Lock pressed")  # Optional
            asyncio.run_coroutine_threadsafe(
                ptt_controller.handle_key_press(keyboard.key_to_scan_code("caps lock")),
                loop
            )
            while keyboard.is_pressed("caps lock"):
                pass
            print("[PTT] Caps Lock released")  # Optional
            asyncio.run_coroutine_threadsafe(
                ptt_controller.handle_key_release(keyboard.key_to_scan_code("caps lock")),
                loop
            )

if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)    
    app_instance = PennyV2QtApp()
    app_instance.run()
