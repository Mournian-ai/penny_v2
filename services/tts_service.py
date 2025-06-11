# penny_v2/services/tts_service.py
import asyncio
import logging
import os
import tempfile
import wave # For saving intermediate WAV for pydub if needed, though Piper can output WAV

import numpy as np
import sounddevice as sd
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from typing import Optional
from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    EmotionTagEvent,
    SpeakRequestEvent, TTSSpeakingStateEvent, UILogEvent, AppShutdownEvent, AudioRMSVolumeEvent
)
from penny_v2.utils.helpers import remove_emojis, find_audio_device_id

logger = logging.getLogger(__name__)

# Default audio processing parameters (can be adjusted or made configurable)
DEFAULT_TTS_SPEED = 1.0  # Normal speed
DEFAULT_TTS_PITCH_SEMITONES = 0.0 # No pitch change
DEFAULT_TTS_VOLUME_REDUCTION_DB = 0.0 # No volume reduction by default

class TTSService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self.speech_queue = asyncio.Queue(maxsize=20)  # Max number of pending speech requests
        self._is_currently_speaking_lock = asyncio.Lock()
        self._is_currently_speaking = False
        
        self._output_device_id: Optional[int] = None
        self._processing_task: Optional[asyncio.Task] = None
        self._current_playback_stop_event: Optional[asyncio.Event] = None

        # Configurable parameters from settings or defaults
        self.volume_db_reduction = getattr(settings, 'TTS_INITIAL_VOLUME_REDUCTION_DB', DEFAULT_TTS_VOLUME_REDUCTION_DB)
        self.speech_speed = getattr(settings, 'TTS_SPEECH_SPEED', DEFAULT_TTS_SPEED)
        self.pitch_semitones = getattr(settings, 'TTS_PITCH_SEMITONES', DEFAULT_TTS_PITCH_SEMITONES)
        
        self.is_muted = False # For UI control
        self.collab_mode = False # For UI control, actual implementation for collab depends on requirements

    async def start(self):
        logger.info("TTSService starting...")
        self._output_device_id = find_audio_device_id(self.settings.TTS_OUTPUT_DEVICE_NAME, kind='output')
        
        if self._output_device_id is None:
            error_msg = f"TTS Output device '{self.settings.TTS_OUTPUT_DEVICE_NAME}' not found. TTS will be silent."
            logger.error(error_msg)
            await self.event_bus.publish(UILogEvent(message=error_msg, level="ERROR"))
        else:
            logger.info(f"TTS Output device found: ID {self._output_device_id} ('{self.settings.TTS_OUTPUT_DEVICE_NAME}')")

        self.event_bus.subscribe_async(SpeakRequestEvent, self.handle_speak_request)
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
        
        self._processing_task = asyncio.create_task(self._process_speech_queue())
        logger.info("TTSService started and speech queue processor initiated.")

    async def stop(self):
        logger.info("TTSService stopping...")
        if self._current_playback_stop_event:
            self._current_playback_stop_event.set()

        if self._processing_task and not self._processing_task.done():
            task_name = self._processing_task.get_name() if hasattr(self._processing_task, 'get_name') else "TTSProcessingTask"
            logger.info(f"Cancelling TTS processing task: {task_name}")
            self._processing_task.cancel()
            try:
                logger.info(f"Awaiting TTS processing task ({task_name}) to finish after cancellation...")
                await self._processing_task
                logger.info(f"TTS processing task ({task_name}) finished after cancellation.")
            except asyncio.CancelledError:
                logger.info(f"TTS processing task ({task_name}) was awaited and confirmed cancelled.")
            except Exception as e:
                logger.error(f"Exception while awaiting cancelled TTS processing task ({task_name}): {e}", exc_info=True)
        else:
            logger.info("TTS processing task already done or not initialized.")

        logger.info(f"Clearing TTS speech queue (current size: {self.speech_queue.qsize()})...")
        while not self.speech_queue.empty():
            try:
                item = self.speech_queue.get_nowait()
                self.speech_queue.task_done()
                # logger.debug(f"Removed '{item[:30]}...' from TTS queue during stop.")
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.warning(f"Error clearing item from speech queue during stop: {e}")
                break
        logger.info(f"TTS speech queue cleared (final size: {self.speech_queue.qsize()}).")

        await self._set_speaking_status(False) # Set status after task is confirmed done
        logger.info("TTSService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent):
        await self.stop()

    async def handle_speak_request(self, event: SpeakRequestEvent):
        logger.info(f"[TTSService] SpeakRequestEvent received: '{event.text[:100]}'")
        if not event.text.strip():
            logger.info("SpeakRequestEvent received with empty text, skipping.")
            return

        if self.is_muted:
            logger.info(f"TTS is muted. Dropping speak request: '{event.text[:50]}...'")
            # Optionally publish a UILogEvent that it was dropped due to mute
            # await self.event_bus.publish(UILogEvent(message="TTS Muted: Speech dropped.", level="DEBUG"))
            return

        if self.speech_queue.full():
            try:
                # Drop the oldest item if the queue is full
                dropped_text = self.speech_queue.get_nowait()
                self.speech_queue.task_done() # Mark the dropped task as done
                logger.warning(f"TTS queue full. Dropped oldest speech: '{dropped_text[:50]}...'")
                await self.event_bus.publish(UILogEvent(message=f"TTS Queue Full. Dropped: {dropped_text[:30]}...", level="WARNING"))
            except asyncio.QueueEmpty:
                pass # Should not happen if full, but good to handle

        await self.speech_queue.put(event.text)
        logger.info(f"Added to TTS queue: '{event.text[:50]}...' (Queue size: {self.speech_queue.qsize()})")
        
        # Update collab mode if it's part of the SpeakRequestEvent and relevant
        # self.collab_mode = event.collab_mode 

    async def _set_speaking_status(self, speaking: bool):
        async with self._is_currently_speaking_lock:
            if self._is_currently_speaking != speaking:
                self._is_currently_speaking = speaking
                await self.event_bus.publish(TTSSpeakingStateEvent(is_speaking=speaking))
                logger.info(f"TTS Speaking status changed to: {speaking}")

    async def _process_speech_queue(self):
        logger.info("TTS speech queue processor started.")
        while True:
            try:
                # Use asyncio.wait_for to make queue.get() responsive to cancellation
                text_to_speak = await asyncio.wait_for(self.speech_queue.get(), timeout=1.0)
                logger.info(f"Processing TTS for: '{text_to_speak[:50]}...'")

                if self._output_device_id is None:
                    await self.event_bus.publish(UILogEvent(message="TTS: No output device, skipping speech.", level="WARNING"))
                    self.speech_queue.task_done()
                    continue

                self._current_playback_stop_event = asyncio.Event()
                await self._set_speaking_status(True)

                try:
                    await self._generate_and_play_audio(text_to_speak, self._current_playback_stop_event)
                except asyncio.CancelledError:
                    logger.info("TTS generation/playback was cancelled during execution.")
                    # Re-raise to be caught by the outer CancelledError handler for the loop
                    raise
                except Exception as e:
                    logger.error(f"Error during TTS generation or playback: {e}", exc_info=True)
                    await self.event_bus.publish(UILogEvent(message=f"TTS Error: {e}", level="ERROR"))
                finally:
                    await self._set_speaking_status(False) # Ensure status is reset
                    self.speech_queue.task_done()
                    self._current_playback_stop_event = None
                    # Avoid asyncio.sleep here if the loop might be stopping

            except asyncio.TimeoutError:
                # This is expected if queue.get() times out.
                # Allows the loop to iterate and check for task cancellation.
                if self._processing_task and self._processing_task.cancelled(): # Check if the main task was cancelled
                    logger.info("TTS processing task detected cancellation during queue.get() timeout.")
                    break # Exit loop
                continue # Continue to the next iteration
            except asyncio.CancelledError:
                logger.info("TTS processing task (_process_speech_queue) was cancelled.")
                break # Exit the loop
            except Exception as e:
                logger.error(f"Unexpected error in TTS processing loop: {e}", exc_info=True)
                try:
                    # Only sleep if the loop is still clearly running; avoid if shutting down
                    if asyncio.get_running_loop().is_running() and not asyncio.get_running_loop().is_closed():
                        await asyncio.sleep(0.1)
                except RuntimeError: # Catch "no running event loop"
                    logger.warning("TTS loop: asyncio.sleep failed, event loop likely stopped during error handling.")
                    break # Exit if loop is gone
        logger.info("TTS speech queue processor finished.")

    async def _generate_and_play_audio(self, text: str, stop_event: asyncio.Event):
        safe_text = remove_emojis(text)
        if not safe_text.strip():
            logger.info("TTS: Text is empty after emoji removal, skipping.")
            return

        # Create a temporary WAV file for Piper's output
        tmp_wav_file = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_f:
                tmp_wav_path = tmp_f.name
            
            logger.debug(f"Piper TTS generating audio for: '{safe_text[:50]}' to {tmp_wav_path}")
            
            # --- Call Piper TTS ---
            process = await asyncio.create_subprocess_exec(
                self.settings.PIPER_PATH,
                "--model", self.settings.PIPER_VOICE_MODEL,
                "--output_file", tmp_wav_path,
                # Piper reads text from stdin if not using --input_file
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, # Capture stdout for logs/errors
                stderr=asyncio.subprocess.PIPE  # Capture stderr for logs/errors
            )
            stdout_piper, stderr_piper = await process.communicate(input=safe_text.encode('utf-8'))

            if process.returncode != 0:
                err_msg = stderr_piper.decode(errors='ignore').strip()
                logger.error(f"Piper TTS error (Code {process.returncode}): {err_msg}")
                await self.event_bus.publish(UILogEvent(message=f"Piper TTS error: {err_msg[:100]}", level="ERROR"))
                return
            
            if not os.path.exists(tmp_wav_path) or os.path.getsize(tmp_wav_path) == 0:
                logger.error(f"Piper ran but WAV file '{tmp_wav_path}' is missing or empty.")
                await self.event_bus.publish(UILogEvent(message="Piper ran but output WAV is empty.", level="ERROR"))
                return

            logger.debug(f"Piper TTS successful. Output: {tmp_wav_path}")

            # --- Load with Pydub and Modify Audio ---
            try:
                audio = AudioSegment.from_wav(tmp_wav_path)
            # Modify pitch/speed based on emotion/tone
            speed = 1.0
            pitch_shift = 0

            if self.current_emotion == "sad":
                speed = 0.9
                pitch_shift = -2
            elif self.current_emotion == "excited":
                speed = 1.2
                pitch_shift = 2
            elif self.current_emotion == "angry":
                speed = 1.1
                pitch_shift = 3
            elif self.current_emotion == "amused":
                speed = 1.05
                pitch_shift = 1
            elif self.current_tone == "sarcastic":
                pitch_shift = -1

            if pitch_shift != 0:
                segment = segment._spawn(segment.raw_data, overrides={
                    "frame_rate": int(segment.frame_rate * (2.0 ** (pitch_shift / 12.0)))
                }).set_frame_rate(segment.frame_rate)

            if speed != 1.0:
                segment = segment._spawn(segment.raw_data, overrides={
                    "frame_rate": int(segment.frame_rate * speed)
                }).set_frame_rate(segment.frame_rate)
            except CouldntDecodeError:
                logger.error(f"Pydub could not decode WAV file: {tmp_wav_path}")
                await self.event_bus.publish(UILogEvent(message="Error decoding TTS audio.", level="ERROR"))
                return

            # 1. Speed Change (affects pitch if not compensated)
            # Pydub's speedup doesn't preserve pitch by default.
            # A simple way to change speed is to change frame rate, then resample.
            # More advanced methods (e.g. WSOLA) would be needed for high-quality time-stretching without pitch change.
            # For simplicity, if speed != 1.0, we adjust frame rate. This will change pitch.
            if self.speech_speed != 1.0 and self.speech_speed > 0:
                logger.debug(f"Adjusting speed by factor: {self.speech_speed}")
                # This method changes speed AND pitch
                # audio = audio.speedup(playback_speed=self.speech_speed) # pydub >= 0.25.0
                # Old method:
                new_frame_rate = int(audio.frame_rate * self.speech_speed)
                audio = audio._spawn(audio.raw_data, overrides={"frame_rate": new_frame_rate})
                # Always resample to a common rate after frame rate override tricks
                audio = audio.set_frame_rate(self.settings.TTS_TARGET_SAMPLE_RATE or 44100)


            # 2. Pitch Change (semitones)
            if self.pitch_semitones != 0.0:
                logger.debug(f"Adjusting pitch by semitones: {self.pitch_semitones}")
                # Pydub doesn't have a direct semitone pitch shift.
                # It relies on a similar frame_rate trick or external libraries like 'soundstretch' or 'rubberband'.
                # Simple frame rate manipulation for pitch:
                octaves = self.pitch_semitones / 12.0
                new_sample_rate_for_pitch = int(audio.frame_rate * (2.0 ** octaves))
                audio = audio._spawn(audio.raw_data, overrides={"frame_rate": new_sample_rate_for_pitch})
                # Always resample to a common rate after frame rate override tricks
                audio = audio.set_frame_rate(self.settings.TTS_TARGET_SAMPLE_RATE or 44100)


            # 3. Volume Adjustment
            if self.volume_db_reduction != 0.0:
                logger.debug(f"Adjusting volume by DB: {-self.volume_db_reduction}")
                audio = audio - self.volume_db_reduction
            
            # --- Prepare for Sounddevice Playback ---
            samples = np.array(audio.get_array_of_samples()).astype(np.float32)
            max_val = np.max(np.abs(samples))
            if max_val > 0:
                samples = samples / max_val
            rms = np.sqrt(np.mean(samples ** 2))
            scaled_rms = min(rms * 100, 100.0)
            await self.event_bus.publish(AudioRMSVolumeEvent(rms_volume=scaled_rms))

            # Then safely scale to int16 range
            samples_int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
            samples_int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)

            # Calculate RMS for UI visualization (only once)
            rms = np.sqrt(np.mean(samples ** 2))
            scaled_rms = min(rms * 100, 100.0)
            await self.event_bus.publish(AudioRMSVolumeEvent(rms_volume=scaled_rms))

            # Convert to float32 for sounddevice
            samples = samples_int16.astype(np.float32) / 32768.0
          
            logger.debug(f"Audio ready for playback: {len(samples)} samples, SR={audio.frame_rate}, Channels={audio.channels}")

            # --- Play with Sounddevice (in executor) ---
            loop = asyncio.get_event_loop()
            playback_finished_future = loop.run_in_executor(
                None, self._blocking_play_audio, samples, audio.frame_rate, stop_event
            )
            stop_event_task = asyncio.create_task(stop_event.wait(), name="TTSStopEventWaitTask")
            
            done, pending = await asyncio.wait(
                [playback_finished_future, stop_event_task], # Pass the task here
                return_when=asyncio.FIRST_COMPLETED     
            )

            if stop_event.is_set():
                logger.info("TTS Playback explicitly stopped.")
                # Ensure sounddevice playback is stopped (it should if stop_event was passed)
                sd.stop() 
            
            # Handle exceptions from the playback future if it completed
            for fut in done:
                if fut is playback_finished_future and fut.exception():
                    logger.error(f"Error during sounddevice playback: {fut.exception()}", exc_info=fut.exception())
                    # Raise or handle as appropriate
            # Cancel any pending futures (should only be one at most)
            for fut in pending:
                fut.cancel()

        finally:
            # Clean up the temporary WAV file
            if tmp_wav_path and os.path.exists(tmp_wav_path):
                try:
                    os.remove(tmp_wav_path)
                    logger.debug(f"Removed temporary TTS WAV file: {tmp_wav_path}")
                except OSError as e: # Changed from PermissionError for broader catch
                    logger.warning(f"Could not remove temp WAV file '{tmp_wav_path}': {e}")

    def _blocking_play_audio(self, samples: np.ndarray, samplerate: int, stop_event_sync: asyncio.Event):
        """
        Plays audio using sounddevice. This is a blocking function.
        It checks the stop_event periodically to allow for interruption.
        Note: sd.play() is non-blocking by default, but sd.wait() is blocking.
        For interruptible playback, we can use sd.OutputStream and write in chunks,
        or use sd.play and rely on sd.stop() called from another thread/async task.
        The asyncio.Event passed here is for signalling from the async world to this sync function.
        """
        current_frame = 0
        chunk_size = samplerate // 10 # Play in 100ms chunks for responsiveness to stop_event

        try:
            # Using stream for more control, though sd.play/sd.stop can also work
            # if sd.stop() is called from the main async loop when stop_event is set.
            # Simpler: use sd.play and sd.wait(), but check stop_event in a loop around sd.wait() if possible,
            # or rely on the main async task calling sd.stop().
            
            # For this example, let's use sd.play and rely on sd.stop() from the async task.
            # sd.wait() would block until finished or sd.stop() is called.
            logger.debug(f"Starting sounddevice playback. Device: {self._output_device_id}")
            sd.play(samples, samplerate=samplerate, device=self._output_device_id)
            
            # Monitor the stop_event while sd.wait() blocks
            # This is a bit tricky because sd.wait() itself is blocking.
            # A better way if sd.wait() is used, is to have the main async task call sd.stop().
            # The stop_event.wait() in the async task _generate_and_play_audio achieves this.
            sd.wait() # Blocks until playback is finished OR sd.stop() is called

            if stop_event_sync.is_set(): # Check if it was stopped externally
                logger.info("Playback loop noted external stop signal after sd.wait completed/was interrupted.")

        except Exception as e:
            logger.error(f"Error in sounddevice playback thread: {e}", exc_info=True)
            # This exception needs to be communicated back to the async task if run_in_executor is used.
            # The run_in_executor will automatically propagate the exception to the future.
            raise # Re-raise to be caught by the future in the async task
        finally:
            logger.debug("Sounddevice playback finished or was stopped.")
            # sd.stop() # Ensure it's stopped, though sd.wait() implies it or was stopped.
                      # Calling sd.stop() here might be redundant if the async part already did.

    # --- UI Control Methods ---
    def set_volume_reduction(self, db_reduction: float):
        """Sets the volume reduction in dB for future TTS outputs."""
        self.volume_db_reduction = float(db_reduction)
        logger.info(f"TTS volume reduction set to: {self.volume_db_reduction} dB")
        asyncio.create_task(self.event_bus.publish(UILogEvent(
            message=f"TTS Volume reduction: {self.volume_db_reduction:.1f} dB", level="CONFIG"
        )))

    def set_speech_speed(self, speed_factor: float):
        """Sets the speech speed factor (e.g., 1.0 normal, 1.5 faster)."""
        if speed_factor > 0:
            self.speech_speed = float(speed_factor)
            logger.info(f"TTS speech speed set to: {self.speech_speed}x")
            asyncio.create_task(self.event_bus.publish(UILogEvent(
                message=f"TTS Speed: {self.speech_speed:.1f}x", level="CONFIG"
            )))
        else:
            logger.warning(f"Invalid speech speed factor: {speed_factor}. Must be > 0.")


    def set_pitch_semitones(self, semitones: float):
        """Sets the pitch shift in semitones."""
        self.pitch_semitones = float(semitones)
        logger.info(f"TTS pitch shift set to: {self.pitch_semitones} semitones")
        asyncio.create_task(self.event_bus.publish(UILogEvent(
            message=f"TTS Pitch: {self.pitch_semitones:.1f} semitones", level="CONFIG"
        )))

    def set_is_muted(self, muted: bool):
        """Mutes or unmutes TTS playback."""
        self.is_muted = bool(muted)
        status = "Muted" if self.is_muted else "Unmuted"
        logger.info(f"TTS has been {status.lower()}.")
        asyncio.create_task(self.event_bus.publish(UILogEvent(message=f"TTS {status}", level="CONFIG")))
        
        if self.is_muted and self._is_currently_speaking and self._current_playback_stop_event:
            logger.info("Muting active speech: stopping current playback.")
            self._current_playback_stop_event.set() # Stop current speech if muted

    def toggle_collab_mode(self, enabled: bool): # Placeholder for collab mode
        """Toggles collaboration mode (details TBD)."""
        self.collab_mode = bool(enabled)
        status = "enabled" if self.collab_mode else "disabled"
        logger.info(f"TTS Collab mode {status}.")
        asyncio.create_task(self.event_bus.publish(UILogEvent(message=f"TTS Collab Mode {status}", level="CONFIG")))
        # Actual collab mode logic (e.g., sending audio to a different output or API) would go here.


    def handle_emotion_tag(self, event: EmotionTagEvent):
        self.current_tone = event.tone
        self.current_emotion = event.emotion
        self.event_bus.emit(UILogEvent(f"[TTSService] Emotion updated: Tone = {event.tone}, Emotion = {event.emotion}"))
