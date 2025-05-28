# penny_v2/services/twitch_token_manager.py

import os
import logging
import aiohttp
from typing import Optional
from dotenv import load_dotenv, set_key
from penny_v2.config import AppConfig

logger = logging.getLogger(__name__)
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

class TwitchTokenManager:
    def __init__(self, settings: AppConfig, env_path: str = ".env"):
        self.settings = settings
        self.env_path = os.getenv("ENV_PATH", env_path)

    async def refresh_app_token(self) -> Optional[str]:
        """
        Refreshes the Twitch App Access Token using the Client Credentials flow.
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

                        if not new_access_token:
                            logger.error(f"App token response missing access_token. Response: {response_data}")
                            return None

                        logger.info(f"App token acquired. Expires in {expires_in} seconds.")

                        self.settings.TWITCH_APP_ACCESS_TOKEN = new_access_token

                        if os.path.exists(self.env_path):
                            set_key(self.env_path, "TWITCH_APP_ACCESS_TOKEN", new_access_token)
                            logger.info("App token saved to .env.")
                        else:
                            logger.warning(f".env file not found at {self.env_path}. Cannot save App token.")

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
        Refreshes the Twitch Chat Access Token using the stored refresh token.
        """
        return await self._refresh_token(
            refresh_token=self.settings.TWITCH_CHAT_REFRESH_TOKEN,
            access_token_key="TWITCH_CHAT_TOKEN",
            refresh_token_key="TWITCH_CHAT_REFRESH_TOKEN",
            log_context="Chat"
        )

    async def _refresh_token(
        self,
        refresh_token: str,
        access_token_key: str,
        refresh_token_key: str,
        log_context: str
    ) -> Optional[str]:
        if not refresh_token:
            logger.error(f"{log_context} Refresh token not set. Cannot refresh {log_context.lower()} token.")
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

                        if not new_access_token:
                            logger.error(f"{log_context} Token refresh missing access_token. Response: {response_data}")
                            return None

                        logger.info(f"{log_context} token refreshed. Expires in {expires_in} seconds.")

                        setattr(self.settings, access_token_key, new_access_token)
                        if new_refresh_token:
                            setattr(self.settings, refresh_token_key, new_refresh_token)

                        if os.path.exists(self.env_path):
                            set_key(self.env_path, access_token_key, new_access_token)
                            if new_refresh_token:
                                set_key(self.env_path, refresh_token_key, new_refresh_token)
                            logger.info(f"{log_context} token saved to .env.")
                        else:
                            logger.warning(f".env file not found at {self.env_path}. Cannot save {log_context.lower()} tokens.")

                        return new_access_token
                    else:
                        logger.error(f"{log_context} Token refresh failed. Status: {response.status}")
                        logger.error(f"Response: {response_data}")
                        return None
        except Exception as e:
            logger.error(f"Exception during {log_context} token refresh: {e}", exc_info=True)
            return None
