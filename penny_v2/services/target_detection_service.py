# penny_v2/services/target_detection_service.py
import asyncio
import logging
from openai import AsyncOpenAI
from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    ExternalTranscriptEvent,
    TargetDetectedEvent,
    UILogEvent,
)

logger = logging.getLogger(__name__)

class TargetDetectionService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self.client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)

    async def start(self):
        self.event_bus.subscribe_async(ExternalTranscriptEvent, self.handle_transcript)
        logger.info("[TargetDetectionService] Started and subscribed to ExternalTranscriptEvent.")
        self.event_bus.emit(UILogEvent("[TargetDetectionService] Ready to evaluate targets."))

    async def handle_transcript(self, event: ExternalTranscriptEvent):
        transcript = event.text.strip()
        if not transcript:
            return

        speaker = event.speaker or "Unknown"
        try:
            logger.debug(f"[TargetDetectionService] Checking if '{transcript}' from {speaker} is directed at Penny.")
            is_targeted, confidence, reason = await self.evaluate_target(transcript)
            self.event_bus.emit(TargetDetectedEvent(
                speaker=speaker,
                text=transcript,
                is_targeted=is_targeted,
                confidence=confidence,
                reason=reason
            ))
            logger.debug(f"[TargetDetectionService] Target result: {is_targeted} ({confidence:.2f}) — {reason}")
            self.event_bus.emit(UILogEvent(
                f"[TargetDetectionService] Targeted={is_targeted} (Confidence={confidence:.2f}) — {reason}"
            ))
        except Exception as e:
            logger.exception(f"[TargetDetectionService] Error handling transcript: {e}")
            self.event_bus.emit(UILogEvent(
                f"[TargetDetectionService] ERROR during evaluation: {e}"
            ))

    async def evaluate_target(self, message: str) -> tuple[bool, float, str]:
        # Fast keyword match shortcut
        lowered = message.lower()
        if any(keyword in lowered for keyword in ("penny", "you think", "what do you", "can you", "are you", "she", "your opinion")):
            return True, 0.85, "Matched direct keyword(s)."

        # GPT-3.5 fallback
        prompt = (
            "You are helping detect whether a message is directed at an AI assistant named Penny. "
            "Respond in valid JSON only using the format: "
            '{ "is_targeted": true/false, "confidence": float, "reason": "string" }\n\n'
            f"Message: \"{message}\""
        )

        try:
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=100,
            )
            content = response.choices[0].message.content
            logger.debug(f"[TargetDetectionService] Raw GPT-3.5 response: {content}")

            import json
            result = json.loads(content)
            return (
                bool(result.get("is_targeted", False)),
                float(result.get("confidence", 0.0)),
                result.get("reason", "No reason provided.")
            )
        except Exception as e:
            logger.error(f"[TargetDetectionService] GPT-3.5 API call failed, falling back: {e}")
            return False, 0.0, f"Fallback: could not determine target (API error: {str(e)})"
