# penny_v2/services/ai_service.py
import logging
from typing import Optional

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.services.context_manager import ContextManager
from penny_v2.core.events import (
    VisionSummaryEvent,
    AppShutdownEvent,
    UILogEvent,
    AIQueryEvent,
    AIResponseEvent,
    SpeakRequestEvent,
)
from penny_v2.services.api_client_service import APIClientService

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, event_bus: EventBus, settings: AppConfig, api_client: APIClientService):
        self.context_manager = ContextManager()
        self.event_bus = event_bus
        self.settings = settings
        self.api_client = api_client
        self._running = False

    async def start(self):
        if self._running:
            logger.info("AIService already running.")
            return

        logger.info("AIService starting...")
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
        self.event_bus.subscribe_async(AIQueryEvent, self.handle_ai_query)
        self.event_bus.subscribe_async(VisionSummaryEvent, self.handle_vision_summary)
        self._running = True
        logger.info("AIService started and listening for AIQueryEvent.")

    async def stop(self):
        if not self._running:
            return
        logger.info("AIService stopping...")
        self._running = False
        logger.info("AIService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent):
        await self.stop()

    async def handle_ai_query(self, event: AIQueryEvent):
        logger.info(f"AIService received AIQueryEvent with input: {event.input_text[:100]}...")

        # Determine context inclusion
        if getattr(event, "source", None) == "mournian":
            prompt = self.context_manager.build_prompt(
                current_input=event.input_text,
                include_vision=getattr(event, "include_vision_context", False)
            )
        else:
            prompt = event.input_text

        response = await self.api_client.get_ai_core_response_text(
            prompt=prompt,
            instruction=event.instruction or ""
        )

        if response is None:
            logger.warning("AIService received no response from APIClientService.")
            await self.event_bus.publish(SpeakRequestEvent(text="Sorry, I couldn't get a response for that."))
            return

        await self.event_bus.publish(SpeakRequestEvent(text=response))

        if getattr(event, "source", None) == "mournian":
            self.context_manager.update_chat(event.input_text, response)

        await self.event_bus.publish(AIResponseEvent(
            output=response,
            original_input=event.input_text,
            original_instruction=event.instruction
        ))

    async def handle_vision_summary(self, event: VisionSummaryEvent):
        self.context_manager.set_vision_context(event.summary)
