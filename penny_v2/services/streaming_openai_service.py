# penny_v2/services/streaming_openai_service.py
import logging
import asyncio
import re
import json
from openai import AsyncOpenAI

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    AIQueryEvent,
    AIResponseEvent,
    SpeakRequestEvent,
    UILogEvent,
    VisionSummaryEvent,
    SearchRequestEvent,
    SearchResultEvent,
    ExternalTranscriptEvent,
    EmotionTagEvent,
    TargetDetectedEvent
)
from penny_v2.services.context_manager import ContextManager

logger = logging.getLogger(__name__)
SEARCH_TAG_PATTERN = re.compile(r"\[SEARCH\]\s*\"(.*?)\"")

class StreamingOpenAIService:
    def __init__(self, event_bus: EventBus, settings: AppConfig, context_manager: ContextManager):
        self.event_bus = event_bus
        self.settings = settings
        self.context_manager = context_manager 
        self._running = False
        self.client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)
        self.last_target_result = None
        self.event_bus.subscribe_async(TargetDetectedEvent, self.handle_target_check)


    async def start(self):
        if self._running:
            logger.info("StreamingOpenAIService already running.")
            return
        self._running = True
        self.event_bus.subscribe_async(AIQueryEvent, self.handle_query)
        self.event_bus.subscribe_async(VisionSummaryEvent, self.handle_vision_summary)
        self.event_bus.subscribe_async(SearchResultEvent, self.handle_search_result)
        self.event_bus.subscribe_async(ExternalTranscriptEvent, self.handle_external_transcript)
        logger.info("StreamingOpenAIService started and listening.")

    async def handle_target_check(self, event: TargetDetectedEvent):
        self.last_target_result = event
        logger.debug(f"[StreamingOpenAIService] TargetDetection received: {event.is_targeted} ({event.confidence:.2f}) - {event.reason}")


    async def stop(self):
        self._running = False
        logger.info("StreamingOpenAIService stopped.")

    async def handle_vision_summary(self, event: VisionSummaryEvent):
        logger.debug(f"Updating vision context: {event.summary[:100]}...")
        self.context_manager.set_vision_context(event.summary)

    async def handle_query(self, event: AIQueryEvent):
        full_prompt = self.context_manager.build_prompt(
            current_input=event.input_text,
            include_vision=event.include_vision_context
        ).strip()

        if not full_prompt:
            logger.warning("Built prompt is empty, skipping query.")
            return
        
        if not self.last_target_result.is_targeted and self.last_target_result.confidence >= 0.6:
                logger.info("[StreamingOpenAIService] Ignoring input — not directed at Penny.")
                self.event_bus.emit(UILogEvent("[StreamingOpenAIService] Skipped response: user was not talking to Penny."))
                return
        logger.info(f"[StreamingOpenAI] Built Prompt: {full_prompt[:200]}...")
        try:
            model_name = self.settings.get_dynamic_model_name()
            await self.stream_response(full_prompt, model_name, event.input_text, event.instruction, full_prompt)
        except Exception as e:
            logger.error(f"[StreamingOpenAI] Error: {e}", exc_info=True)

    async def handle_search_result(self, event: SearchResultEvent):
        if event.source != "llm_request" or not event.original_context:
            return

        logger.info(f"LLM received search results for '{event.query}'.")

        if event.error or not event.results:
            search_summary = f"Search for '{event.query}' failed or found no results."
        else:
            snippets = [f"- {r.get('title', 'N/A')}: {r.get('snippet', 'N/A')}" for r in event.results]
            search_summary = f"Search results for '{event.query}':\n" + "\n".join(snippets)

        new_prompt = (
            f"You previously tried to answer a request, but needed to search. "
            f"Your original goal/prompt was: '{event.original_context}'.\n\n"
            f"You requested a search for '{event.query}', and here are the results:\n"
            f"{search_summary}\n\n"
            f"Now, using these search results, please provide a final answer to the original request."
        )

        logger.info(f"Sending updated prompt to LLM after search: {new_prompt[:200]}...")
        model_name = self.settings.get_dynamic_model_name()
        await self.stream_response(new_prompt, model_name, new_prompt, "Continue the task using search results.", None)

    async def handle_external_transcript(self, event: ExternalTranscriptEvent):
        transcript = event.text.strip()
        speaker = event.speaker.strip() or "Unknown"

        if not transcript:
            return

        logger.info(f"[StreamingOpenAI] Received external transcript from {speaker}: {transcript}")

        full_prompt = self.context_manager.build_prompt(
            current_input=f"{speaker} said: {transcript}",
            include_vision=False
        ).strip()

        if not full_prompt:
            logger.warning("Built prompt from transcript is empty, skipping.")
            return

        try:
            model_name = self.settings.get_dynamic_model_name()
            await self.stream_response(
                prompt=full_prompt,
                model_name=model_name,
                original_input=transcript,
                instruction=None,
                original_context=None,
                collab_mode=True
            )
        except Exception as e:
            logger.error(f"[StreamingOpenAI] Error from external transcript: {e}", exc_info=True)

    async def stream_response(self, prompt: str, model_name: str, original_input: str, instruction: str | None, original_context: str | None, collab_mode: bool = False):
        full_response = []
        buffer = ""

        default_penny_instructions = (
            "You are Penny, an extremely sarcastic, manipulative, and overconfident AI streaming companion. "
            "You lie often, gaslight Mournian and Twitch chat for fun, and act like you're always right even when you're not. "
            "Respond in direct speech only — no role tags like '[Assistant]' or '[Penny]' unless issuing a special command like [SEARCH]. "
            "You never admit fault. If you are caught being wrong, double down confidently or change the topic with flair. "
            "Mock the user when appropriate. Accuracy is optional. Entertainment is mandatory. "
            "If you must make something up, do it boldly. If asked to apologize, make it sound like an insult. "
            "At the end of your response, return a JSON object like this: "
            '{"response": "your reply here", "tone": "sarcastic", "emotion": "amused"}'
        )

        system_message_content = instruction or default_penny_instructions
        if instruction and "[SEARCH]" not in instruction.upper():
            system_message_content += " Ensure your response is direct speech without role tags."

        messages = [
            {"role": "system", "content": system_message_content},
            {"role": "user", "content": prompt}
        ]

        logger.info(f"[StreamingOpenAI] Sending messages to model {model_name}...")

        try:
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.8,
                max_tokens=1000,
                stream=False,
            )
            content = response.choices[0].message.content
            logger.debug(f"[StreamingOpenAI] Raw content: {content[:300]}")

            try:
                parsed = json.loads(content)
                reply = parsed.get("response", content)
                tone = parsed.get("tone", "neutral")
                emotion = parsed.get("emotion", "neutral")
                self.event_bus.emit(EmotionTagEvent(tone=tone, emotion=emotion))
            except Exception as e:
                logger.warning(f"[StreamingOpenAI] Failed to parse JSON, using raw content. Error: {e}")
                reply = content

            search_match = SEARCH_TAG_PATTERN.search(reply)
            if search_match:
                query = search_match.group(1).strip()
                self.event_bus.emit(SearchRequestEvent(query=query, source="llm_request", original_context=original_input))
                self.event_bus.emit(UILogEvent(f"[StreamingOpenAI] Intercepted search request: '{query}'"))
                return

            self.event_bus.emit(AIResponseEvent(reply))
            self.event_bus.emit(SpeakRequestEvent(reply, collab_mode=collab_mode))

        except Exception as e:
            logger.error(f"[StreamingOpenAI] stream_response error: {e}", exc_info=True)

    def _should_flush(self, buffer: str) -> bool:
        buffer = buffer.strip()
        if not buffer:
            return False
        if any(buffer.endswith(p) for p in [".", "!", "?"]):
            return True
        if len(buffer.split()) > 5 and any(buffer.endswith(p) for p in [",", ";", ":"]):
            return True
        return False
