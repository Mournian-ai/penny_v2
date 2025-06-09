import asyncio
import websockets
import logging
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import ExternalTranscriptEvent

connected_clients = set()

async def handler(websocket):
    """Handles incoming WebSocket messages and routes them based on type."""
    logging.info("Client connected.")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                message_type = data.get("type")

                if message_type == "transcription":
                    text = data.get("text", "").strip()
                    username = data.get("username", "Unknown")
                    if text:
                        logging.info(f"[Transcription] From {username}: {text}")
                        # Publish to your main program's event bus
                        EventBus.get_instance().publish(ExternalTranscriptEvent(text=text, speaker=username))
                
                elif message_type == "status":
                    message_text = data.get("message", "No message content.")
                    level = data.get("level", "INFO")
                    logging.info(f"[Status] Level: {level}, Message: {message_text}")
                    # Publish to your main dashboard's event bus
                    EventBus.get_instance().publish(UILogEvent(message=message_text, level=level))

                else:
                    logging.warning(f"Received unknown message type: {message}")

            except json.JSONDecodeError:
                logging.error(f"Failed to decode JSON from message: {message}")

    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Connection closed: {e}")
    finally:
        connected_clients.remove(websocket)
        logging.info("Client disconnected.")

async def start_ws_client(uri="ws://192.168.0.124:7001/ws"):
    """Continuously tries to connect to the WebSocket server."""
    logging.info(f"Attempting to connect to WebSocket at {uri}")
    async for websocket in websockets.connect(uri):
        try:
            await handler(websocket)
        except websockets.exceptions.ConnectionClosed:
            logging.warning("Connection lost. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
