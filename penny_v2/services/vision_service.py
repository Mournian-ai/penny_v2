import io
import json
import logging
from PIL import Image
import mss
import openai
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import UILogEvent
from penny_v2.config import settings

logger = logging.getLogger(__name__)

class VisionService:
    def __init__(self, event_bus: EventBus, profile_path: str):
        self.event_bus = event_bus
        self.profile_path = profile_path
        self.region = self.load_region()
        openai.api_key = settings.OPENAI_API_KEY

    def load_region(self):
        try:
            with open(self.profile_path, "r") as f:
                data = json.load(f)
            # Assuming only one region for now
            return data["regions"][0]
        except Exception as e:
            logger.error(f"[VisionService] Failed to load vision profile: {e}")
            return None

    def capture_region(self):
        if not self.region:
            return None
        with mss.mss() as sct:
            monitor = {
                "top": self.region["top"],
                "left": self.region["left"],
                "width": self.region["width"],
                "height": self.region["height"]
            }
            sct_img = sct.grab(monitor)
            return Image.frombytes("RGB", sct_img.size, sct_img.rgb)

    async def analyze_with_openai(self, image: Image.Image, prompt="What is happening in this scene?"):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        try:
            result = await openai.ChatCompletion.acreate(
                model="gpt-4o",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                files=[{"file": buf, "name": "scene.png"}],
                max_tokens=300
            )
            content = result.choices[0].message.content
            logger.info(f"[VisionService] GPT-4o Vision response: {content}")
            self.event_bus.emit(UILogEvent("vision", content))
        except Exception as e:
            logger.error(f"[VisionService] OpenAI vision error: {e}")
            self.event_bus.emit(UILogEvent("vision", f"[Error] {e}"))

    async def run_once(self):
        logger.info("[VisionService] Capturing screen and analyzing with GPT-4o")
        image = self.capture_region()
        if image:
            await self.analyze_with_openai(image)
