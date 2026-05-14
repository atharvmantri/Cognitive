"""
Cognitive Desktop Agent - Focus Tracker
Tracks the active window/application across the desktop.
Platform-specific implementations for macOS and Windows.
"""

import platform
import subprocess
import time
import threading
from typing import Optional, Callable, Dict, Any


class FocusTracker:
    """Tracks the currently focused application/window."""

    def __init__(self):
        self.on_focus_changed: Optional[Callable] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_focus: Optional[str] = None
        self._check_interval = 1.0  # Check every second

    def start(self):
        """Start tracking focus in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop tracking focus."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _poll_loop(self):
        """Poll for focus changes at regular intervals."""
        while self._running:
            try:
                current = self._get_active_window()
                if current and current != self._last_focus:
                    self._last_focus = current
                    if self.on_focus_changed:
                        self.on_focus_changed(current)
            except Exception:
                pass
            time.sleep(self._check_interval)

    def _get_active_window(self) -> Optional[str]:
        """Get the active window title and process name."""
        system = platform.system()

        if system == "Darwin":
            return self._get_active_window_macos()
        elif system == "Windows":
            return self._get_active_window_windows()
        else:
            return self._get_active_window_linux()

    @staticmethod
    def _get_active_window_macos() -> Optional[str]:
        """Get active window on macOS using osascript."""
        try:
            # Get the frontmost application name
            app_script = 'tell application "System Events" to name of first application process whose frontmost is true'
            app_name = subprocess.check_output(
                ["osascript", "-e", app_script],
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).decode().strip()

            # Get the window title of the frontmost app
            title_script = (
                'tell application "System Events" to tell process "'
                + app_name
                + '" to name of first window whose value of attribute "AXMain" is true'
            )
            try:
                window_title = subprocess.check_output(
                    ["osascript", "-e", title_script],
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                ).decode().strip()
            except Exception:
                window_title = "Unknown Window"

            return f"{app_name}: {window_title}"
        except Exception:
            return None

    @staticmethod
    def _get_active_window_windows() -> Optional[str]:
        """Get active window on Windows using ctypes."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            # Get foreground window handle
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None

            # Get window title length
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return None

            # Get window title
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            window_title = buffer.value

            # Get process ID
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            # Get process name from PID
            process_name = FocusTracker._get_process_name_windows(pid.value)

            if process_name:
                return f"{process_name}: {window_title}"
            return window_title

        except Exception:
            # Fallback: try using psutil if available
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if proc.pid == pid.value:
                            return f"{proc.name()}: {window_title}"
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass
            return None

    @staticmethod
    def _get_process_name_windows(pid: int) -> Optional[str]:
        """Get process name from PID on Windows."""
        try:
            import psutil
            proc = psutil.Process(pid)
            return proc.name()
        except Exception:
            return None

    @staticmethod
    def _get_active_window_linux() -> Optional[str]:
        """Get active window on Linux using xdotool or wmctrl."""
        try:
            # Try xdotool first
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            # Fallback to wmctrl
            result = subprocess.run(
                ["wmctrl", "-p"], capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                # Parse output - first line with active window
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(None, 3)
                    if len(parts) >= 4 and "-1" not in parts[2]:
                        return parts[3]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None

    def get_active_window(self) -> Optional[str]:
        """Get the current active window (synchronous)."""
        return self._get_active_window()