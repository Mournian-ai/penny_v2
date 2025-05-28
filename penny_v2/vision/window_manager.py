import pygetwindow as gw
import win32gui
import logging

logger = logging.getLogger(__name__)

class WindowManager:
    def __init__(self):
        self.windows = []

    def list_visible_windows(self):
        """Return a list of visible window titles."""
        self.windows = [w for w in gw.getWindowsWithTitle('') if w.title and w.visible]
        return [w.title for w in self.windows]

    def move_and_resize_window(self, title, x=0, y=0, width=1920, height=1080):
        try:
            window = next(w for w in self.windows if w.title == title)
            window.restore()  # Ensure it's not minimized
            window.moveTo(x, y)
            window.resizeTo(width, height)
            logger.info(f"[WindowManager] Moved '{title}' to ({x},{y}) at {width}x{height}")
            return True
        except Exception as e:
            logger.error(f"[WindowManager] Error moving window '{title}': {e}")
            return False
