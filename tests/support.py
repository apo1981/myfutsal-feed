"""Ayudas comunes de las pruebas del feed (US-007a). Solo biblioteca estandar, Python 3.9.

Las pruebas trabajan siempre sobre directorios temporales: no modifican el repositorio.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Callable, Dict, List, Optional

FEED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(FEED, "scripts")
FIXTURES = os.path.join(FEED, "tests", "fixtures", "contract")
sys.path.insert(0, SCRIPTS)

SEASON_ID = "2026-27"
BASE_SEASON = {
    "schemaVersion": 1,
    "seasonId": SEASON_ID,
    "competitionName": "Liga de prueba",
    "updatedAt": "2026-10-01T10:00:00Z",
    "teams": [
        {"id": "aaa", "name": "Equipo A", "shortName": "AAA", "crestURL": None},
        {"id": "bbb", "name": "Equipo B", "shortName": "BBB", "crestURL": None},
        {"id": "ccc", "name": "Equipo C", "shortName": "CCC", "crestURL": None},
    ],
    "phases": [{
        "id": "liga", "name": "Liga", "kind": "league", "order": 1,
        "rounds": [{
            "id": "liga-01", "number": 1, "name": "Jornada 1",
            "matches": [
                {"id": "m-01", "homeTeamId": "aaa", "awayTeamId": "bbb", "status": "finished",
                 "kickoff": {"date": "2026-10-03", "time": "2026-10-03T16:00:00Z"},
                 "score": {"home": 2, "away": 1}, "decidedBy": None, "note": None},
                {"id": "m-02", "homeTeamId": "bbb", "awayTeamId": "ccc", "status": "scheduled",
                 "kickoff": {"date": "2026-10-10", "time": None},
                 "score": None, "decidedBy": None, "note": None},
            ],
        }],
    }],
}
BASE_INDEX = {
    "schemaVersion": 1,
    "currentSeasonId": SEASON_ID,
    "seasons": [{"id": SEASON_ID, "name": "Temporada 2026/27", "url": "seasons/%s.json" % SEASON_ID}],
}


def write_json(path: str, document: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)


def season_with(mutate: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    season = copy.deepcopy(BASE_SEASON)
    if mutate:
        mutate(season)
    return season


def match_of(season: Dict[str, Any], identifier: str) -> Dict[str, Any]:
    for phase in season["phases"]:
        for rnd in phase["rounds"]:
            for match in rnd["matches"]:
                if match["id"] == identifier:
                    return match
    raise KeyError(identifier)


def build_public(root: str, season: Optional[Dict[str, Any]] = None, index: Optional[Dict[str, Any]] = None) -> str:
    """Escribe `<root>/public/v1/...` con el indice y la temporada dados. Devuelve public/."""
    public = os.path.join(root, "public")
    write_json(os.path.join(public, "v1", "index.json"), index or BASE_INDEX)
    write_json(os.path.join(public, "v1", "seasons", SEASON_ID + ".json"), season or BASE_SEASON)
    return public


def tool_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Entorno minimo y sin red: proxy inaccesible, solo loopback permitido."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
        "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }
    if os.environ.get("FEED_TOOLS_VENV"):
        env["FEED_TOOLS_VENV"] = os.environ["FEED_TOOLS_VENV"]
    env.update(extra or {})
    return env


def run_script(args: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd or FEED, env=env or tool_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


class InProcessResult:
    """Resultado de ejecutar un `main(argv)` en el propio proceso (mas rapido que un subproceso)."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_main(main: Callable[[List[str]], int], argv: List[str]) -> InProcessResult:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return InProcessResult(code, out.getvalue(), err.getvalue())


def output_of(result: Any) -> str:
    return result.stdout + result.stderr


class TempDirTestCase(unittest.TestCase):
    """Cada prueba recibe un directorio temporal propio, borrado al terminar."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="feed-test.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
