"""
Cognitive Desktop Agent — Entry Point
Runs as a native messaging host, bridging the browser extension
to system-level APIs for focus tracking and idle detection.
"""

import sys
import os
import json
import asyncio
import platform
import struct
import threading

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cognitive_desktop.focus_tracker import FocusTracker
from cognitive_desktop.idle_detector import IdleDetector
from cognitive_desktop.native_messaging import NativeMessagingHost


class CognitiveDesktopAgent:
    """Main desktop agent that coordinates focus tracking and idle detection."""

    def __init__(self):
        self.focus_tracker = FocusTracker()
        self.idle_detector = IdleDetector()
        self.native_host = NativeMessagingHost()
        self.running = False
        self.current_focus = None
        self.current_idle_ms = 0

    def start(self):
        """Start the desktop agent."""
        print(f"[cognitive-agent] Starting on {platform.system()} {platform.release()}")

        self.running = True

        # Start focus tracking
        self.focus_tracker.on_focus_changed = self._on_focus_changed
        self.focus_tracker.start()

        # Start idle detection
        self.idle_detector.on_idle_changed = self._on_idle_changed
        self.idle_detector.start()

        # Start native messaging (reads from stdin for Chrome)
        self.native_host.on_message = self._on_browser_message
        self.native_host.start()

        print("[cognitive-agent] Agent running. Waiting for connections...")

        # Keep running
        try:
            while self.running:
                asyncio.run(self._tick())
                self._sleep(1.0)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop the desktop agent."""
        print("[cognitive-agent] Shutting down...")
        self.running = False
        self.focus_tracker.stop()
        self.idle_detector.stop()
        print("[cognitive-agent] Agent stopped.")

    async def _tick(self):
        """Periodic tick — send state updates to browser extension."""
        state = self._collect_state()
        # Send state to browser via native messaging
        try:
            message = json.dumps({
                "type": "DESKTOP_STATE",
                "payload": state,
            })
            # Prefix with 4-byte length (Chrome native messaging protocol)
            msg_bytes = message.encode("utf-8")
            sys.stdout.buffer.write(struct.pack("I", len(msg_bytes)))
            sys.stdout.buffer.write(msg_bytes)
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            # Browser disconnected
            pass

    def _collect_state(self):
        """Collect current desktop state."""
        return {
            "active_window": self.current_focus,
            "idle_seconds": self.current_idle_ms / 1000,
            "platform": platform.system(),
            "timestamp": self._iso_timestamp(),
        }

    def _on_focus_changed(self, window_info):
        """Callback when the active window changes."""
        self.current_focus = window_info
        print(f"[cognitive-agent] Focus: {window_info}")

    def _on_idle_changed(self, idle_ms):
        """Callback when idle state changes."""
        self.current_idle_ms = idle_ms

    def _on_browser_message(self, message):
        """Handle messages from the browser extension."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "GET_DESKTOP_STATE":
                return self._collect_state()
            elif msg_type == "PING":
                return {"status": "pong"}
            else:
                return {"error": f"Unknown message type: {msg_type}"}
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}

    @staticmethod
    def _iso_timestamp():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _sleep(seconds):
        """Cross-platform sleep that can be interrupted."""
        import time
        time.sleep(seconds)


def main():
    """Entry point for the desktop agent executable."""
    agent = CognitiveDesktopAgent()
    agent.start()


if __name__ == "__main__":
    main()