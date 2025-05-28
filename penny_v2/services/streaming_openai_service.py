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
)
logger = logging.getLogger(__name__)

class StreamingOpenAIService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self._running = False

        self.client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)

    async def start(self):
        if self._running:
            logger.info("StreamingOpenAIService already running.")
            return
        self._running = True
        self.event_bus.subscribe_async(AIQueryEvent, self.handle_query)
        logger.info("StreamingOpenAIService started and listening.")

    async def stop(self):
        self._running = False
        logger.info("StreamingOpenAIService stopped.")

    async def handle_query(self, event: AIQueryEvent):
        full_prompt = event.input_text.strip() if event.input_text else ""
        if not full_prompt:
            return

        logger.info(f"[StreamingOpenAI] Prompt: {full_prompt}")
        try:
            model_name = self.settings.get_dynamic_model_name()
            await self.stream_response(full_prompt, model_name)
        except Exception as e:
            logger.error(f"[StreamingOpenAI] Error: {e}")

    async def stream_response(self, prompt: str, model_name: str):
        full_response = []
        buffer = ""

        response = await self.client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are Penny, a sarcastic but helpful AI streaming companion. Respond in a clever, expressive way."},
                {"role": "user", "content": prompt},
            ],
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

    def _should_flush(self, buffer: str) -> bool:
        buffer = buffer.strip()
        if not buffer:
            return False
        if any(buffer.endswith(p) for p in [".", "!", "?"]):
            return True
        if len(buffer) > 20 and any(buffer.endswith(p) for p in [",", ";", ":"]):
            return True
        return False
