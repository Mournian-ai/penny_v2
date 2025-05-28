# penny_v2/vision/window_utils.py
import pygetwindow as gw
import win32gui
import win32con

def list_visible_windows():
    return [w for w in gw.getWindowsWithTitle('') if w.title and w.isVisible]

def move_and_resize_window(title: str, x=0, y=0, width=1920, height=1080):
    try:
        window = gw.getWindowsWithTitle(title)[0]
        window.restore()  # Ensure it's not minimized
        window.moveTo(x, y)
        window.resizeTo(width, height)
        return True
    except Exception as e:
        print(f"[Window Move Error] {e}")
        return False
