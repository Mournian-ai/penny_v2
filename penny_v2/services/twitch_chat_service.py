# penny_v2/services/twitch_chat_service.py
import asyncio
import logging
from typing import Optional # Added for type hinting

from twitchio.ext import commands # twitchio library for IRC
from twitchio.client import Client # Import Client for a non-command bot if preferred
from twitchio.errors import AuthenticationError # For error handling

from penny_v2.config import AppConfig
from penny_v2.utils.helpers import should_respond_to_penny_mention
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import AppShutdownEvent, UILogEvent, TwitchMessageEvent
from penny_v2.services.api_client_service import APIClientService

logger = logging.getLogger(__name__)

class TwitchBot(commands.Bot): # Using commands.Bot for potential command handling later
    def __init__(self, event_bus_instance: EventBus, app_config: AppConfig, api_client_service):
        self.event_bus_instance = event_bus_instance
        self.app_config = app_config
        self.api_client_service = api_client_service
        super().__init__(
            token=app_config.TWITCH_CHAT_TOKEN, # This should be the bot's OAuth token (e.g., "oauth:yourtoken")
            prefix=getattr(app_config, 'COMMAND_PREFIX', '!'), # Get prefix from settings or default to '!'
            initial_channels=[app_config.TWITCH_CHANNEL.lower()] # Channel names should be lowercase
        )
        logger.debug(f"TwitchBot initialized for channel: {app_config.TWITCH_CHANNEL.lower()} with prefix: '{self._prefix}'")

    async def event_ready(self):
        """Called once the bot is successfully connected to Twitch IRC."""
        channel_name = self.app_config.TWITCH_CHANNEL.lower()
        logger.info(f"Twitch IRC Bot connected as {self.nick} to channel: {channel_name}")
        await self.event_bus_instance.publish(UILogEvent(f"Twitch Chat connected: {self.nick} on #{channel_name}", level="INFO"))

    async def event_message(self, message):
        """Handles incoming messages from Twitch chat."""
        if message.echo: # Ignore messages sent by the bot itself
            return

        logger.debug(f"Twitch Chat | <{message.author.name}>: {message.content}")
        await self.event_bus_instance.publish(
            TwitchMessageEvent(
                username=message.author.name,
                message=message.content,
                tags=message.tags or {} # Twitch tags provide extra context
            )
        )

        if should_respond_to_penny_mention(message.content):
            logger.info(f"[TwitchBot] Penny mention detected: {message.content}")
            await self.api_client_service.get_api_chat_response_text(
                username=message.author.name,
                message_text=message.content
            )
        
        if self._prefix:
             await self.handle_commands(message) 

    async def event_error(self, error: Exception, data: Optional[str] = None):
        """Handles errors from the Twitch connection."""
        logger.error(f"Twitch Bot error: {error}")
        if isinstance(error, AuthenticationError):
            logger.error("Twitch Authentication Error: Please check your TWITCH_TOKEN. It should be an OAuth token (e.g., 'oauth:xxxxxxxx').")
            await self.event_bus_instance.publish(UILogEvent("Twitch Chat Auth Failed. Check token.", level="CRITICAL"))
            # Consider a more graceful shutdown or preventing retries here if auth fails.
        else:
            await self.event_bus_instance.publish(UILogEvent(f"Twitch Chat Error: {error}", level="ERROR"))
        # Depending on the error, twitchio might try to reconnect or might close.

    async def event_command_error(self, ctx, error):
        """Handles errors when processing bot commands."""
        if isinstance(error, commands.CommandNotFound):
            # You can choose to ignore CommandNotFound or log it
            # logger.debug(f"Command not found: {ctx.command.name if ctx.command else ctx.message.content.split()[0]}")
            return # Don't respond to unknown commands unless you want to
        logger.error(f"Error in command {ctx.command.name if ctx.command else 'unknown'}: {error}")
        await ctx.send(f"Oops! Something went wrong with that command, @{ctx.author.name}.")


