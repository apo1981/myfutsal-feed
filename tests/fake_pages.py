"""Servidor de prueba que imita lo que el smoke check necesita de GitHub Pages (US-007a, criterio 6).

Escucha solo en 127.0.0.1, en un puerto libre que elige el sistema. Sirve documentos bajo un
prefijo de ruta (como un sitio de proyecto), con `ETag` y `304` ante `If-None-Match`.
Para simular la propagacion de la CDN, cada documento tiene una lista de versiones: cada `200`
sirve la siguiente, y la ultima se repite. Solo biblioteca estandar, Python 3.9.
"""
from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple

LOOPBACK = "127.0.0.1"
JSON_TYPE = "application/json; charset=utf-8"


def etag_of(body: bytes) -> str:
    return '"%s"' % hashlib.sha256(body).hexdigest()[:16]


class FakePages:
    def __init__(self, prefix: str = "/myfutsal-feed") -> None:
        self.prefix = prefix
        self.versions = {}  # type: Dict[str, List[bytes]]
        self.served = {}  # type: Dict[str, int]
        self.requests = []  # type: List[Tuple[str, str, Dict[str, str]]]
        self.send_etag = True
        self.honor_conditional = True
        self.content_type = JSON_TYPE
        self.redirect_to = None  # type: Optional[str]
        self._lock = threading.Lock()
        self._server = HTTPServer((LOOPBACK, 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- API de las pruebas --------------------------------------------------
    @property
    def address(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def base_url(self, host: str = LOOPBACK) -> str:
        return "http://%s:%d%s" % (host, self.port, self.prefix)

    def publish(self, path: str, *versions: bytes) -> None:
        """`path` relativo a la base, p. ej. `/v1/index.json`. Cada 200 sirve la version siguiente."""
        self.versions[self.prefix + path] = list(versions)
        self.served[self.prefix + path] = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    # -- Servidor ------------------------------------------------------------
    def _handler(self) -> type:
        pages = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # silencioso
                return

            def do_GET(self) -> None:
                pages._respond(self)

        return Handler

    def _respond(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            headers = {key.lower(): value for key, value in handler.headers.items()}
            self.requests.append(("GET", handler.path, headers))
            versions = self.versions.get(handler.path)
            if self.redirect_to:
                handler.send_response(301)
                handler.send_header("Location", self.redirect_to)
                handler.end_headers()
                return
            if versions is None:
                handler.send_response(404)
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
            position = min(self.served[handler.path], len(versions) - 1)
            body = versions[position]
            conditional = headers.get("if-none-match")
            if self.honor_conditional and conditional and conditional == etag_of(body):
                handler.send_response(304)
                handler.send_header("ETag", etag_of(body))
                handler.end_headers()
                return
            self.served[handler.path] += 1
            handler.send_response(200)
            handler.send_header("Content-Type", self.content_type)
            if self.send_etag:
                handler.send_header("ETag", etag_of(body))
            handler.send_header("Cache-Control", "max-age=600")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
