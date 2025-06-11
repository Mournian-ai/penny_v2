import asyncio
import logging
import os
import wave
from typing import Optional

import numpy as np
import pyaudio
import sounddevice as sd
import websockets

from asyncio import run_coroutine_threadsafe
from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    AudioRMSVolumeEvent, PTTRecordingStateEvent,
    TranscriptionAvailableEvent, UILogEvent
)
from penny_v2.utils.helpers import find_audio_device_id
from penny_v2.core.events import AudioRecordedEvent


logger = logging.getLogger(__name__)

# Constants
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 100
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)
PYAUDIO_FORMAT = pyaudio.paInt16
PTT_SAMPLERATE = 16000
PTT_CHANNELS = 1
PTT_DTYPE = 'int16'
VTUBER_VOLUME_SCALE = 1000
VTUBER_VOLUME_CAP = 100.0


class AudioService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        from penny_v2.services.ptt_controller import PTTController
        
        self.event_bus = event_bus
        self.settings = settings
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.ptt_enabled = False

        self._input_device_id_mic = find_audio_device_id(settings.INPUT_DEVICE_NAME_SUBSTRING, 'input')
        self._input_device_id_vac = find_audio_device_id(settings.VTUBER_AUDIO_DEVICE_NAME, 'input')

        self._pyaudio_instance: Optional[pyaudio.PyAudio] = None
        self._pyaudio_stream: Optional[pyaudio.Stream] = None
        self._websocket_connection: Optional[websockets.WebSocketClientProtocol] = None

        self._streaming_task: Optional[asyncio.Task] = None
        self._ptt_task: Optional[asyncio.Task] = None
        self._ptt_recorded_frames = []
        self._ptt_stream: Optional[sd.InputStream] = None
        self._is_ptt_recording = False

        self._vtuber_stream: Optional[sd.InputStream] = None
        self._vtuber_task: Optional[asyncio.Task] = None
        self._ptt_controller = PTTController(event_bus, self, settings)

    async def start(self):
        logger.info("Starting AudioService")
        self.loop = asyncio.get_running_loop()

    async def start_ptt_recording(self):
        def callback(indata, frames, time, status):
            if self._is_ptt_recording:
                self._ptt_recorded_frames.append(indata.copy())
        try:
            self._ptt_stream = sd.InputStream(
                samplerate=PTT_SAMPLERATE,
                channels=PTT_CHANNELS,
                dtype=PTT_DTYPE,
                device=self._input_device_id_mic,
                callback=callback
            )
            self._ptt_stream.start()
            self._is_ptt_recording = True
            await self.event_bus.publish(PTTRecordingStateEvent(is_recording=True))
        except Exception as e:
            logger.error(f"PTT stream error: {e}")
            await self.event_bus.publish(UILogEvent("PTT error", level="ERROR"))

    async def stop_ptt_recording(self):
        if not self._is_ptt_recording:
            return
        self._is_ptt_recording = False

        if self._ptt_stream:
            self._ptt_stream.stop()
            self._ptt_stream.close()
            self._ptt_stream = None

        if not self._ptt_recorded_frames:
            await self.event_bus.publish(UILogEvent("No audio was recorded during PTT.", level="WARNING"))
            return

        data = np.concatenate(self._ptt_recorded_frames)
        self._ptt_recorded_frames.clear()

        temp_path = "temp_ptt_recording.wav"
        with wave.open(temp_path, 'wb') as wf:
            wf.setnchannels(PTT_CHANNELS)
            wf.setsampwidth(2)  # 16-bit PCM = 2 bytes
            wf.setframerate(PTT_SAMPLERATE)
            wf.writeframes(data.tobytes())

        await self.event_bus.publish(PTTRecordingStateEvent(is_recording=False))
        await self.event_bus.publish(
            AudioRecordedEvent(
                audio_path=temp_path,
                filename=os.path.basename(temp_path)
            )
        )
    async def shutdown(self):
        logger.info("Shutting down AudioService")
        if self._ptt_stream:
            self._ptt_stream.stop()
            self._ptt_stream.close()
            self._ptt_stream = None
        self._is_ptt_recording = False

    def set_ptt_enabled(self, enabled: bool):
        self.ptt_enabled = enabled
        self._ptt_controller.set_enabled(enabled)

    def is_ptt_enabled(self) -> bool:
        return self.ptt_enabled
    
    def disable_ptt(self):
        self.set_ptt_enabled(False)
