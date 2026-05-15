"""
Cognitive Desktop Agent - Idle Detector
Detects system idle time by tracking last user input (keyboard/mouse).
"""

import platform
import time
import threading
from typing import Optional, Callable


class IdleDetector:
    """
    Tracks system idle time (seconds since last user input).
    Useful as a signal for cognitive load: long idle periods may indicate
    the user is overwhelmed, distracted, or away.
    """

    def __init__(self):
        self.on_idle_changed: Optional[Callable] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_idle_seconds: float = 0.0
        self._check_interval = 5.0  # Poll every 5 seconds

    def start(self):
        """Start the idle detection loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the idle detection loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _poll_loop(self):
        """Poll idle time at regular intervals and fire callbacks on change."""
        while self._running:
            try:
                idle_seconds = self._get_idle_time()
                # Round to nearest second to avoid noisy callbacks
                idle_seconds = round(idle_seconds)

                if idle_seconds != round(self._last_idle_seconds):
                    self._last_idle_seconds = idle_seconds
                    if self.on_idle_changed:
                        self.on_idle_changed(idle_seconds * 1000)  # ms
            except Exception:
                pass

            time.sleep(self._check_interval)

    def _get_idle_time(self) -> float:
        """Get system idle time in seconds."""
        system = platform.system()

        if system == "Darwin":
            return self._get_idle_macos()
        elif system == "Windows":
            return self._get_idle_windows()
        else:
            return self._get_idle_linux()

    @staticmethod
    def _get_idle_macos() -> float:
        """Get idle time on macOS using ioreg."""
        try:
            # Use IOHIDSystem to get idle time
            result = subprocess_check_output(
                ["ioreg", "-c", "IOHIDSystem"],
                timeout=5,
            )
            for line in result.split("\n"):
                if "HIDIdleTime" in line:
                    # Value is in nanoseconds
                    parts = line.split("=")
                    if len(parts) == 2:
                        ns = float(parts[1].strip().strip('"'))
                        return ns / 1_000_000_000.0  # Convert to seconds
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _get_idle_windows() -> float:
        """Get idle time on Windows using GetLastInputInfo."""
        try:
            import ctypes
            from ctypes import wintypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("dwTime", wintypes.DWORD),
                ]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)

            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                tick_count = ctypes.windll.kernel32.GetTickCount()
                idle_ms = tick_count - lii.dwTime
                return idle_ms / 1000.0
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _get_idle_linux() -> float:
        """Get idle time on Linux using XScreenSaver or /proc."""
        try:
            import subprocess
            result = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / 1000.0  # ms to seconds
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        try:
            # Fallback: read /proc/uptime
            with open("/proc/uptime", "r") as f:
                system_uptime = float(f.read().split()[0])
            # This gives uptime, not idle time - but is a reasonable proxy
            return 0.0  # Cannot determine true idle from /proc/uptime alone
        except Exception:
            pass

        return 0.0

    def get_idle_seconds(self) -> float:
        """Get current idle time in seconds (synchronous query)."""
        return self._get_idle_time()


def subprocess_check_output(args, timeout=5):
    """Wrapper for subprocess.check_output with timeout."""
    import subprocess
    try:
        return subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=timeout
        ).decode("utf-8")
    except Exception:
        return ""