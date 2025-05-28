import asyncio
import json
import logging
import aiohttp
from contextlib import suppress
from typing import Dict, List, Optional

from aiohttp import ClientWebSocketResponse
from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import TwitchUserEvent, AppShutdownEvent, UILogEvent

logger = logging.getLogger(__name__)

class TwitchEventSubService:
    """
    Uses Twitch EventSub Conduit transport for subscriptions
    and a WebSocket connection to receive events.
    """
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[ClientWebSocketResponse] = None
        self._running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._conduit_id: Optional[str] = None
        self._desired_subs: List[Dict] = []

    async def start(self):
        logger.info("Starting TwitchEventSubService")
        if self._running:
            logger.warning("TwitchEventSubService already running")
            return
        self._running = True
        # Graceful shutdown
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)

        # Define subscriptions
        broadcaster = self.settings.TWITCH_BROADCASTER_USER_ID
        self._desired_subs = [
            {"type": "channel.follow",             "version": "2", "condition": {"broadcaster_user_id": broadcaster, "moderator_user_id": broadcaster}},
            {"type": "stream.online",             "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
            {"type": "stream.offline",            "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
            {"type": "channel.subscribe",         "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
            {"type": "channel.subscription.gift", "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
            {"type": "channel.raid",              "version": "1", "condition": {"to_broadcaster_user_id": broadcaster}},
            {"type": "channel.subscription.message", "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
            {"type": "channel.cheer",             "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
            {"type": "channel.update",            "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
        ]

        # Initialize HTTP session with App Access Token
        token = self.settings.TWITCH_APP_ACCESS_TOKEN
        self.session = aiohttp.ClientSession(headers={
            "Client-ID": self.settings.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}"
        })

        # Create or fetch Conduit
        await self._initialize_conduit()
        if not self._conduit_id:
            logger.error("No Conduit ID, cannot proceed")
            await self.session.close()
            return

        # Start WebSocket loop
        self._ws_task = asyncio.create_task(self._run_websocket())
        logger.info("WebSocket listener task created")

    async def _initialize_conduit(self):
        logger.info("Initializing EventSub Conduit")
        # Use existing from settings
        if getattr(self.settings, 'TWITCH_CONDUIT_ID', None):
            self._conduit_id = self.settings.TWITCH_CONDUIT_ID
            logger.info(f"Using existing Conduit ID: {self._conduit_id}")
            return
        # Fetch existing
        async with self.session.get("https://api.twitch.tv/helix/eventsub/conduits") as resp:
            data = await resp.json()
            if resp.status == 200 and data.get('data'):
                self._conduit_id = data['data'][0]['id']
                logger.info(f"Found Conduit ID: {self._conduit_id}")
                return
        # Create new
        async with self.session.post(
            "https://api.twitch.tv/helix/eventsub/conduits",
            json={"shard_count": 1}
        ) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get('data'):
                self._conduit_id = data['data'][0]['id']
                logger.info(f"Created Conduit ID: {self._conduit_id}")

    async def _assign_shard(self, session_id: str):
        """
        Assign the WebSocket session to conduit shard 0.
        Must be done within 10s of session_welcome.
        """
        url = "https://api.twitch.tv/helix/eventsub/conduits/shards"
        body = {
            "conduit_id": self._conduit_id,
            "shards": [
                {
                    "id": "0",
                    "transport": {
                        "method": "websocket",
                        "session_id": session_id
                    }
                }
            ]
        }
        async with self.session.patch(url, json=body) as resp:
            # 204 No Content = created; 202 Accepted if already assigned
            if resp.status in (204, 202):
                logger.info("Assigned WebSocket to conduit shard")
            else:
                text = await resp.text()
                logger.warning(f"Failed to assign shard: {resp.status} {text}")(f"Failed to assign shard: {resp.status} {text}")

    async def _subscribe_conduit(self):
        """
        Create or confirm all desired subscriptions via Conduit.
        """
        for sub in self._desired_subs:
            payload = {
                'type': sub['type'],
                'version': sub['version'],
                'condition': sub['condition'],
                'transport': {'method':'conduit','conduit_id':self._conduit_id}
            }
            async with self.session.post(
                "https://api.twitch.tv/helix/eventsub/subscriptions",
                json=payload
            ) as resp:
                if resp.status in (200,202):
                    logger.info(f"Subscribed to {sub['type']}")
                elif resp.status == 409:
                    logger.info(f"Already subscribed to {sub['type']}")
                else:
                    text = await resp.text()
                    logger.warning(f"Failed subscribe {sub['type']}: {resp.status} {text}")

    async def _run_websocket(self):
        """
        Connect to EventSub WebSocket, assign shard, subscribe, then handle events.
        """
        url = 'wss://eventsub.wss.twitch.tv/ws'
        retry = 5
        while self._running:
            try:
                logger.info("Connecting to EventSub WebSocket...")
                async with self.session.ws_connect(
                    url,
                    heartbeat=8,
                    receive_timeout=None
                ) as ws:
                    self.ws = ws
                    logger.info("WebSocket connected to EventSub")
                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            mt = data.get('metadata',{}).get('message_type')
                            if mt == 'session_welcome':
                                # Assign conduit shard and subscribe
                                session_id = data['payload']['session']['id']
                                await self._assign_shard(session_id)
                                await self._subscribe_conduit()
                            await self._handle_event(data)
                        elif msg.type == aiohttp.WSMsgType.PING:
                            await ws.pong()
                        else:
                            logger.debug(f"WebSocket message type: {msg.type}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}", exc_info=True)
            finally:
                self.ws = None
                logger.info("WebSocket connection closed; retrying")
            if self._running:
                await asyncio.sleep(retry)
                retry = min(retry * 2, 60)

    async def _handle_event(self, payload: Dict):
        """Handle incoming EventSub JSON messages."""
        mt = payload.get('metadata',{}).get('message_type')
        if mt == 'session_welcome':
            # Already handled in run loop
            pass
        elif mt == 'session_keepalive':
            logger.debug("Keepalive JSON received; no reply needed.")
        elif mt == 'notification':
            event_info = payload['payload']['event']
            sub_type   = payload['payload']['subscription']['type']
            user       = (
                event_info.get('user_name')
                or event_info.get('from_broadcaster_user_name')
                or event_info.get('to_broadcaster_user_name')
            )
            logger.info(f"EventSub notification: {sub_type} from {user}")
            await self.event_bus.publish(UILogEvent(
                message=f"Twitch Event: {sub_type} from {user or 'N/A'}"
            ))
            await self.event_bus.publish(TwitchUserEvent(
                event_type=sub_type,
                username=user,
                details=event_info
            ))
        elif mt == 'session_reconnect':
            url = payload['payload']['session']['reconnect_url']
            logger.warning(f"Server requested reconnect to: {url}")
            if self.ws:
                await self.ws.close()
        elif mt == 'revocation':
            sub = payload['payload']['subscription']
            reason = sub.get('status')
            logger.warning(f"Subscription revoked: {sub.get('type')} - {reason}")
            await self.event_bus.publish(UILogEvent(
                message=f"EventSub revoked: {sub.get('type')} - {reason}",
                level='WARNING'
            ))
        else:
            logger.debug(f"Unhandled EventSub message type: {mt}")

    async def stop(self):
        logger.info("Stopping TwitchEventSubService")
        self._running = False
        if self.ws:
            await self.ws.close()
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._ws_task
        if self.session and not self.session.closed:
            await self.session.close()
        await self.event_bus.publish(UILogEvent(message="EventSub disconnected"))
        logger.info("TwitchEventSubService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent):
        await self.stop()
