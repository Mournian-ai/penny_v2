# penny_v2/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List
import json
import os

def _load_vtuber_override(field_name, default):
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("vtuber", {}).get(field_name, default)
    except Exception:
        return default
class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    LOG_LEVEL: str = "INFO"

    # Twitch Settings
    TWITCH_NICKNAME: str
    TWITCH_CHAT_TOKEN: str
    TWITCH_CHAT_REFRESH_TOKEN: str
    TWITCH_CHANNEL: str
    TWITCH_BROADCASTER_USER_ID: str
    TWITCH_CLIENT_ID: str
    TWITCH_APP_ACCESS_TOKEN: str
    TWITCH_APP_REFRESH_TOKEN: Optional[str] = None
    TWITCH_CLIENT_SECRET: str 
    TWITCH_CONDUIT_ID: Optional[str] = None
    
    # FastAPI Backends
    FASTAPI_URL_MAIN: str
    FASTAPI_URL_TRANSCRIBE: str
    WEBSOCKET_TRANSCRIBE_URL: str
    OPENAI_API_KEY: str
    
    # TTS Settings
    PIPER_PATH: str
    PIPER_VOICE_MODEL: str
    TTS_OUTPUT_DEVICE_NAME: str  # Substring of the VB Cable input name
    GOOGLE_API_KEY: str
    GOOGLE_CSE_ID: str
    # Audio Settings
    INPUT_DEVICE_NAME_SUBSTRING: Optional[str] = None  # For mic input if different from transcription VAC
    PTT_KEY: str = "caps lock"
    VTUBER_AUDIO_DEVICE_NAME: str
    TTS_TARGET_SAMPLE_RATE: int = 48000  # Default sample rate for TTS output
    # VTuber - Define paths to your assets
    VTUBER_ASSETS_PATH: str = "./vtuber/assets/"
    VTUBER_BASE_IMAGE: str = "body.png"
    VTUBER_EYE_OPEN_LEFT: str = "eye_open_left.png"
    VTUBER_EYE_OPEN_RIGHT: str = "eye_open_right.png"
    VTUBER_EYE_CLOSED_LEFT: str = "eye_closed_left.png"
    VTUBER_EYE_CLOSED_RIGHT: str = "eye_closed_right.png"
    VTUBER_MOUTH_SHAPES: List[str] = [
        "mouth_closed.png",
        "mouth_slightly_open.png",
        "mouth_open.png",
        "mouth_wide_open.png"
    ]
    VTUBER_LEFT_EYE_POS: tuple[int, int] = _load_vtuber_override("left_eye", (200, 245))
    VTUBER_RIGHT_EYE_POS: tuple[int, int] = _load_vtuber_override("right_eye", (265, 245))
    VTUBER_MOUTH_POS: tuple[int, int] = _load_vtuber_override("mouth", (245, 306))
    VTUBER_IMAGE_SCALE_FACTOR: float = 0.5  # Base image
    VTUBER_EYE_SCALE_FACTOR: float = _load_vtuber_override("eye_scale", 1.0)
    VTUBER_MOUTH_SCALE_FACTOR: float = _load_vtuber_override("mouth_scale", 1.0)

    def get_dynamic_model_name(self) -> str:
        """Load OpenAI model name from settings.json (used for hot-swapping models)."""
        try:
            with open("settings.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("openai_model", "gpt-4o")  # fallback default
        except Exception as e:
            print(f"[Config] Error loading model from settings.json: {e}")
            return "gpt-4o"

# Load configuration
try:
    settings = AppConfig()
except Exception as e:
    print(f"Error loading configuration: {e}")
    print("Please ensure a '.env' file exists and is correctly formatted.")
    exit(1)

if __name__ == "__main__":
    # Test loading config
    print("Configuration loaded successfully:")
    print(f"Twitch Channel: {settings.TWITCH_CHANNEL}")
    print(f"Piper Path: {settings.PIPER_PATH}")
    print(f"VTuber Mouth Shapes: {settings.VTUBER_MOUTH_SHAPES}")
    print(f"Log Level: {settings.LOG_LEVEL}")
    print(f"OpenAI Model (dynamic): {settings.get_dynamic_model_name()}")
