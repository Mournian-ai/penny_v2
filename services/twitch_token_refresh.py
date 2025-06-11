# penny_v2/services/twitch_token_refresh.py

import os
import logging
import aiohttp
import json # Added
import time # Added
from typing import Optional
from dotenv import load_dotenv, set_key
from penny_v2.config import AppConfig

logger = logging.getLogger(__name__)
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
SETTINGS_FILE = "settings.json" # Define settings file name

class TwitchTokenManager:
    def __init__(self, settings: AppConfig, env_path: str = ".env"):
        self.settings = settings
        self.env_path = os.getenv("ENV_PATH", env_path)

    def _update_settings_json(self, updates: dict):
        """Reads, updates, and writes settings.json safely."""
        data = {}
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            
            # Ensure 'tokens' key exists
            if 'tokens' not in data:
                data['tokens'] = {}

            data['tokens'].update(updates)

            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            logger.info(f"Updated token info in {SETTINGS_FILE}: {updates.keys()}")

        except Exception as e:
            logger.error(f"Failed to read/write {SETTINGS_FILE}: {e}", exc_info=True)

    async def refresh_app_token(self) -> Optional[str]:
        """
        Refreshes the Twitch App Access Token and saves its expiry time.
        """
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.settings.TWITCH_CLIENT_ID,
            "client_secret": self.settings.TWITCH_CLIENT_SECRET,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TWITCH_TOKEN_URL, data=payload) as response:
                    response_data = await response.json()
                    if response.status == 200:
                        new_access_token = response_data.get("access_token")
                        expires_in = response_data.get("expires_in")

                        if not new_access_token or expires_in is None: # Check expires_in too
                            logger.error(f"App token response missing data. Response: {response_data}")
                            return None

                        # Calculate expiry timestamp
                        expires_at = int(time.time()) + expires_in
                        logger.info(f"App token acquired. Expires in {expires_in}s (at {expires_at}).")

                        # Update settings object and .env
                        self.settings.TWITCH_APP_ACCESS_TOKEN = new_access_token
                        if os.path.exists(self.env_path):
                            set_key(self.env_path, "TWITCH_APP_ACCESS_TOKEN", new_access_token)
                            logger.info("App token saved to .env.")
                        
                        # Update settings.json
                        self._update_settings_json({
                            "TWITCH_APP_TOKEN_EXPIRES_AT": expires_at
                        })

                        return new_access_token
                    else:
                        logger.error(f"App token request failed. Status: {response.status}")
                        logger.error(f"Response: {response_data}")
                        return None
        except Exception as e:
            logger.error(f"Exception during App token request: {e}", exc_info=True)
            return None

    async def refresh_chat_token(self) -> Optional[str]:
        """
        Refreshes the Twitch Chat Access Token and saves its expiry time.
        """
        return await self._refresh_token(
            refresh_token=self.settings.TWITCH_CHAT_REFRESH_TOKEN,
            access_token_key="TWITCH_CHAT_TOKEN",
            refresh_token_key="TWITCH_CHAT_REFRESH_TOKEN",
            expires_at_key="TWITCH_CHAT_TOKEN_EXPIRES_AT", # Added key
            log_context="Chat"
        )

    async def _refresh_token(
        self,
        refresh_token: str,
        access_token_key: str,
        refresh_token_key: str,
        expires_at_key: str, # Added key
        log_context: str
    ) -> Optional[str]:
        if not refresh_token:
            logger.error(f"{log_context} Refresh token not set.")
            return None

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.settings.TWITCH_CLIENT_ID,
            "client_secret": self.settings.TWITCH_CLIENT_SECRET,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TWITCH_TOKEN_URL, data=payload) as response:
                    response_data = await response.json()
                    if response.status == 200:
                        new_access_token = response_data.get("access_token")
                        new_refresh_token = response_data.get("refresh_token")
                        expires_in = response_data.get("expires_in")

                        if not new_access_token or expires_in is None: # Check expires_in too
                            logger.error(f"{log_context} Token refresh missing data. Response: {response_data}")
                            return None

                        # Calculate expiry timestamp
                        expires_at = int(time.time()) + expires_in
                        logger.info(f"{log_context} token refreshed. Expires in {expires_in}s (at {expires_at}).")

                        # Update settings object and .env
                        setattr(self.settings, access_token_key, new_access_token)
                        keys_to_save_env = {access_token_key: new_access_token}
                        if new_refresh_token:
                            setattr(self.settings, refresh_token_key, new_refresh_token)
                            keys_to_save_env[refresh_token_key] = new_refresh_token

                        if os.path.exists(self.env_path):
                            for key, value in keys_to_save_env.items():
                                set_key(self.env_path, key, value)
                            logger.info(f"{log_context} token saved to .env.")

                        # Update settings.json
                        self._update_settings_json({
                            expires_at_key: expires_at
                        })

                        return new_access_token
                    else:
                        logger.error(f"{log_context} Token refresh failed. Status: {response.status}")
                        logger.error(f"Response: {response_data}")
                        return None
        except Exception as e:
            logger.error(f"Exception during {log_context} token refresh: {e}", exc_info=True)
            return None
