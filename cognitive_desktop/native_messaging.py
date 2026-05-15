"""
Cognitive Desktop Agent - Native Messaging Bridge
Implements the Chrome Native Messaging protocol for communication
between the browser extension and the desktop agent.

Protocol:
  - Messages are prefixed with a 4-byte length (native byte order, 32-bit)
  - JSON payloads are UTF-8 encoded
  - Extension sends requests via chrome.runtime.sendNativeMessage
  - Agent responds via stdout with same length-prefixed format
"""

import sys
import json
import struct
import threading
from typing import Optional, Callable, Dict, Any


class NativeMessagingHost:
    """Chrome Native Messaging protocol handler."""

    def __init__(self):
        self.on_message: Optional[Callable] = None
        self._running = False
        self._read_thread: Optional[threading.Thread] = None

    def start(self):
        """Start listening for messages from the browser extension."""
        self._running = True
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()

    def stop(self):
        """Stop the native messaging loop."""
        self._running = False
        if self._read_thread:
            self._read_thread.join(timeout=2.0)

    def send(self, message: dict):
        """Send a message back to the browser extension."""
        try:
            encoded = json.dumps(message).encode("utf-8")
            # Prefix with 4-byte length (Chrome native messaging format)
            sys.stdout.buffer.write(struct.pack("I", len(encoded)))
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            self._running = False

    def _read_loop(self):
        """Continuously read length-prefixed messages from stdin."""
        while self._running:
            try:
                # Read 4-byte length prefix
                raw_length = self._read_bytes(4)
                if not raw_length:
                    # No data available, brief sleep
                    self._sleep(0.1)
                    continue

                if len(raw_length) < 4:
                    break

                msg_length = struct.unpack("I", raw_length)[0]

                # Sanity check: prevent memory exhaustion
                if msg_length > 10 * 1024 * 1024:  # 10 MB max
                    print(f"[cognitive-agent] Message too large: {msg_length} bytes",
                          file=sys.stderr)
                    break

                # Read the JSON payload
                raw_message = self._read_bytes(msg_length)
                if len(raw_message) < msg_length:
                    break

                message = json.loads(raw_message.decode("utf-8"))

                # Dispatch to handler
                if self.on_message:
                    response = self.on_message(message)
                    if response is not None:
                        self.send(response)
                else:
                    self.send({"error": "no message handler registered"})

            except json.JSONDecodeError as e:
                print(f"[cognitive-agent] JSON decode error: {e}", file=sys.stderr)
                continue
            except struct.error:
                # Incomplete length prefix, keep waiting
                continue
            except BrokenPipeError:
                # Browser disconnected
                self._running = False
                break
            except Exception as e:
                print(f"[cognitive-agent] Read error: {e}", file=sys.stderr)
                continue

    def _read_bytes(self, n: int) -> bytes:
        """Read exactly n bytes from stdin, blocking."""
        data = b""
        remaining = n
        while remaining > 0 and self._running:
            try:
                chunk = sys.stdin.buffer.read(remaining)
                if not chunk:
                    break
                data += chunk
                remaining -= len(chunk)
            except (IOError, OSError):
                break
        return data

    @staticmethod
    def _sleep(seconds: float):
        """Sleep without blocking signal handling."""
        import time
        time.sleep(seconds)


def register_native_host(app_name: str, host_path: str):
    """
    Print registry commands for registering the native messaging host.
    On macOS/Linux, this outputs shell commands. On Windows, it outputs
    PowerShell commands. Must be run with appropriate privileges.

    Windows registry:
      HKEY_LOCAL_MACHINE\\SOFTWARE\\Google\\Chrome\\NativeMessagingHosts\\com.cognitive.agent
      HKEY_CURRENT_USER\\SOFTWARE\\Google\\Chrome\\NativeMessagingHosts\\com.cognitive.agent
    """
    import platform
    system = platform.system()

    if system == "Windows":
        print(f"# Run in PowerShell as Administrator:")
        print(f'$path = "HKCU:\\Software\\Google\\Chrome\\NativeMessagingHosts\\{app_name}"')
        print(f"New-Item -Force -Path $path")
        print(f'Set-ItemProperty -Path $path -Name "(Default)" -Value \'{{"name":"{app_name}","description":"Cognitive Desktop Agent","path":"{host_path}","type":"stdio"}}\'')
    elif system == "Darwin":
        print(f"# Run in Terminal:")
        print(f"mkdir -p ~/Library/Application\\ Support/Google/Chrome/NativeMessagingHosts/")
        print(f"cat > ~/Library/Application\\ Support/Google/Chrome/NativeMessagingHosts/{app_name}.json << EOF")
        print(json.dumps({
            "name": app_name,
            "description": "Cognitive Desktop Agent",
            "path": host_path,
            "type": "stdio",
            "allowed_origins": [
                "chrome-extension://EXTENSION_ID_HERE/"
            ],
        }, indent=2))
        print("EOF")
    else:
        print(f"# Linux setup:")
        print(f"mkdir -p ~/.config/google-chrome/NativeMessagingHosts/")
        print(f"cat > ~/.config/google-chrome/NativeMessagingHosts/{app_name}.json << EOF")
        print(json.dumps({
            "name": app_name,
            "description": "Cognitive Desktop Agent",
            "path": host_path,
            "type": "stdio",
            "allowed_origins": [
                "chrome-extension://EXTENSION_ID_HERE/"
            ],
        }, indent=2))
        print("EOF")


def get_chrome_native_host_manifest(app_name: str, host_path: str) -> dict:
    """Generate the native messaging host manifest dict."""
    return {
        "name": app_name,
        "description": "Cognitive Desktop Agent",
        "path": host_path,
        "type": "stdio",
        "allowed_origins": [
            "chrome-extension://*/"
        ],
    }