# penny_v2/core/event_bus.py
import asyncio
import logging
from collections import defaultdict
from typing import Callable, Type, TypeVar, DefaultDict, List

from penny_v2.core.events import BaseEvent

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseEvent)

class EventBus:
    def __init__(self):
        self._subscribers: DefaultDict[Type[BaseEvent], List[Callable]] = defaultdict(list)
        self._async_subscribers: DefaultDict[Type[BaseEvent], List[Callable]] = defaultdict(list)

    def subscribe(self, event_type: Type[T], callback: Callable[[T], None]):
        """Subscribes a synchronous callback to an event type."""
        logger.debug(f"Subscribing {callback.__name__} to {event_type.__name__}")
        self._subscribers[event_type].append(callback)

    def subscribe_async(self, event_type: Type[T], coro_callback: Callable[[T], asyncio.Future]):
        """Subscribes an asynchronous callback (coroutine) to an event type."""
        logger.debug(f"Subscribing async {coro_callback.__name__} to {event_type.__name__}")
        self._async_subscribers[event_type].append(coro_callback)

    def unsubscribe(self, event_type: Type[T], callback: Callable):
        """Unsubscribes a callback from an event type."""
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
        if callback in self._async_subscribers[event_type]:
            self._async_subscribers[event_type].remove(callback)
            
    async def publish(self, event: BaseEvent):
        event_type = type(event)
        logger.debug(f"Publishing event: {event_type.__name__} - {event}")

        # Handle synchronous subscribers
        for callback in self._subscribers[event_type]:
            try:
                # Run synchronous callbacks in a thread pool executor to avoid blocking asyncio loop
                # Or, if they are very fast and GUI related, they might need to be scheduled via root.after
                # For simplicity here, we call directly, but this is a point of caution for long-running sync code.
                # If callback is for UI, it must be thread-safe or scheduled on UI thread.
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, callback, event) 
                # callback(event) # Direct call - BE CAREFUL if it blocks
            except Exception as e:
                logger.error(f"Error in sync subscriber {callback.__name__} for {event_type.__name__}: {e}", exc_info=True)

        # Handle asynchronous subscribers
        if self._async_subscribers[event_type]:
            await asyncio.gather(
                *(coro_callback(event) for coro_callback in self._async_subscribers[event_type]),
                return_exceptions=True # Allows other tasks to complete if one fails
            )
            # Check results for exceptions if needed