class TwitchChatService:
    def __init__(self, event_bus: EventBus, settings: AppConfig, api_client_service: APIClientService):
        self.event_bus = event_bus
        self.settings = settings
        self.api_client_service = api_client_service
        self.bot: Optional[TwitchBot] = None
        self._bot_run_task: Optional[asyncio.Task] = None
        self._is_running = False # To prevent multiple starts/stops

    async def start(self):
        if self._is_running:
            logger.warning("TwitchChatService is already running or starting.")
            return

        if not (self.settings.TWITCH_CHAT_TOKEN and self.settings.TWITCH_NICKNAME and self.settings.TWITCH_CHANNEL):
            logger.error("TWITCH_TOKEN, TWITCH_NICKNAME, or TWITCH_CHANNEL not configured. Twitch Chat Service cannot start.")
            await self.event_bus.publish(UILogEvent("Twitch Chat config missing (token, nick, or channel).", level="CRITICAL"))
            return
        
        # Ensure TWITCH_TOKEN starts with 'oauth:'
        if not self.settings.TWITCH_CHAT_TOKEN.startswith('oauth:'):
            logger.error("TWITCH_TOKEN does not appear to be a valid OAuth token (should start with 'oauth:').")
            await self.event_bus.publish(UILogEvent("Invalid TWITCH_TOKEN format for Chat.", level="CRITICAL"))
            # return # Optionally prevent start if token format is wrong

        logger.info("TwitchChatService starting...")
        self._is_running = True
        self.bot = TwitchBot(self.event_bus, self.settings, self.api_client_service)
        
        # The bot.start() method is blocking and runs the bot's internal loop.
        # We run it as an asyncio task.
        self._bot_run_task = asyncio.create_task(self._run_bot_with_retry())
        
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
        logger.info("TwitchChatService (bot run task) initiated.")

    async def _run_bot_with_retry(self):
        """Runs the bot and attempts to reconnect on disconnections."""
        if not self.bot: return # Should not happen if start logic is correct
        
        retry_delay = 5 # Initial delay in seconds
        max_retry_delay = 300 # Max delay 5 minutes

        while self._is_running: # Controlled by the service's overall running state
            try:
                logger.info(f"Attempting to connect Twitch bot to {self.settings.TWITCH_CHANNEL}...")
                # bot.start() is blocking and will run until connection error or bot.close()
                await self.bot.start() 
                # If bot.start() returns (e.g., after bot.close()), we might be shutting down
                if not self._is_running:
                    logger.info("Bot stopped as service is no longer running.")
                    break
                logger.warning("Twitch bot connection closed unexpectedly. Attempting to reconnect...")
            except AuthenticationError as e: # Specific handling for auth errors
                logger.critical(f"Twitch Authentication Failed: {e}. Please check your TWITCH_TOKEN. Stopping connection attempts.")
                await self.event_bus.publish(UILogEvent("Twitch Chat Authentication Failed. Service stopped.", level="CRITICAL"))
                self._is_running = False # Stop further retries for auth failure
                break # Exit the retry loop
            except Exception as e:
                logger.error(f"Error during Twitch bot execution: {e}. Retrying in {retry_delay}s.")
                await self.event_bus.publish(UILogEvent(f"Twitch Chat disconnected. Retrying...", level="WARNING"))
            
            if not self._is_running: # Check again if stop was called during error handling
                break

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay) # Exponential backoff

        logger.info("Twitch bot run loop has exited.")


    async def stop(self):
        if not self._is_running:
            logger.info("TwitchChatService is already stopped or stopping.")
            return

        logger.info("TwitchChatService stopping...")
        self._is_running = False # Signal the run loop to stop

        if self.bot:
            logger.info("Closing Twitch bot connection...")
            # bot.close() is the clean way to shut down the twitchio bot
            await self.bot.close() 
            self.bot = None # Clear the bot instance

        if self._bot_run_task and not self._bot_run_task.done():
            # Give the task a moment to finish after bot.close()
            try:
                await asyncio.wait_for(self._bot_run_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Twitch bot run task did not finish cleanly after close, cancelling.")
                self._bot_run_task.cancel()
                try:
                    await self._bot_run_task
                except asyncio.CancelledError:
                    logger.info("Twitch bot run task was cancelled.")
            except Exception as e: # Catch any other exception during await
                logger.error(f"Exception while awaiting bot task shutdown: {e}")
        
        self._bot_run_task = None
        logger.info("TwitchChatService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent):
        await self.stop()

    async def send_chat_message(self, text: str, channel: Optional[str] = None):
        """Sends a message to the specified Twitch channel (or default if None)."""
        if not self._is_running or not self.bot or not self.bot.is_connected(): # is_connected might need check for existence
            logger.warning("Twitch bot not connected or service not running, cannot send message.")
            return

        target_channel_name = (channel or self.settings.TWITCH_CHANNEL).lower()
        
        # twitchio's Bot class can send messages directly if connected to the channel.
        # Or get the channel object:
        chan_obj = self.bot.get_channel(target_channel_name)

        if chan_obj:
            try:
                await chan_obj.send(text)
                logger.info(f"Sent to Twitch Chat [{target_channel_name}]: {text}")
            except Exception as e: # Catch potential errors during send (e.g., connection issues)
                logger.error(f"Failed to send chat message to {target_channel_name}: {e}")
                await self.event_bus.publish(UILogEvent(f"Failed to send to Twitch: {text[:30]}...", level="WARNING"))
        else:
            logger.warning(f"Could not find channel object for {target_channel_name} to send message.")
            await self.event_bus.publish(UILogEvent(f"Twitch: Channel {target_channel_name} not found for sending.", level="WARNING"))
