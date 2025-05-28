import asyncio
import aiohttp
import websockets
import json
import logging
import os
from typing import Optional

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus 
from penny_v2.core.events import ( 
    AppShutdownEvent,
    UILogEvent,
    AIQueryEvent,
    AudioRecordedEvent,
    TranscriptionAvailableEvent,
)

logger = logging.getLogger(__name__)

def is_valid_transcription(text: str) -> bool:
    cleaned = text.strip().replace(" ", "")
    return cleaned and cleaned not in {".", "..", "...", ". . .", "…"}

class TranscribeService:
    CHUNK_SIZE = 64 * 1024  # 64KB per WS chunk
    MAX_RETRIES = 3

    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        # Ensure FASTAPI_URL_TRANSCRIBE does not end with a slash if /transcribe is hardcoded
        base_url = settings.FASTAPI_URL_TRANSCRIBE.rstrip('/')
        self.http_url = f"{base_url}/transcribe"
        self.ws_url = settings.WEBSOCKET_TRANSCRIBE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        logger.info("TranscribeService starting...")
        # Standard timeout: 5 min for connect, 30 sec for total. Adjust as needed.
        timeout = aiohttp.ClientTimeout(connect=300, total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
        self.event_bus.subscribe_async(AudioRecordedEvent, self._on_audio_recorded)
        self._running = True
        logger.info("TranscribeService started.")

    async def stop(self) -> None:
        if not self._running:
            return
        logger.info("TranscribeService stopping...")
        self._running = False
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("TranscribeService aiohttp session closed.")
        logger.info("TranscribeService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent) -> None:
        await self.stop()

    async def _on_audio_recorded(self, event: AudioRecordedEvent) -> None:
        if not self._running:
            logger.warning("TranscribeService not running, skipping audio processing.")
            return
            
        if event.audio_path and os.path.exists(event.audio_path): # Check if path exists
            try:
                # ALWAYS read from the file path to get the full WAV (with headers)
                audio_bytes_to_send = self._read_file(event.audio_path)
                
                # Use event.filename if provided and makes sense, otherwise derive from path
                filename_to_send = event.filename if event.filename else os.path.basename(event.audio_path)
                
                if not audio_bytes_to_send:
                    logger.error(f"Failed to read audio from path or file is empty: {event.audio_path}")
                    await self.event_bus.publish(UILogEvent(message=f"Audio file empty/unreadable: {os.path.basename(event.audio_path)}", level="ERROR"))
                    return

                logger.info(f"AudioService: Processing audio from disk: {filename_to_send} ({len(audio_bytes_to_send)} bytes)")
                await self._process_http(audio_bytes_to_send, filename_to_send)

            except FileNotFoundError:
                logger.error(f"Audio file not found at path: {event.audio_path}")
                await self.event_bus.publish(UILogEvent(message=f"Audio file not found: {os.path.basename(event.audio_path)}", level="ERROR"))
            except Exception as e:
                logger.error(f"Error reading or processing audio from path {event.audio_path}: {e}", exc_info=True)
                await self.event_bus.publish(UILogEvent(message=f"Error processing audio {os.path.basename(event.audio_path)}", level="ERROR"))
                try:
                    os.remove(event.audio_path)
                    logger.debug(f"Deleted temp audio file: {event.audio_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete temp audio file: {event.audio_path} | Error: {e}")
        elif event.audio_bytes:
            # This case should ideally not be hit if ListeningService always provides a path.
            # If it does, these are likely raw PCM bytes and will fail on the server.
            logger.warning(
                f"AudioRecordedEvent for '{event.filename}' has audio_bytes but no valid audio_path. "
                "Sending raw bytes, but server expects a full WAV file. This will likely fail."
            )
            # Forcing an error or specific handling might be better than sending known bad data.
            # For now, let's try sending it but expect failure.
            filename_to_send = event.filename if event.filename else "raw_audio_segment.raw"
            await self._process_http(event.audio_bytes, filename_to_send) # This will likely still fail server-side
            await self.event_bus.publish(UILogEvent(message=f"Sent raw audio for {filename_to_send}, may fail.", level="WARNING"))

        else:
            logger.warning(f"AudioRecordedEvent received without audio_path or audio_bytes. Event: {event.__dict__}")



    def _read_file(self, path: str) -> bytes:
        # This is a synchronous file read. For very large files in a highly async environment,
        # you might consider aiofiles, but for typical audio segments, this is usually fine.
        with open(path, "rb") as f:
            return f.read()

    async def _post_with_retries(self, wav_bytes: bytes, identifier: str) -> dict:
        """
        Posts audio data with retries, creating new FormData for each attempt.
        """
        last_exc = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            # --- CREATE NEW FORMDATA FOR EACH ATTEMPT ---
            data = aiohttp.FormData()
            data.add_field('file',
                           wav_bytes,
                           filename=identifier,
                           content_type='audio/wav')
            # --------------------------------------------
            try:
                assert self.session, "Aiohttp session not initialized"
                logger.info(f"HTTP attempt {attempt}/{self.MAX_RETRIES} for {identifier} to {self.http_url}")
                async with self.session.post(self.http_url, data=data) as resp:
                    # Log response status and headers for debugging
                    logger.debug(f"Response status for {identifier}: {resp.status}")
                    logger.debug(f"Response headers for {identifier}: {resp.headers}")
                    
                    if resp.status >= 400: # Check for client/server errors
                        error_text = await resp.text()
                        logger.warning(
                            f"HTTP attempt {attempt} failed for {identifier} with status {resp.status}: {error_text}"
                        )
                        # Raise for status will create an exception we can catch
                        resp.raise_for_status() 
                    
                    # If successful (2xx)
                    logger.info(f"HTTP attempt {attempt} successful for {identifier}.")
                    return await resp.json() # Assuming server returns JSON

            except aiohttp.ClientResponseError as e: # Specific exception for HTTP errors
                last_exc = e
                logger.warning(
                    f"HTTP attempt {attempt} ClientResponseError for {identifier}: "
                    f"Status={e.status}, Message='{e.message}', URL='{e.request_info.url}'"
                )
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(1 * attempt) # Exponential backoff might be better
                else: # Last attempt
                    logger.error(f"All HTTP attempts failed for {identifier} after {attempt} tries.")
                    raise # Re-raise the last ClientResponseError

            except aiohttp.ClientError as e: # Catch other aiohttp client errors (e.g., connection issues)
                last_exc = e
                logger.warning(f"HTTP attempt {attempt} ClientError for {identifier}: {e}")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(1 * attempt)
                else:
                    logger.error(f"All HTTP attempts (ClientError) failed for {identifier} after {attempt} tries.")
                    raise
            
            except Exception as e: # Catch any other unexpected errors
                last_exc = e
                logger.error(f"Unexpected error during HTTP attempt {attempt} for {identifier}: {e}", exc_info=True)
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(1 * attempt)
                else:
                    logger.error(f"All HTTP attempts (Unexpected Error) failed for {identifier} after {attempt} tries.")
                    raise

        # This part should ideally not be reached if an exception is raised on the last attempt
        if last_exc:
            raise last_exc
        else:
            # Should not happen if MAX_RETRIES >= 1
            raise RuntimeError(f"Transcription failed for {identifier} after {self.MAX_RETRIES} attempts without a specific exception.")

    async def _process_http(self, wav_bytes: bytes, identifier: str) -> None:
        if not self._running or not self.session or self.session.closed:
            logger.warning(f"TranscribeService not ready or session closed for HTTP transcription of {identifier}.")
            return

        try:
            logger.info(f"HTTP transcription request for {identifier} ({len(wav_bytes)} bytes)")
            result = await self._post_with_retries(wav_bytes, identifier)
            
            text = result.get("text", "").strip()

            if not is_valid_transcription(text):
                logger.info(f"Skipping AIQueryEvent for {identifier} due to empty or junk transcription: '{text}'")
                return

            logger.info(f"HTTP transcription result for {identifier}: '{text}'")

            await self.event_bus.publish(
                TranscriptionAvailableEvent(text=text, is_final=True, audio_path=identifier)
            )

            await self.event_bus.publish(
                AIQueryEvent(instruction="process_transcription", input_text=text)
            )

        except Exception as e:
            logger.error(f"HTTP transcription processing failed for {identifier}: {e}", exc_info=True)
            await self.event_bus.publish(
                TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=str(e))
            )
            await self.event_bus.publish(
                UILogEvent(message=f"Transcription HTTP Error for {identifier}: {e}", level="ERROR")
            )


    async def transcribe_bytes_via_ws(self, wav_bytes: bytes, identifier: str) -> None:
        if not self._running:
            logger.warning(f"TranscribeService not ready for WS transcription of {identifier}.")
            return
        try:
            logger.info(f"WS transcription request for {identifier} ({len(wav_bytes)} bytes) to {self.ws_url}")
            # Consider adding connect_timeout and ping_interval/ping_timeout for robustness
            async with websockets.connect(self.ws_url, max_size=2**24, ping_interval=20, ping_timeout=20) as ws:
                logger.info(f"WS connection established for {identifier}.")
                # Stream chunks
                for i in range(0, len(wav_bytes), self.CHUNK_SIZE):
                    chunk = wav_bytes[i:i+self.CHUNK_SIZE]
                    await ws.send(chunk)
                    logger.debug(f"Sent chunk {i//self.CHUNK_SIZE + 1} for {identifier} ({len(chunk)} bytes)")
                    await asyncio.sleep(0.01) # Small sleep to allow server to process, adjust as needed

                # Send an end-of-stream message if your WS server expects one
                # For example: await ws.send(json.dumps({"event": "EOS"}))
                # Or simply close the sending side: await ws.close_sending_stream()
                # This depends on your WebSocket server's protocol.
                # If it just waits for the client to stop sending and then processes, this might be fine.
                # Often, an explicit "done" message or closing the send stream is better.
                logger.info(f"Finished sending all audio data for {identifier} via WS.")

                full_transcription_segments = []
                final_text_received = False
                while True: # Loop to receive messages
                    try:
                        # Set a timeout for receiving messages to prevent hanging indefinitely
                        msg = await asyncio.wait_for(ws.recv(), timeout=30.0) 
                        payload = json.loads(msg)
                        segment_text = payload.get("text", "")
                        is_final_segment = payload.get("is_final", False)
                        
                        logger.debug(f"WS received for {identifier}: final={is_final_segment}, text='{segment_text}'")

                        await self.event_bus.publish(
                            TranscriptionAvailableEvent(text=segment_text, is_final=is_final_segment, audio_path=identifier)
                        )
                        
                        # Accumulate text. Be mindful of how your server sends final transcriptions.
                        # If 'is_final' comes with the full text, you might not need to accumulate.
                        # If 'is_final' comes with the *last segment* of the full text, you do.
                        full_transcription_segments.append(segment_text)

                        if is_final_segment:
                            final_text = "".join(full_transcription_segments).strip()
                            # It's possible the server sends the full text when is_final=True
                            # If payload.get("text") is the full final text when is_final=True, use that directly.
                            # Example: if is_final_segment and server sends full text in 'text':
                            # final_text = segment_text.strip()

                            logger.info(f"WS final transcription for {identifier}: '{final_text}'")
                            # Publish the consolidated final text again if it's different or if partials were also "final"
                            # This ensures a single, truly final event if needed.
                            await self.event_bus.publish(
                                TranscriptionAvailableEvent(text=final_text, is_final=True, audio_path=identifier)
                            )
                            if final_text:
                                await self.event_bus.publish(
                                    AIQueryEvent(instruction="process_transcription", input_text=final_text) # Changed instruction
                                )
                            else:
                                logger.info(f"Skipping AIQueryEvent for {identifier} (WS) due to empty final transcription.")
                            final_text_received = True
                            break # Exit receive loop once final transcription is processed
                    
                    except websockets.exceptions.ConnectionClosedOK:
                        logger.info(f"WS connection closed gracefully by server for {identifier}.")
                        break
                    except websockets.exceptions.ConnectionClosedError as e:
                        logger.warning(f"WS connection closed with error for {identifier}: {e}")
                        if not final_text_received: # If connection closed before final, publish error
                           await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=str(e)))
                        break
                    except asyncio.TimeoutError:
                        logger.warning(f"WS receive timeout for {identifier}. No message in 30s.")
                        if not final_text_received:
                           await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error="WebSocket receive timeout"))
                        break # Exit if no message received for a while
                    except json.JSONDecodeError as e:
                        logger.error(f"WS JSON decode error for {identifier}: {e}. Message: '{msg}'")
                        # Continue to try and receive next message if possible, or break
                        continue 
                    except Exception as e_inner: # Catch other errors inside the loop
                        logger.error(f"Error processing WS message for {identifier}: {e_inner}", exc_info=True)
                        if not final_text_received:
                            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=str(e_inner)))
                        break # Break on other unexpected errors

                if not final_text_received: # If loop exited without receiving a final text
                    logger.warning(f"WS transcription for {identifier} did not receive a final segment.")
                    # Publish an error or empty final transcription if not already done
                    # This might be redundant if already handled in exception blocks, but acts as a fallback
                    existing_error_event = TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error="No final transcription received")
                    # Check if an error event was already sent to avoid duplicates
                    # This check is conceptual; actual implementation might need tracking state
                    # For simplicity here, we might just publish it.
                    await self.event_bus.publish(existing_error_event)


        except websockets.exceptions.InvalidURI:
            logger.error(f"WS Invalid URI: {self.ws_url}", exc_info=True)
            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=f"Invalid WebSocket URI: {self.ws_url}"))
            await self.event_bus.publish(UILogEvent(message=f"Transcription WS Invalid URI: {self.ws_url}", level="ERROR"))
        except websockets.exceptions.WebSocketException as e: # Catch specific websocket connection errors
            logger.error(f"WS connection failed for {identifier} to {self.ws_url}: {e}", exc_info=True)
            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=str(e)))
            await self.event_bus.publish(UILogEvent(message=f"Transcription WS Connection Error: {e}", level="ERROR"))
        except ConnectionRefusedError as e: # Catch connection refused specifically
            logger.error(f"WS ConnectionRefusedError for {identifier} to {self.ws_url}: {e}", exc_info=True)
            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=str(e)))
            await self.event_bus.publish(UILogEvent(message=f"Transcription WS Connection Refused: {self.ws_url}", level="ERROR"))
        except Exception as e: # Catch-all for other unexpected errors during WS processing
            logger.error(f"Generic WS transcription error for {identifier}: {e}", exc_info=True)
            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=identifier, error=str(e)))
            await self.event_bus.publish(UILogEvent(message=f"Transcription WS Generic Error: {e}", level="ERROR"))


    async def transcribe_audio_path_via_ws(self, audio_path: str) -> None:
        """Helper to transcribe a file path via WebSockets."""
        if not self._running:
            logger.warning("TranscribeService not running, skipping WS transcription for path.")
            return
        try:
            audio_bytes = self._read_file(audio_path)
            await self.transcribe_bytes_via_ws(audio_bytes, audio_path)
        except FileNotFoundError:
            logger.error(f"Audio file not found for WS transcription: {audio_path}")
            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=audio_path, error="File not found"))
            await self.event_bus.publish(UILogEvent(message=f"Audio file not found: {audio_path}", level="ERROR"))
        except Exception as e:
            logger.error(f"Error reading file for WS transcription {audio_path}: {e}", exc_info=True)
            await self.event_bus.publish(TranscriptionAvailableEvent(text="", is_final=True, audio_path=audio_path, error=f"Error reading file: {e}"))
            await self.event_bus.publish(UILogEvent(message=f"Error reading audio file {audio_path}: {e}", level="ERROR"))

