# penny_v2/core/events.py
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict

@dataclass
class BaseEvent:
    pass

@dataclass
class AudioRecordedEvent(BaseEvent):
    audio_path: Optional[str] = None
    audio_bytes: Optional[bytes] = None
    filename: Optional[str] = "recording.wav"

@dataclass
class UILogEvent(BaseEvent):
    message: str
    level: str = "INFO"

@dataclass
class TranscriptionAvailableEvent(BaseEvent):
    text: str
    is_final: bool = True
    audio_path: Optional[str] = None  
    error: Optional[str] = None

@dataclass
class AIQueryEvent:
    def __init__(self, input_text: str, instruction: Optional[str] = None, include_vision_context: bool = False, source: Optional[str] = None):
        self.input_text = input_text
        self.instruction = instruction
        self.include_vision_context = include_vision_context
        self.source = source
        
@dataclass
class AIResponseEvent(BaseEvent):
    text_to_speak: str
    original_query: Optional[str] = None

@dataclass
class SpeakRequestEvent(BaseEvent):
    text: str
    collab_mode: bool = False

@dataclass
class TTSSpeakingStateEvent(BaseEvent):
    is_speaking: bool

@dataclass
class TwitchMessageEvent(BaseEvent):
    username: str
    message: str
    tags: dict = field(default_factory=dict)

@dataclass
class TwitchUserEvent(BaseEvent): # For subs, raids etc.
    event_type: str # e.g., "sub", "resub", "gift", "raid"
    username: str
    details: dict = field(default_factory=dict) # e.g., months, viewer_count

@dataclass
class AudioRMSVolumeEvent(BaseEvent): # For VTuber mouth movement
    rms_volume: float # Normalized 0-1 or raw RMS

@dataclass
class PTTRecordingStateEvent(BaseEvent):
    is_recording: bool

@dataclass
class AppShutdownEvent(BaseEvent):
    pass

class VisionSummaryEvent:
    def __init__(self, summary: str):
        self.summary = summary

@dataclass
class SearchRequestEvent(BaseEvent):
    """Event to request a web search."""
    query: str
    source: str = "unknown"  # E.g., "twitch_command", "llm_request"
    num_results: int = 3
    original_user: Optional[str] = None
    original_context: Optional[str] = None

@dataclass
class SearchResultEvent(BaseEvent):
    """Event carrying the results of a web search."""
    query: str
    results: List[Dict]
    source: str
    original_user: Optional[str] = None
    original_context: Optional[str] = None
    error: Optional[str] = None

@dataclass
class ExternalTranscriptEvent(BaseEvent):
    text: str
    speaker: str
