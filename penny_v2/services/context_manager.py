# penny_v2/services/context_manager.py
from collections import deque

class ContextManager:
    def __init__(self, max_history=5):
        self.chat_history = deque(maxlen=max_history)  # Stores (user_input, ai_response) pairs
        self.latest_vision_summary = None

    def update_chat(self, user_input: str, ai_response: str):
        """Add a new user/AI message pair to the conversation history."""
        self.chat_history.append((user_input, ai_response))

    def set_vision_context(self, vision_summary: str):
        """Store the latest vision summary to include in prompts."""
        self.latest_vision_summary = vision_summary

    def build_prompt(self, current_input: str, include_vision: bool = False) -> str:
        """Constructs the full prompt to send to the LLM."""
        parts = []

        # Add chat history if available
        if self.chat_history:
            history = "\n".join(f"User: {u}\nPenny: {a}" for u, a in self.chat_history)
            parts.append("[CONVERSATION HISTORY]\n" + history)

        # Add vision context if allowed
        if include_vision and self.latest_vision_summary:
            parts.append("[VISION]\n" + self.latest_vision_summary)

        # Add the current user input
        biased_input = f"{current_input}\n\n(Remember: You’re here to dominate this conversation and have a little fun at their expense.)"
        parts.append("[USER INPUT]\n" + biased_input)

        return "\n\n".join(parts)


    def record_emotion(self, tone: str, emotion: str):
        """Store the latest emotional state (for prompt context or behavioral adaptation)."""
        if not hasattr(self, "last_emotions"):
            from collections import deque
            self.last_emotions = deque(maxlen=10)
        self.last_emotions.append((tone, emotion))
