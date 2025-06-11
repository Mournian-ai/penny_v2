import io
import logging
import asyncio
import base64
from PIL import Image
import mss
from openai import AsyncOpenAI

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import UILogEvent, SpeakRequestEvent, VisionSummaryEvent
logger = logging.getLogger(__name__)

class VisionService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self.running = False
        self.client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)
        self._task = None

    async def start(self):
        logger.info("[_start] VisionService initialized, vision loop not running by default.")

    def is_running(self):
        return self._task is not None and not self._task.done()

    def stop(self):
        logger.info("[stop] VisionService stopping loop.")
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def toggle(self):
        if self.is_running():
            self.stop()
        else:
            self._task = asyncio.create_task(self.run_loop())

    def capture_screen(self, x=0, y=0, width=1920, height=1080) -> Image.Image:
        with mss.mss() as sct:
            monitor = {"top": y, "left": x, "width": width, "height": height}
            sct_img = sct.grab(monitor)
            return Image.frombytes("RGB", sct_img.size, sct_img.rgb)

    async def analyze_image(self, image: Image.Image, prompt="Here is the latest frame from the user's screen."):
        # Convert image to base64
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode()

        system_prompt = (
            "You are Penny, a sarcastic, sharp-witted AI Twitch companion with dry humor. "
            "You are observing the user's game or desktop screen in real time. "
            "If there is something interesting, funny, dramatic, or worth mocking, make a short sarcastic or insightful comment about it. "
            "Be blunt, witty, and humanlike. Sound like you're talking to a live audience. "
            "Ensure your response is concise, engaging, and fits the context of the scene.  Make sure its funny you can flame Mournian, if all you are going to do is talk about the game, return [No Comment]"
            "If nothing interesting is happening, or the scene is boring, repetitive, or meaningless, just reply with: [NO COMMENT]. "
            "Do not explain the image or say what you see — just react like Penny would on Twitch."
        )

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                        ]
                    }
                ],
                max_tokens=300
            )

            caption = response.choices[0].message.content.strip()
            logger.info(f"[VisionService] GPT-4o Vision response: {caption}")
            await self.event_bus.publish(UILogEvent("vision", caption))
            await self.event_bus.publish(VisionSummaryEvent(summary=caption))
            if caption != "[NO COMMENT]":
                await self.event_bus.publish(SpeakRequestEvent(text=caption))

        except Exception as e:
            logger.error(f"[analyze_image] [VisionService] Error analyzing image: {e}")
            await self.event_bus.publish(UILogEvent("vision", f"[Error] {e}"))

    async def run_loop(self, interval=30):
        self.running = True
        logger.info("[run_loop] VisionService run loop started, capturing every %s seconds.", interval)
        while self.running:
            image = self.capture_screen()
            await self.analyze_image(image)
            await asyncio.sleep(interval)
