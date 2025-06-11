import asyncio
import logging
import os
import uuid
from collections import deque
from typing import Optional, List

import numpy as np
import sounddevice as sd
import webrtcvad
import soundfile as sf

from asyncio import run_coroutine_threadsafe
from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    AudioRecordedEvent, AudioRMSVolumeEvent, UILogEvent
)
from penny_v2.utils.helpers import find_audio_device_id

logger = logging.getLogger(__name__)

# Constants
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'
FRAME_DURATION_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * (FRAME_DURATION_MS / 1000))
BYTE_WIDTH = 2  # 16-bit PCM
MIN_AUDIO_BYTES = 32000
PRE_SPEECH_FRAMES = int(300 / FRAME_DURATION_MS)
POST_SPEECH_FRAMES = int(500 / FRAME_DURATION_MS)
TEMP_DIR = "temp_audio_processing"


class ListeningService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self.device_id = find_audio_device_id(settings.INPUT_DEVICE_NAME_SUBSTRING, 'input')
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        self._stream: Optional[sd.InputStream] = None
        self._vad = webrtcvad.Vad(3) # <--- Maybe start with 1 (less aggressive)

        # --- STORE NUMPY ARRAYS ---
        self._buffer: deque[np.ndarray] = deque(maxlen=PRE_SPEECH_FRAMES)
        self._recording: list[np.ndarray] = []
        # ------------------------

        self._in_speech = False
        self._silence_frames = 0
        self._is_listening = False

    async def start_listening(self):
        logger.info("ListeningService started on device %s", self.device_id)
        self.loop = asyncio.get_running_loop()
        self._is_listening = True

        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Sounddevice status: {status}")

            if not self._is_listening:
                return

            rms = np.sqrt(np.mean(indata.astype(np.float32) ** 2))
            logger.debug(f"Callback received {frames} frames. RMS: {rms:.4f}")

            # --- PROCESS NUMPY ARRAY ---
            self._process_frame(indata.copy()) # Pass a copy of the ndarray
            # -------------------------

            run_coroutine_threadsafe(
                self.event_bus.publish(AudioRMSVolumeEvent(rms_volume=rms)),
                self.loop
            )

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                device=self.device_id,
                blocksize=FRAME_SIZE,
                callback=callback
            )
            self._stream.start()
        except Exception as e:
            logger.error(f"Failed to start ListeningService stream: {e}", exc_info=True)
            await self.event_bus.publish(UILogEvent("Error starting passive mic stream", level="ERROR"))

    async def stop_listening(self):
        self._is_listening = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("ListeningService stopped.")
        # Finalize any pending recording when explicitly stopped
        await self._finalize_segment()

    # --- MODIFIED _process_frame ---
    def _process_frame(self, frame_np: np.ndarray):
        frame_bytes = frame_np.tobytes()
        frame_size_bytes = FRAME_SIZE * BYTE_WIDTH

        # Ensure frame size is correct for VAD
        if len(frame_bytes) != frame_size_bytes:
            logger.warning(f"Unexpected frame size: {len(frame_bytes)}, expected {frame_size_bytes}. Skipping.")
            return

        try:
            is_speech = self._vad.is_speech(frame_bytes, sample_rate=SAMPLE_RATE)
        except Exception as e:
            logger.error(f"Error in VAD processing: {e}")
            is_speech = False

        if not self._in_speech:
            self._buffer.append(frame_np) # Append ndarray

        if is_speech:
            if not self._in_speech:
                logger.info("VAD: speech started")
                self._in_speech = True
                self._recording = list(self.  _buffer)
                self._buffer.clear()
            self._recording.append(frame_np) # Append ndarray
            self._silence_frames = 0
        elif self._in_speech:
            self._recording.append(frame_np) # Append ndarray
            self._silence_frames += 1
            if self._silence_frames >= POST_SPEECH_FRAMES:
                logger.info("VAD: speech ended")
                current_recording = list(self._recording)
                asyncio.run_coroutine_threadsafe(
                    self._finalize_segment(current_recording), self.loop
                )
                self._in_speech = False
                self._recording.clear()
                self._buffer.clear()
    # --- END MODIFIED ---

    # --- MODIFIED _finalize_segment ---
    async def _finalize_segment(self, recording_data: Optional[List[np.ndarray]] = None):
        data_to_process = recording_data if recording_data is not None else self._recording

        if not data_to_process:
            logger.warning("VAD: No audio data recorded.")
            return

        try:
            # --- USE CONCATENATE ---
            audio_np = np.concatenate(data_to_process)
            audio_bytes = audio_np.tobytes() # Get bytes after concatenating
            # ---------------------

            if len(audio_bytes) < MIN_AUDIO_BYTES:
                logger.warning("VAD: Skipping short segment (%d bytes).", len(audio_bytes))
                return

            os.makedirs(TEMP_DIR, exist_ok=True)
            filename = f"vad_{uuid.uuid4().hex}.wav"
            path = os.path.join(TEMP_DIR, filename)

            if audio_np.size > 0:
                logger.debug(f"Audio NP array shape: {audio_np.shape}, min: {np.min(audio_np)}, max: {np.max(audio_np)}")
            else:
                logger.error("Audio NP array is EMPTY before writing!")
                return # Don't proceed if empty

            # Write using soundfile with the NumPy array
            sf.write(path, audio_np, SAMPLE_RATE, subtype='PCM_16')

            logger.info(f"Saved VAD segment: {path} ({len(audio_bytes)} bytes)")

            await self.event_bus.publish(AudioRecordedEvent(
                audio_path=path,
                filename=filename,
                audio_bytes=audio_bytes
            ))

        except Exception as e:
            logger.error(f"Failed to finalize/save audio: {e}", exc_info=True)
            await self.event_bus.publish(UILogEvent(message=f"Error saving audio: {e}", level="ERROR"))
    # --- END MODIFIED ---

    def is_listening(self) -> bool:
        return self._is_listening