"""Local-only web GUI for searching a search database built by oath.search.

Binds to 127.0.0.1 only. Nothing here sends data anywhere.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from oath.search import list_chats, search

INDEX_HTML = Path(__file__).parent / "static" / "index.html"


def _make_handler(db_path: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet

        def _json(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path == "/":
                html = INDEX_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif parsed.path == "/api/chats":
                self._json([{"name": name, "count": n} for name, n in list_chats(db_path)])
            elif parsed.path == "/api/search":
                q = qs.get("q", [""])[0]
                chat = qs.get("chat", [""])[0] or None
                self._json([asdict(h) for h in search(db_path, q, chat)])
            else:
                self._json({"error": "not found"}, status=404)

    return Handler


def serve(db_path: Path, port: int = 8765) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"no search database at {db_path} -- build one first")
    server = HTTPServer(("127.0.0.1", port), _make_handler(db_path))
    print(f"search GUI running at http://127.0.0.1:{port}  (local only, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
