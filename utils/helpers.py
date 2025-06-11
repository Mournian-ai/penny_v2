# penny_v2/utils/helpers.py
import regex # Using 'regex' for more complete Unicode emoji support
import logging
import sounddevice as sd
from typing import Optional, List
import os

logger = logging.getLogger(__name__)

def remove_emojis(text: str) -> str:
    if not text:
        return ""
    emoji_pattern = regex.compile(
        r'[\p{Emoji_Presentation}\p{Emoji}\p{Extended_Pictographic}]',
        flags=regex.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def find_audio_device_id(name_substring: Optional[str], kind: str = 'input') -> Optional[int]:
    """Finds an audio device by a substring in its name, or returns the default device if no name is provided."""
    try:
        if not name_substring:
            default_input, default_output = sd.default.device
            device_id = default_input if kind == 'input' else default_output
            device_type = 'input' if kind == 'input' else 'output'
            logger.info(f"Using default {device_type} device ID: {device_id}")
            return device_id

        devices: List[dict] = sd.query_devices()
        for i, dev in enumerate(devices):
            name = dev.get('name', '').lower()
            if name_substring.lower() in name:
                if kind == 'input' and dev.get('max_input_channels', 0) > 0:
                    logger.info(f"Found input device '{dev['name']}' with ID {i} for substring '{name_substring}'")
                    return i
                elif kind == 'output' and dev.get('max_output_channels', 0) > 0:
                    logger.info(f"Found output device '{dev['name']}' with ID {i} for substring '{name_substring}'")
                    return i
    except Exception as e:
        logger.error(f"Error querying audio devices: {e}", exc_info=True)

    logger.warning(f"{kind.capitalize()} device containing '{name_substring}' not found.")
    return None

def get_asset_path(filename: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))  # helpers.py dir
    asset_dir = os.path.join(base_dir, "..", "vtuber", "assets")
    full_path = os.path.normpath(os.path.join(asset_dir, filename))
    return full_path

def should_respond_to_penny_mention(message: str) -> bool:
    lowered = message.lower()
    return (
        "penny" in lowered and (
            "?" in lowered or
            "can" in lowered or
            "do you" in lowered or
            "think" in lowered or
            "hey" in lowered or
            lowered.startswith("penny")
        )
    )
