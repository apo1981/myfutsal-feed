"""Comprobacion posterior al despliegue (US-007a, criterio 6; ADR-004 §4).

Pide `index.json` y la temporada vigente a la URL base y comprueba: 200, `Content-Type` JSON,
`ETag`, `304` ante `If-None-Match` y que el `updatedAt` servido es el esperado (el de `public/`).
Solo `updatedAt` se reintenta, porque la CDN puede servir la version anterior unos minutos;
el resto de fallos son inmediatos. Solo biblioteca estandar, compatible con Python 3.9.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 12 * 60.0
REQUEST_TIMEOUT_SECONDS = 30.0
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
JSON_CONTENT_TYPE = re.compile(r"^application/(.+\+)?json\s*(;.*)?$", re.IGNORECASE)


class SmokeFailure(Exception):
    """Fallo de la comprobacion; el mensaje dice cual y por que."""


Response = Tuple[int, Dict[str, str], bytes]


def fetch(url: str, headers: Optional[Dict[str, str]] = None) -> Response:
    """GET sin seguir redirecciones. Devuelve (estado, cabeceras en minusculas, cuerpo)."""
    request = urllib.request.Request(url, headers=headers or {})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, lowercase(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, lowercase(error.headers.items()), error.read()
    except (urllib.error.URLError, OSError) as error:
        raise SmokeFailure("no se pudo pedir %s: %s" % (url, error))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def lowercase(items: Any) -> Dict[str, str]:
    return {key.lower(): value for key, value in items}


def check_base_url(base: str) -> str:
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme == "https" and parsed.hostname:
        return base.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS:
        return base.rstrip("/")
    raise SmokeFailure("la URL base debe ser https (http solo para %s): %s" % (" o ".join(LOOPBACK_HOSTS), base))


def fetch_json_document(url: str) -> Tuple[Dict[str, str], Any]:
    """200 con `Content-Type` JSON, `ETag` y cuerpo JSON. Devuelve (cabeceras, documento)."""
    status, headers, body = fetch(url)
    if status != 200:
        raise SmokeFailure("%s respondio %d y se esperaba 200" % (url, status))
    content_type = headers.get("content-type", "")
    if not JSON_CONTENT_TYPE.match(content_type):
        raise SmokeFailure("%s: Content-Type %r no es JSON" % (url, content_type))
    if not headers.get("etag"):
        raise SmokeFailure("%s: falta la cabecera ETag" % url)
    try:
        return headers, json.loads(body.decode("utf-8"))
    except ValueError:
        raise SmokeFailure("%s: el contenido no es JSON" % url)


def check_revalidation(url: str, etag: str) -> None:
    status, _headers, _body = fetch(url, {"If-None-Match": etag})
    if status != 304:
        raise SmokeFailure("%s: con If-None-Match %s respondio %d y se esperaba 304" % (url, etag, status))


def read_expectation(public_dir: str) -> Tuple[str, str]:
    """(temporada vigente, updatedAt esperado) segun los ficheros locales de public/."""
    with open(os.path.join(public_dir, "v1", "index.json"), "r", encoding="utf-8") as handle:
        season_id = json.load(handle)["currentSeasonId"]
    with open(os.path.join(public_dir, "v1", "seasons", season_id + ".json"), "r", encoding="utf-8") as handle:
        return season_id, json.load(handle)["updatedAt"]


def run_smoke(base: str, season_id: str, expected: str, interval: float, timeout: float,
              clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
              log: Callable[[str], None] = print) -> float:
    """Devuelve los segundos hasta servir la version esperada; lanza SmokeFailure si falla."""
    index_url = base + "/v1/index.json"
    season_url = "%s/v1/seasons/%s.json" % (base, season_id)
    started = clock()
    attempt = 0
    while True:
        attempt += 1
        index_headers, _index = fetch_json_document(index_url)
        season_headers, season = fetch_json_document(season_url)
        served = season.get("updatedAt") if isinstance(season, dict) else None
        if served == expected:
            break
        elapsed = clock() - started
        log("intento %d: updatedAt servido %r, esperado %r (%.0f s)" % (attempt, served, expected, elapsed))
        if elapsed + interval > timeout:
            raise SmokeFailure("updatedAt servido %r no coincide con el esperado %r tras %.0f s" % (served, expected, elapsed))
        sleep(interval)
    check_revalidation(index_url, index_headers["etag"])
    check_revalidation(season_url, season_headers["etag"])
    return clock() - started


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprobacion posterior al despliegue (US-007a)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("--base", required=True, help="URL base con ruta, p. ej. https://usuario.github.io/myfutsal-feed")
    parser.add_argument("--public", help="directorio public/ con lo desplegado (por defecto <feed>/public)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        base = check_base_url(args.base)
        season_id, expected = read_expectation(args.public or os.path.join(args.feed, "public"))
        elapsed = run_smoke(base, season_id, expected, args.interval, args.timeout)
    except SmokeFailure as failure:
        print("FALLO smoke-check: %s" % failure, file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print("FALLO smoke-check: no se pudo leer lo esperado en public/: %s: %s" % (type(error).__name__, error), file=sys.stderr)
        return 1
    print("smoke-check correcto: version %s servida tras %.1f s" % (expected, elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
