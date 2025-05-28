# penny_v2/services/streaming_openai_service.py
import logging
import asyncio
from openai import AsyncOpenAI

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    AIQueryEvent,
    AIResponseEvent,
    SpeakRequestEvent,
    UILogEvent,
    VisionSummaryEvent, # Added
)
from penny_v2.services.context_manager import ContextManager # Added

logger = logging.getLogger(__name__)

class StreamingOpenAIService:
    def __init__(self, event_bus: EventBus, settings: AppConfig, context_manager: ContextManager): # Added context_manager
        self.event_bus = event_bus
        self.settings = settings
        self.context_manager = context_manager # Added
        self._running = False
        self.client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)

    async def start(self):
        if self._running:
            logger.info("StreamingOpenAIService already running.")
            return
        self._running = True
        self.event_bus.subscribe_async(AIQueryEvent, self.handle_query)
        self.event_bus.subscribe_async(VisionSummaryEvent, self.handle_vision_summary) # Added
        logger.info("StreamingOpenAIService started and listening.")

    async def stop(self):
        self._running = False
        logger.info("StreamingOpenAIService stopped.")

    async def handle_vision_summary(self, event: VisionSummaryEvent): # Added
        """Handles updates to the vision context."""
        logger.debug(f"Updating vision context: {event.summary[:100]}...")
        self.context_manager.set_vision_context(event.summary)

    async def handle_query(self, event: AIQueryEvent):
        # Build the prompt using ContextManager
        full_prompt = self.context_manager.build_prompt(
            current_input=event.input_text,
            include_vision=event.include_vision_context
        ).strip() # Modified

        if not full_prompt:
            logger.warning("Built prompt is empty, skipping query.")
            return

        logger.info(f"[StreamingOpenAI] Built Prompt: {full_prompt[:200]}...") # Log built prompt
        try:
            model_name = self.settings.get_dynamic_model_name()
            # Pass the original input for context update later
            await self.stream_response(full_prompt, model_name, event.input_text, event.instruction) # Added event.input_text and instruction
        except Exception as e:
            logger.error(f"[StreamingOpenAI] Error: {e}", exc_info=True) # Added exc_info

    async def stream_response(self, prompt: str, model_name: str, original_input: str, instruction: str | None): # Added original_input and instruction
        full_response = []
        buffer = ""

        # Use the instruction if provided, otherwise use a default system prompt
        system_message_content = instruction or "You are Penny, a sarcastic but helpful AI streaming companion. Respond in a clever, expressive way." # Modified

        messages = [
                {"role": "system", "content": system_message_content},
                {"role": "user", "content": prompt},
            ]

        logger.debug(f"Sending messages to OpenAI: {messages}")

        response = await self.client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=True,
            temperature=0.7,
            max_tokens=300,
        )

        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                token = delta.content
                full_response.append(token)
                buffer += token

                if self._should_flush(buffer):
                    await self.event_bus.publish(SpeakRequestEvent(text=buffer.strip(), collab_mode=False))
                    buffer = ""

        if buffer.strip():
            await self.event_bus.publish(SpeakRequestEvent(text=buffer.strip(), collab_mode=False))

        final = "".join(full_response).strip()
        if final:
            logger.info(f"[StreamingOpenAI] Final Response: {final}")
            await self.event_bus.publish(
                AIResponseEvent(text_to_speak=final, original_query=prompt)
            )
            await self.event_bus.publish(
                UILogEvent(message=f"Penny: {final}", level="INFO")
            )
            # Update context AFTER getting the full response
            self.context_manager.update_chat(original_input, final) # Added context update
            logger.debug(f"Updated chat context. History size: {len(self.context_manager.chat_history)}")


    def _should_flush(self, buffer: str) -> bool:
        buffer = buffer.strip()
        if not buffer:
            return False
        # Flush on sentence-ending punctuation
        if any(buffer.endswith(p) for p in [".", "!", "?"]):
            return True
        # Flush on longer pauses (comma, semicolon, colon) after some length
        if len(buffer.split()) > 5 and any(buffer.endswith(p) for p in [",", ";", ":"]): # Increased word count
            return True
        return False
