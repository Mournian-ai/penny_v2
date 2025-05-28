# penny_v2/services/search_service.py
import asyncio
import logging
from typing import List, Dict, Optional

# You will need to install this: pip install google-api-python-client
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

from penny_v2.config import AppConfig
from penny_v2.core.event_bus import EventBus
from penny_v2.core.events import (
    AppShutdownEvent, UILogEvent, AIQueryEvent, SpeakRequestEvent # Import existing events
)

logger = logging.getLogger(__name__)

# Define new Events (Conceptually - you'll add these to events.py later)
# @dataclass
# class SearchRequestEvent(BaseEvent):
#    query: str
#    source: str = "unknown"
#    num_results: int = 3

# @dataclass
# class SearchResultEvent(BaseEvent):
#    query: str
#    results: List[Dict]
#    source: str
#    error: Optional[str] = None


class SearchService:
    def __init__(self, event_bus: EventBus, settings: AppConfig):
        self.event_bus = event_bus
        self.settings = settings
        self._running = False
        self.service = None

        if not GOOGLE_API_AVAILABLE:
            logger.error("google-api-python-client not installed. SearchService will be disabled.")
            return

        # IMPORTANT: Add these to your .env and config.py
        self.api_key = getattr(settings, "GOOGLE_API_KEY", None)
        self.cse_id = getattr(settings, "GOOGLE_CSE_ID", None)

        if self.api_key and self.cse_id:
            try:
                # Build the service object
                self.service = build("customsearch", "v1", developerKey=self.api_key)
                logger.info("Google Custom Search service built successfully.")
            except Exception as e:
                 logger.error(f"Failed to build Google Search service: {e}", exc_info=True)
        else:
            logger.warning("GOOGLE_API_KEY or GOOGLE_CSE_ID not found in settings. SearchService will be disabled.")

    async def start(self):
        """Starts the service and subscribes to events."""
        if not self.service:
            logger.error("SearchService cannot start - service not built (check config and installation).")
            await self.event_bus.publish(UILogEvent("SearchService disabled: Missing keys or library.", level="ERROR"))
            return

        logger.info("SearchService starting...")
        # TODO: Subscribe to SearchRequestEvent when defined and InteractionService is updated
        # self.event_bus.subscribe_async(SearchRequestEvent, self.handle_search_request)
        self.event_bus.subscribe_async(AppShutdownEvent, self.handle_shutdown)
        self._running = True
        logger.info("SearchService started.")

    async def stop(self):
        """Stops the service."""
        self._running = False
        logger.info("SearchService stopped.")

    async def handle_shutdown(self, event: AppShutdownEvent):
        """Handles application shutdown."""
        await self.stop()

    def _blocking_search(self, query: str, num_results: int) -> List[Dict]:
        """
        The actual (blocking) Google API call.
        Designed to be run in an executor.
        """
        try:
            logger.debug(f"Executing search API call for '{query}' (num={num_results})")
            res = self.service.cse().list(q=query, cx=self.cse_id, num=num_results).execute()
            return res.get('items', [])
        except HttpError as e:
            logger.error(f"Google Search API HttpError: {e.content}")
            # Consider parsing e.content for specific error messages
            raise  # Re-raise to be caught in the async wrapper
        except Exception as e:
            logger.error(f"Unexpected error during Google Search API call: {e}", exc_info=True)
            raise # Re-raise

    async def perform_search(self, query: str, num_results: int = 3) -> List[Dict]:
        """
        Asynchronously performs a search using the Google CSE API.
        Returns a list of result dictionaries or an empty list on error.
        """
        if not self._running or not self.service:
            logger.warning("SearchService not running or configured. Cannot perform search.")
            return []

        logger.info(f"Performing search via executor for: '{query}'")
        loop = asyncio.get_event_loop()
        try:
            # Run the blocking API call in the default thread pool executor
            results = await loop.run_in_executor(
                None, self._blocking_search, query, num_results
            )
            logger.info(f"Search found {len(results)} results for '{query}'.")
            return results
        except HttpError as e:
            await self.event_bus.publish(UILogEvent(f"Search failed (HTTP Error {e.resp.status}) for '{query}'.", level="ERROR"))
            return []
        except Exception as e:
            await self.event_bus.publish(UILogEvent(f"Search failed for '{query}': {e}", level="ERROR"))
            return []

    # --- Event Handler (Example - To be implemented fully later) ---
    # async def handle_search_request(self, event: SearchRequestEvent):
    #     """Handles incoming search requests from the event bus."""
    #     logger.info(f"Handling SearchRequestEvent for '{event.query}' from {event.source}")
    #     results = await self.perform_search(event.query, event.num_results)
    #
    #     await self.event_bus.publish(SearchResultEvent(
    #         query=event.query,
    #         results=results,
    #         source=event.source,
    #         error="No results found." if not results else None
    #     ))
    #
    #     # Example: If it came from LLM, maybe feed it back?
    #     if event.source == "llm_request" and results:
    #         snippet = results[0].get('snippet', 'No snippet.')
    #         await self.event_bus.publish(AIQueryEvent(
    #              instruction=f"You asked to search for '{event.query}'. Here's the top result. Use it to answer your original goal.",
    #              input_text=snippet
    #         ))
    #     elif not results:
    #          await self.event_bus.publish(SpeakRequestEvent(text=f"Sorry, I couldn't find anything for {event.query}"))
