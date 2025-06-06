import asyncio
import websockets
import logging
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import ExternalTranscriptEvent

connected_clients = set()

async def handler(websocket):
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            text = data.get("text", "").strip()
            speaker = data.get("speaker", "Unknown")
            if text:
                EventBus.get_instance().publish(ExternalTranscriptEvent(text=text, speaker=speaker))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.remove(websocket)

async def start_ws_server(host="0.0.0.0", port=8765):
    logging.info(f"[WebSocket] Server started on ws://{host}:{port}")
    return await websockets.serve(handler, host, port)
