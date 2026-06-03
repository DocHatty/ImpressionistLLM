#!/usr/bin/env python3
"""
Bulletproof Asynchronous HTTP server for Prompt Manager.

Combines native asyncio for high-performance, non-blocking networking with
safe worker thread dispatch for synchronous handlers to preserve business logic.
"""

import asyncio
import atexit
import os
import signal
import socket
import sys
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from io import BytesIO
from pathlib import Path

try:
    from .server._shared import json, logger
    from .server.handler import PromptHandler
    from .server.vendor import (
        _configure_openrouter_sdk_logger,
        _ensure_openrouter_python_sdk,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from server._shared import json, logger
    from server.handler import PromptHandler
    from server.vendor import (
        _configure_openrouter_sdk_logger,
        _ensure_openrouter_python_sdk,
    )

# Module-level handles for graceful shutdown
_server_instance = None
_event_loop = None
_shutdown_lock = threading.Lock()
_shutdown_done = False
class AsyncIOStreamWriterWrapper:
    """Thread-safe file-like wrapper that schedules synchronous writes onto the asyncio loop."""

    def __init__(self, loop, writer):
        self.loop = loop
        self.writer = writer
        self.lock = threading.Lock()
        self.futures = []

    def write(self, data):
        if not data:
            return

        async def _write_and_drain():
            self.writer.write(data)
            await self.writer.drain()

        try:
            future = asyncio.run_coroutine_threadsafe(_write_and_drain(), self.loop)
            with self.lock:
                self.futures.append(future)
        except Exception:
            pass

    def flush(self):
        pass

    def close(self):
        pass


class MockSocket:
    """Socket-like object that emulates makefile interface for StreamRequestHandler."""

    def __init__(self, rfile, wfile, client_address):
        self._rfile = rfile
        self._wfile = wfile
        self.client_address = client_address

    def makefile(self, mode, bufsize=0):
        if "r" in mode:
            return self._rfile
        else:
            return self._wfile

    def getpeername(self):
        return self.client_address

    def getsockname(self):
        return ("127.0.0.1", 58080)

    def sendall(self, data):
        if hasattr(self._wfile, "write"):
            self._wfile.write(data)

    def send(self, data):
        if hasattr(self._wfile, "write"):
            self._wfile.write(data)
            return len(data)
        return 0

    def settimeout(self, timeout):
        pass

    def fileno(self):
        return -1

    def close(self):
        pass


async def handle_client(reader, writer, loop, script_dir):
    """Handle an incoming HTTP connection asynchronously on the event loop."""
    client_address = writer.get_extra_info("peername") or ("127.0.0.1", 0)

    try:
        # 1. Asynchronously read HTTP request headers
        header_bytes = b""
        while True:
            line = await reader.readline()
            if not line:
                break
            header_bytes += line
            if line == b"\r\n" or line == b"\n":
                break

        if not header_bytes:
            writer.close()
            return

        # 2. Parse Content-Length to buffer the request body
        content_length = 0
        for line in header_bytes.split(b"\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                if k.strip().lower() == b"content-length":
                    try:
                        content_length = int(v.strip())
                    except ValueError:
                        content_length = 0
                    break

        # 3. Asynchronously read request body
        body_bytes = b""
        if content_length > 0:
            try:
                body_bytes = await reader.readexactly(content_length)
            except asyncio.IncompleteReadError as e:
                body_bytes = e.partial

        # 4. Package request streams and wrap inside emulated socket
        rfile = BytesIO(header_bytes + body_bytes)
        wfile = AsyncIOStreamWriterWrapper(loop, writer)
        mock_socket = MockSocket(rfile, wfile, client_address)

        # 5. Dispatch the synchronous PromptHandler routing onto a worker thread
        # This keeps the main event loop completely non-blocking.
        def _dispatch():
            try:
                # PromptHandler constructor automatically invokes setup, handle, and finish
                handler = PromptHandler(mock_socket, client_address, _server_instance)
            except Exception as e:
                logger.error(f"Error handling request from {client_address}: {e}")
        await asyncio.to_thread(_dispatch)

        # Wait for all async writes/drains to complete thread-safely
        with wfile.lock:
            futures_copy = list(wfile.futures)
        for f in futures_copy:
            try:
                await asyncio.wrap_future(f)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Client handler exception: {e}")
    finally:
        # 6. Asynchronously drain and close the connection
        try:
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def _graceful_shutdown(reason="unknown"):
    """Tell the HTTP server to stop accepting new requests and exit cleanly."""
    global _shutdown_done
    with _shutdown_lock:
        if _shutdown_done:
            return
        _shutdown_done = True

    logger.info(f"========================================")
    logger.info(f"Graceful shutdown initiated (reason={reason})")
    logger.info(f"========================================")

    server = _server_instance
    if server is not None:
        try:
            server.close()
        except Exception as e:
            logger.warning(f"server.close() raised: {e}")

    # Close shared httpx client connection pool
    try:
        from server.openrouter_modules.openrouter_client_helpers import OpenRouterClientHelpersMixin
        if OpenRouterClientHelpersMixin._httpx_client is not None:
            OpenRouterClientHelpersMixin._httpx_client.close()
            logger.info("Closed shared HTTPX client connection pool")
    except Exception as e:
        logger.warning(f"Failed to close HTTPX client: {e}")

    # Close shared cached OpenRouter SDK client instances
    try:
        from server.openrouter_modules.openrouter_client_helpers import OpenRouterClientHelpersMixin
        with OpenRouterClientHelpersMixin._sdk_client_lock:
            for cache_key, sdk_client in list(OpenRouterClientHelpersMixin._sdk_client_cache.items()):
                try:
                    real_client = getattr(sdk_client, "client", sdk_client)
                    real_client.close()
                except Exception:
                    pass
            OpenRouterClientHelpersMixin._sdk_client_cache.clear()
            logger.info("Closed and cleared shared cached OpenRouter SDK client instances")
    except Exception as e:
        logger.warning(f"Failed to close cached OpenRouter SDK clients: {e}")

    # Tell all chat stream workers to cancel
    try:
        for cancel_event in _collect_active_cancel_events():
            try:
                cancel_event.set()
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to cancel in-flight chat streams: {e}")

    # Stop the event loop
    loop = _event_loop
    if loop is not None and loop.is_running():
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception as e:
            logger.warning(f"Failed to stop event loop: {e}")

    logger.info("Graceful shutdown complete")


def _collect_active_cancel_events():
    """Return every threading.Event cancel flag currently registered with the handler."""
    out = []
    try:
        with PromptHandler._chat_run_lock:
            for run in PromptHandler._chat_runs.values():
                ev = run.get("cancel") if isinstance(run, dict) else None
                if ev is not None:
                    out.append(ev)
    except Exception:
        pass
    return out


def _install_signal_handlers():
    """Install signal handlers so taskkill (no /F) and console close events shut us down cleanly."""

    def _handler(signum, frame):
        try:
            name = signal.Signals(signum).name
        except Exception:
            name = str(signum)
        _graceful_shutdown(reason=f"signal {name}")
        try:
            sys.exit(0)
        except SystemExit:
            raise

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass

    atexit.register(_graceful_shutdown, reason="atexit")


def kill_existing_server(port=58080):
    """Kill any existing server on the target port."""
    import subprocess
    import sys as _os_sys

    if _os_sys.platform != "win32":
        try:
            # Use lsof to get the PID using the port
            result = subprocess.run(
                ["lsof", "-t", f"-i:{port}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                if pid.isdigit() and int(pid) != os.getpid():
                    logger.info(
                        f"Killing existing server on port {port} (PID {pid})"
                    )
                    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
                    import time
                    time.sleep(0.2)
        except Exception as e:
            logger.warning(f"Could not check/kill existing server on macOS/Unix: {e}")
        return

    try:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                result = s.connect_ex(("127.0.0.1", port))
                if result != 0:
                    return
        except Exception:
            pass

        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for line in result.stdout.split("\n"):
            if f":{port}" in line:
                parts = line.split()
                if len(parts) >= 2:
                    local_addr = parts[1]
                    if ":" in local_addr:
                        addr_parts = local_addr.split(":")
                        if addr_parts[-1] == str(port):
                            pid = parts[-1]
                            if pid == "4":
                                logger.info(
                                    f"Skipping PID 4 (System/HTTP.sys) on port {port}"
                                )
                                continue
                            if pid.isdigit() and int(pid) != os.getpid():
                                logger.info(
                                    f"Killing existing server on port {port} (PID {pid})"
                                )
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", pid],
                                    capture_output=True,
                                    timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW
                                    if sys.platform == "win32"
                                    else 0,
                                )
                                import time

                                time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Could not check for existing server: {e}")


def find_port(start=58080):
    """Find first available port."""
    for port in range(start, start + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    continue
        except OSError:
            pass

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


def _default_script_dir():
    """Return the repository root based on this server file location."""
    return Path(__file__).parent.parent.parent.resolve()


def _resolve_script_dir():
    """Resolve the app root and guard against split/invalid launch arguments."""
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).resolve()
        if (candidate / "prompts").is_dir():
            return candidate

        fallback = _default_script_dir()
        logger.warning(
            f"Ignoring invalid script directory argument {candidate}; "
            f"falling back to {fallback}"
        )
        return fallback

    return _default_script_dir()


def _resolve_temp_dir(script_dir):
    """Resolve the temp directory while avoiding relative fragments from split args."""
    if len(sys.argv) > 2:
        candidate = Path(sys.argv[2])
        if candidate.is_absolute():
            return candidate.resolve()

        logger.warning(
            f"Ignoring non-absolute temp directory argument {candidate}; "
            "using app temp directory"
        )

    return script_dir / "temp"


def _eager_warmup(api_key_provider):
    """Eliminate first-call lag by paying every cold-start cost up front, in parallel."""
    try:
        _ensure_openrouter_python_sdk()
        logger.info("Warmup: OpenRouter SDK import complete")
    except Exception as e:
        logger.warning(f"Warmup: SDK import failed: {e}")
        return

    def _warm_tls():
        try:
            import urllib.request

            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                method="HEAD",
                headers={"User-Agent": "ImpressionistLLM/warmup"},
            )
            with urllib.request.urlopen(req, timeout=4.0):
                pass
            logger.info("Warmup: OpenRouter TLS handshake complete")
        except Exception as e:
            logger.debug(f"Warmup: TLS warm failed (non-fatal): {e}")

    threading.Thread(target=_warm_tls, name="WarmupTLS", daemon=True).start()

    def _warm_catalog():
        try:
            api_key = api_key_provider()
            if not api_key:
                logger.debug("Warmup: no API key configured, skipping catalog warm")
                return
            dummy = type("_WarmupCtx", (), {})()
            dummy.get_api_key = lambda: api_key
            getter = getattr(PromptHandler, "_get_models_cached", None)
            if callable(getter):
                try:
                    getter(dummy, api_key)
                    logger.info("Warmup: OpenRouter model catalog primed")
                except Exception as e:
                    logger.debug(f"Warmup: catalog prefetch failed (non-fatal): {e}")
        except Exception as e:
            logger.debug(f"Warmup: catalog warm setup failed: {e}")

    threading.Thread(target=_warm_catalog, name="WarmupCatalog", daemon=True).start()


def _monitor_parent_process():
    """Poll parent PID and shut down if parent process dies (PID becomes 1 on Unix)."""
    import time
    logger.info("Parent process monitor started")
    while not _shutdown_done:
        try:
            if os.getppid() == 1:
                logger.warning("Parent process died (inherited by PID 1). Initiating automatic shutdown.")
                _graceful_shutdown(reason="parent_process_died")
                os._exit(0)
        except Exception:
            pass
        time.sleep(1.0)


def main():
    """Main entry point for the server."""
    global _server_instance, _event_loop

    # Set process priority to BelowNormal on Windows to keep GUI operations smooth
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000
            )
        except Exception:
            pass

    logger.info("========================================")
    logger.info("ImpressionistLLM HTTP Server starting (async)...")
    logger.info("========================================")

    script_dir = _resolve_script_dir()
    logger.info(f"Script directory: {script_dir}")

    PromptHandler.script_dir = script_dir
    _configure_openrouter_sdk_logger(script_dir)

    default_port = 58080
    logger.info("Finding available port...")
    port = find_port(default_port)
    if not port:
        logger.error("Could not find available port")
        sys.exit(1)

    logger.info(f"Using port: {port}")

    # Write port info
    temp_dir = _resolve_temp_dir(script_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / "server_port.txt").write_text(str(port))
    (temp_dir / "server_info.json").write_text(
        json.dumps(
            {
                "port": port,
                "status": "running",
                "script_dir": str(script_dir),
                "pid": os.getpid(),
            }
        )
    )
    logger.info(f"Wrote port info to {temp_dir}")

    _install_signal_handlers()

    # Start parent process monitor daemon thread
    threading.Thread(
        target=_monitor_parent_process,
        daemon=True,
        name="ParentMonitor",
    ).start()

    # Initialize the asyncio event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _event_loop = loop

    # Start the async TCP socket HTTP server
    async_server = loop.run_until_complete(
        asyncio.start_server(
            lambda r, w: handle_client(r, w, loop, script_dir),
            "127.0.0.1",
            port,
        )
    )
    _server_instance = async_server
    logger.info(f"Server started asynchronously on http://127.0.0.1:{port}")
    logger.info("Server ready to accept connections")

    # Set mock server attribute for handler compatibility
    class MockServer:
        def __init__(self, server_port):
            self.server_port = server_port

    _server_instance.server_port = port

    # Eager warmup in background thread
    def _api_key_provider():
        try:
            inst = type("_ProviderCtx", (), {})()
            getter = getattr(PromptHandler, "get_api_key", None)
            if callable(getter):
                return getter(inst)
        except Exception:
            return ""
        return ""

    threading.Thread(
        target=_eager_warmup,
        args=(_api_key_provider,),
        daemon=True,
        name="Warmup",
    ).start()

    logger.info("========================================")
    logger.info("Server initialization complete")
    logger.info("========================================")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        _graceful_shutdown(reason="KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Event loop raised: {e}")
        _graceful_shutdown(reason=f"unhandled exception: {e}")
        raise
    finally:
        loop.close()
        logger.info("Event loop closed")


if __name__ == "__main__":
    main()
