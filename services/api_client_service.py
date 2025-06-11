# penny_v2/services/api_client_service.py
import logging
import time
from collections import defaultdict

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    AppShutdownEvent,
    AIQueryEvent
)

logger = logging.getLogger(__name__)

class APIClientService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self._running = False

        # Cooldown tracking
        self._cooldowns = defaultdict(lambda: 0.0)
        self._cooldown_seconds = 3.0  # Configurable per-user cooldown

    async def start(self):
        if self._running:
            logger.info("APIClientService already running.")
            return
        logger.info("APIClientService starting (OpenAI passthrough mode)...")
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
        self._running = True
        logger.info("APIClientService started.")

    async def stop(self):
        self._running = False
        logger.info("APIClientService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent):
        await self.stop()

    async def get_ai_core_response_text(self, prompt: str, instruction: str = "", user_id: str = "global"):
        """ Replaces /respond — emits AIQueryEvent directly. """
        if not self._running:
            return None
        if not self._check_and_update_cooldown(user_id):
            logger.info(f"[Cooldown] Core response blocked for {user_id}")
            return None
        await self._emit_ai_query(
            instruction=instruction or "Respond to the following:",
            input_text=prompt,
            source="mournian",
            include_vision_context=True
        )
        return None

    async def get_api_chat_response_text(self, username: str, message_text: str, context: dict = None):
        """ Replaces /respond_chat — emits AIQueryEvent with Twitch flair. """
        if not self._running:
            return None
        if not self._check_and_update_cooldown(username):
            logger.info(f"[Cooldown] Chat response blocked for {username}")
            return None
        prompt = f"{username} says: {message_text}"
        await self._emit_ai_query(
            instruction="Respond in Penny's Twitch voice to this message.",
            input_text=prompt,
            source="twitch"
        )
        return None

    async def get_api_shout_out_text(self, username: str):
        """ Replaces /shout_out — emits AIQueryEvent for a shout-out. """
        if not self._running:
            return None
        if not self._check_and_update_cooldown(username):
            logger.info(f"[Cooldown] Shout-out blocked for {username}")
            return None
        prompt = f"Give a flashy shout-out to Twitch streamer {username}."
        await self._emit_ai_query(
            instruction="Twitch shout-out style, over-the-top but witty.",
            input_text=prompt,
            source="twitch"
        )
        return None

    async def get_api_event_reaction_text(self, event_type: str, username: str = None, details: dict = None):
        """ Replaces /react_event — emits AIQueryEvent with context. """
        if not self._running:
            return None
        details = details or {}

        # Normalize Twitch event_type
        event_type = event_type.replace("channel.", "").replace("subscription.", "")

        key = f"event:{event_type}:{username or 'anon'}"
        if not self._check_and_update_cooldown(key):
            logger.info(f"[Cooldown] Event reaction blocked for {key}")
            return None

        if event_type == "sub":
            instruction = "Thank the user for subscribing in Penny's sarcastic Twitch streamer voice."
            prompt = f"{username} just subscribed."
        elif event_type == "gift":
            count = details.get("count", 1)
            instruction = "React with chaotic excitement to a gift bomb in chat."
            prompt = f"{username} gifted {count} subs!"
        elif event_type == "message":
            months = details.get("cumulative_months", "several")
            instruction = "Celebrate the user resubscribing with sarcastic gratitude."
            prompt = f"{username} just resubscribed for {months} months!"
        elif event_type == "raid":
            count = details.get("viewer_count", 0)
            instruction = "Welcome the raiders in dramatic, Penny-style fashion."
            prompt = f"{username} is raiding with {count} viewers!"
        elif event_type == "follow":
            instruction = "React in a quirky and clever way to someone following the Twitch channel."
            prompt = f"{username} just followed the channel."
        else:
            instruction = f"React to Twitch event type: {event_type}"
            prompt = f"Event triggered by {username or 'Unknown'}: {details}"

        await self._emit_ai_query(instruction=instruction, input_text=prompt, source="twitch")


    async def _emit_ai_query(self, instruction: str, input_text: str, source: str = None, include_vision_context: bool = False):
        logger.debug(f"[APIClientService -> AI] Instruction: {instruction}\nInput: {input_text}")
        logger.info(f"[Emit AIQueryEvent] Source={source}, Vision={'Yes' if include_vision_context else 'No'}")
        await self.event_bus.publish(AIQueryEvent(
            instruction=instruction,
            input_text=input_text,
            source=source,
            include_vision_context=include_vision_context
        ))

    def _check_and_update_cooldown(self, key: str) -> bool:
        now = time.monotonic()
        if now < self._cooldowns[key]:
            return False
        self._cooldowns[key] = now + self._cooldown_seconds
        return True
