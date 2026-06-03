"""
Static file serving mixin for PromptHandler.
Handles serving HTML, CSS, and other static files with caching.
"""
from pathlib import Path
import re
from urllib.parse import unquote

from ._shared import logger


class StaticFilesMixin:
    """Mixin providing static file serving with caching."""

    _SAFE_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+$")

    def _read_file_bytes_cached(self, path: Path):
        """Read file bytes with caching based on mtime."""
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None

        key = str(path)
        with self._cache_lock:
            cached = self._html_bytes_cache.get(key)
            if cached and cached[0] == mtime:
                # Refresh recency (LRU-ish behavior on plain dict).
                self._html_bytes_cache[key] = self._html_bytes_cache.pop(
                    key, cached
                )
                return cached[1]

        data = path.read_bytes()
        with self._cache_lock:
            self._html_bytes_cache[key] = (mtime, data)
            self._prune_lru_cache(
                self._html_bytes_cache,
                self._html_bytes_cache_max_entries,
            )
        return data

    def _serve_static_file(self, filename, content_type="text/html; charset=utf-8"):
        """Serve a static file from the prompts directory with caching."""
        asset_name = self._validate_asset_filename(filename)
        if asset_name is None:
            self.send_error(400, "Invalid filename")
            return

        html_path = self.script_dir / "prompts" / asset_name
        if not html_path.exists():
            self.send_error(404, f"{asset_name} not found")
            return
        content = self._read_file_bytes_cached(html_path)
        if content is None:
            self.send_error(404, f"{asset_name} not found")
            return

        # If it is HTML, validate ticket or session cookie to prevent unauthorized access
        if content_type.startswith("text/html"):
            import os
            from urllib.parse import parse_qs, urlparse
            from . import _shared

            secret = os.environ.get("IMPRESSIONIST_API_SECRET", "")
            if secret:
                # 1. Check session cookie
                from http.cookies import SimpleCookie
                cookie_header = self.headers.get("Cookie", "")
                session_valid = False
                session_cookie_id = ""
                if cookie_header:
                    try:
                        cookie = SimpleCookie()
                        cookie.load(cookie_header)
                        if "ImpressionistSession" in cookie:
                            session_cookie_id = cookie["ImpressionistSession"].value
                            session_valid = _shared.validate_session(session_cookie_id)
                    except Exception:
                        pass

                new_session_id = None
                # 2. If cookie invalid, check single-use ticket
                if not session_valid:
                    parsed_url = urlparse(self.path)
                    query_params = parse_qs(parsed_url.query)
                    ticket = query_params.get("ticket", [""])[0]
                    if _shared.consume_ticket(ticket):
                        new_session_id = _shared.create_session()
                        session_valid = True
                    else:
                        logger.warning(f"Unauthorized HTML access blocked: {self.path} (invalid/missing ticket and session)")
                        self.send_error(401, "Unauthorized: Invalid or missing session credentials")
                        return

                # If we have a secret and the session is valid, inject it along with the global fetch interceptor
                script_inject = (
                    f'<script>'
                    f'window.IMPRESSIONIST_API_SECRET = "{secret}";'
                    f'(function() {{'
                    f'  const origFetch = window.fetch;'
                    f'  window.fetch = function(input, init) {{'
                    f'    init = init || {{}};'
                    f'    init.headers = init.headers || {{}};'
                    f'    if (init.headers instanceof Headers) {{'
                    f'      init.headers.set("X-API-Secret", window.IMPRESSIONIST_API_SECRET);'
                    f'    }} else if (Array.isArray(init.headers)) {{'
                    f'      let found = false;'
                    f'      for (let i = 0; i < init.headers.length; i++) {{'
                    f'        if (init.headers[i][0].toLowerCase() === "x-api-secret") {{'
                    f'          init.headers[i][1] = window.IMPRESSIONIST_API_SECRET;'
                    f'          found = true; break;'
                    f'        }}'
                    f'      }}'
                    f'      if (!found) init.headers.push(["X-API-Secret", window.IMPRESSIONIST_API_SECRET]);'
                    f'    }} else {{'
                    f'      init.headers["X-API-Secret"] = window.IMPRESSIONIST_API_SECRET;'
                    f'    }}'
                    f'    return origFetch(input, init);'
                    f'  }};'
                    f'}})();'
                    f'</script>'
                )
                try:
                    content_str = content.decode("utf-8")
                    content_str = content_str.replace("<head>", f"<head>{script_inject}", 1)
                    content = content_str.encode("utf-8")
                except Exception as e:
                    logger.warning(f"Failed to inject secret into HTML: {e}")

                # Serve the HTML page, setting the session cookie if newly generated
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                if new_session_id:
                    self.send_header(
                        "Set-Cookie",
                        f"ImpressionistSession={new_session_id}; HttpOnly; SameSite=Lax; Path=/",
                    )
                self.send_header("Content-Length", len(content))
                self._set_cache_headers(300)
                self.end_headers()
                self.wfile.write(content)
                return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(content))
        self._set_cache_headers(300)
        self.end_headers()
        self.wfile.write(content)

    def _validate_asset_filename(self, filename):
        """Validate prompt asset names and reject traversal attempts."""
        if not isinstance(filename, str):
            return None
        candidate = unquote(filename).strip()
        if not candidate or "\x00" in candidate or "/" in candidate or "\\" in candidate:
            return None
        if not self._SAFE_ASSET_NAME_RE.fullmatch(candidate):
            return None
        return candidate

    def serve_html(self):
        """Serve prompt_manager.html."""
        self._serve_static_file("prompt_manager.html")

    def serve_context_manager(self):
        """Serve context_manager.html."""
        self._serve_static_file("context_manager.html")

    def serve_html_file(self, filename):
        """Serve any HTML file from the prompts directory."""
        self._serve_static_file(filename)

    def serve_chat_html(self):
        """Serve chat.html."""
        self._serve_static_file("chat.html")

    def serve_css(self, path):
        """Serve CSS files from the prompts directory."""
        # Extract filename from path (e.g., "/_design-system-core.css" -> "_design-system-core.css")
        filename = self._validate_asset_filename(path.lstrip("/"))
        if filename is None:
            self.send_error(400, "Invalid filename")
            return
        if not filename.lower().endswith(".css"):
            self.send_error(400, "Invalid CSS filename")
            return

        css_path = self.script_dir / "prompts" / filename
        content = self._read_file_bytes_cached(css_path)
        if content is None:
            self.send_error(404, f"{filename} not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self._set_cache_headers(600)
        self.end_headers()
        self.wfile.write(content)

    def serve_js(self, path):
        """Serve JavaScript files from the prompts directory."""
        filename = self._validate_asset_filename(path.lstrip("/"))
        if filename is None:
            self.send_error(400, "Invalid filename")
            return
        if not filename.lower().endswith(".js"):
            self.send_error(400, "Invalid JS filename")
            return

        js_path = self.script_dir / "prompts" / filename
        content = self._read_file_bytes_cached(js_path)
        if content is None:
            self.send_error(404, f"{filename} not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self._set_cache_headers(600)
        self.end_headers()
        self.wfile.write(content)
