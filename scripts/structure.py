"""Estructura del repositorio del feed (US-007a, criterio 1; US-007b1, criterio 7c).

Lista permitida de `public/`, ficheros obligatorios, ausencia de secretos y de `_headers`,
repositorio Git independiente, remoto esperado e identidad no personal (D-067, D-074(2)).
Solo biblioteca estandar, compatible con Python 3.9. No escribe nada.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional

SEASON_FILE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}\.json$")
ALLOWED_INDEX = os.path.join("v1", "index.json")
SEASONS_DIR = os.path.join("v1", "seasons")
PLACEHOLDER_ID = "pendiente"
EXAMPLE_TEAMS = os.path.join("examples", "initial-load", "equipos.csv")

REQUIRED_FILES = [
    os.path.join("public", "v1", "index.json"),
    os.path.join("schema", "league-feed-v1.season.schema.json"),
    os.path.join("schema", "league-feed-v1.index.schema.json"),
    os.path.join("schema", "SHA256SUMS"),
    EXAMPLE_TEAMS,
    os.path.join(".github", "workflows", "publish.yml"),
    os.path.join("policy", "workflow-policy.schema.json"),
    "requirements-ci.txt",
    "RUNBOOK.md",
    os.path.join("scripts", "verify"),
    os.path.join("scripts", "validate-schema"),
    os.path.join("scripts", "check-invariants"),
    os.path.join("scripts", "check-updated-at"),
    os.path.join("scripts", "convert-csv"),
    os.path.join("scripts", "check-initial-load"),
    os.path.join("scripts", "check-allowlist"),
    os.path.join("scripts", "check-structure"),
    os.path.join("scripts", "check-workflow"),
    os.path.join("scripts", "check-runbook"),
    os.path.join("scripts", "smoke-check"),
    os.path.join("scripts", "prepare-gate-rehearsal"),
]

SECRET_NAME_PATTERNS = [".env*", "*.pem", "*.key", "id_*"]
# Los patrones se parten en dos para que este fichero no se detecte a si mismo.
SECRET_CONTENT_MARKERS = [b"gh" + b"p_", b"github" + b"_pat_", b"-----" + b"BEGIN", b"AK" + b"IA"]
# Rutas locales de un Mac o de un Linux: delatan el nombre del operador. Se construyen por partes para que este
# fichero no se detecte a si mismo.
LOCAL_PATH_MARKERS = [b"/" + b"Users/", b"/" + b"home/"]
IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__"}

# Identidad (D-074(2)). La direccion `noreply` del operador no se escribe aqui: es la identidad local del
# repositorio (`git config --local user.email`) y debe tener esta forma. El resto de direcciones permitidas
# en el historial son fijas.
NON_PERSONAL_EMAIL = "feed@example.invalid"
WEB_COMMITTER_EMAIL = "noreply@github.com"
OPERATOR_NOREPLY_PATTERN = re.compile(r"^[0-9]+\+[^@\s]+@users\.noreply\.github\.com$")
REMOTE_NAME = "origin"
# Solo HTTPS sin credenciales incrustadas (un token en la URL acabaria en .git/config).
REMOTE_URL_PATTERN = re.compile(r"^https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/myfutsal-feed(?:\.git)?$")


def walk_files(root: str, skip_ignored: bool = True) -> List[str]:
    """Rutas relativas de todos los ficheros bajo `root` (sin .git, .venv ni __pycache__)."""
    found = []
    for directory, subdirs, names in os.walk(root):
        if skip_ignored:
            subdirs[:] = [d for d in subdirs if d not in IGNORED_DIRECTORIES]
        for name in names:
            found.append(os.path.relpath(os.path.join(directory, name), root))
    return sorted(found)


# --------------------------------------------------------------------------
# Comprobaciones de fichero
# --------------------------------------------------------------------------

def allowlist_problems(public_dir: str) -> List[str]:
    if not os.path.isdir(public_dir):
        return ["public/ no existe"]
    problems = []
    for relative in walk_files(public_dir, skip_ignored=False):
        directory, name = os.path.split(relative)
        if relative == ALLOWED_INDEX:
            continue
        if directory == SEASONS_DIR and SEASON_FILE_PATTERN.match(name):
            continue
        problems.append("public/%s no esta en la lista permitida (solo v1/index.json y v1/seasons/AAAA-AA.json)" % relative)
    return problems


def missing_files(feed: str) -> List[str]:
    problems = ["falta %s" % relative for relative in REQUIRED_FILES if not os.path.isfile(os.path.join(feed, relative))]
    for directory in ("valid", "invalid"):
        if not os.path.isdir(os.path.join(feed, "tests", "fixtures", "contract", directory)):
            problems.append("falta tests/fixtures/contract/%s/" % directory)
    seasons = os.path.join(feed, "public", SEASONS_DIR)
    if not os.path.isdir(seasons) or not any(SEASON_FILE_PATTERN.match(n) for n in os.listdir(seasons)):
        problems.append("falta public/v1/seasons/<id>.json para la temporada")
    return problems


def headers_problems(feed: str) -> List[str]:
    return ["%s: GitHub Pages no admite _headers" % relative for relative in walk_files(feed) if os.path.basename(relative) == "_headers"]


def secret_problems(feed: str) -> List[str]:
    problems = []
    for relative in walk_files(feed):
        name = os.path.basename(relative)
        if any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_NAME_PATTERNS):
            problems.append("%s: el nombre parece el de un secreto" % relative)
        with open(os.path.join(feed, relative), "rb") as handle:
            content = handle.read()
        for marker in SECRET_CONTENT_MARKERS:
            if marker in content:
                problems.append("%s: el contenido parece un secreto (contiene %s)" % (relative, marker.decode("ascii")))
    return problems


def published_files(feed: str) -> List[str]:
    """Ficheros versionados (`git ls-files`): lo que se publica; sin git, recorre el arbol (S-1 de US-007b1)."""
    result = subprocess.run(["git", "-C", feed, "ls-files", "-z"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        return walk_files(feed)
    names = result.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted(name for name in names if name and os.path.isfile(os.path.join(feed, name)))


def local_path_problems(feed: str) -> List[str]:
    """Ningun fichero versionado puede llevar una ruta local (B-1 de la revision de US-007b1): nombra fichero:linea."""
    problems = []
    for relative in published_files(feed):
        with open(os.path.join(feed, relative), "rb") as handle:
            for number, line in enumerate(handle.read().splitlines(), start=1):
                for marker in LOCAL_PATH_MARKERS:
                    if marker in line:
                        problems.append("%s:%d: contiene una ruta local (%s); usa una ruta relativa" % (relative, number, marker.decode("ascii")))
    return problems


def placeholder_problems(feed: str) -> List[str]:
    """Hasta US-007b `public/` no lleva equipos ni resultados inventados: solo el marcador `pendiente`."""
    problems = []
    seasons = os.path.join(feed, "public", SEASONS_DIR)
    if not os.path.isdir(seasons):
        return problems
    for name in sorted(os.listdir(seasons)):
        with open(os.path.join(seasons, name), "r", encoding="utf-8") as handle:
            season = json.load(handle)
        extra_teams = [t.get("id") for t in season.get("teams", []) if t.get("id") != PLACEHOLDER_ID]
        if extra_teams:
            problems.append("public/v1/seasons/%s: equipos que no son el marcador %r: %s" % (name, PLACEHOLDER_ID, extra_teams))
        for phase in season.get("phases", []):
            for rnd in phase.get("rounds", []):
                if rnd.get("matches"):
                    problems.append("public/v1/seasons/%s: la ronda %s tiene partidos" % (name, rnd.get("id")))
    return problems


def example_team_ids(feed: str) -> List[str]:
    with open(os.path.join(feed, EXAMPLE_TEAMS), "r", encoding="utf-8", newline="") as handle:
        return [row["id"] for row in csv.DictReader(handle)]


def example_id_problems(feed: str) -> List[str]:
    """S-3 de la revision de US-007a2: ningun `id` de equipo de los datos de ejemplo puede estar en `public/`.

    Mantiene A4 (sin datos inventados en lo publicado) durante toda la vida del feed.
    """
    seasons = os.path.join(feed, "public", SEASONS_DIR)
    if not os.path.isfile(os.path.join(feed, EXAMPLE_TEAMS)) or not os.path.isdir(seasons):
        return []  # lo primero ya lo senala `ficheros-obligatorios`
    examples = set(example_team_ids(feed))
    problems = []
    for name in sorted(os.listdir(seasons)):
        with open(os.path.join(seasons, name), "r", encoding="utf-8") as handle:
            season = json.load(handle)
        for team in season.get("teams", []):
            if team.get("id") in examples:
                problems.append("public/v1/seasons/%s: el equipo %r es un dato de ejemplo (%s)" % (name, team.get("id"), EXAMPLE_TEAMS))
    return problems


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------

def git(directory: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", directory] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def independent_repo_problems(feed: str) -> List[str]:
    dot_git = os.path.join(feed, ".git")
    if os.path.islink(dot_git) or not os.path.isdir(dot_git):
        return [".git no es un directorio propio (el feed debe ser un repositorio independiente, no submodulo ni gitlink)"]
    top = git(feed, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(feed):
        return ["la raiz del repositorio Git no es %s" % feed]
    return []


def app_side_problems(feed: str, app_root: str) -> List[str]:
    name = os.path.basename(os.path.realpath(feed))
    problems = []
    if git(app_root, "check-ignore", "-q", name + "/").returncode != 0:
        problems.append("%s/ no esta en el .gitignore de la app" % name)
    tracked = git(app_root, "ls-files", "--stage", "--", name)
    if tracked.stdout.strip():
        problems.append("el repositorio de la app versiona %s (gitlink o ficheros); debe estar ignorado" % name)
    return problems


def local_config(feed: str, key: str) -> str:
    return git(feed, "config", "--local", "--get", key).stdout.strip()


def remote_problems(feed: str) -> List[str]:
    """Sin remoto, o solo `origin` apuntando a `https://github.com/<usuario>/myfutsal-feed(.git)` sin credenciales."""
    listing = git(feed, "remote", "-v")
    if listing.returncode != 0:
        return ["no se pudo leer el remoto: %s" % listing.stderr.strip()]
    problems = []
    for line in sorted(set(listing.stdout.splitlines())):
        fields = line.split()
        name, url = fields[0], fields[1] if len(fields) > 1 else ""
        if name != REMOTE_NAME:
            problems.append("remoto inesperado %r (solo se admite %r)" % (name, REMOTE_NAME))
        elif not REMOTE_URL_PATTERN.match(url):
            problems.append("la URL del remoto %r no es https://github.com/<usuario>/myfutsal-feed(.git) sin credenciales" % name)
    return problems


def identity_problems(feed: str) -> List[str]:
    """Identidad local no personal y direcciones del historial dentro de la lista permitida (D-074(2)).

    La identidad local es `feed@example.invalid` mientras no haya remoto y la `noreply` del operador en cuanto
    exista `origin`. El historial solo puede contener esa identidad local, `feed@example.invalid` y
    `noreply@github.com` (committer de las ediciones web).
    """
    configured = local_config(feed, "user.email")
    if not local_config(feed, "user.name") or not configured:
        return ["falta la identidad local (git config --local user.name / user.email) antes del primer commit"]
    global_email = git(feed, "config", "--global", "--get", "user.email").stdout.strip()
    if global_email and configured == global_email:
        return ["la identidad local coincide con la global personal: use una direccion no personal"]
    is_noreply = bool(OPERATOR_NOREPLY_PATTERN.match(configured))
    has_remote = bool(git(feed, "remote").stdout.strip())
    if has_remote and not is_noreply:
        return ["con remoto la identidad local debe ser la direccion noreply del operador (<id>+<usuario>@users.noreply.github.com), no %s" % configured]
    if not has_remote and not is_noreply and configured != NON_PERSONAL_EMAIL:
        return ["la identidad local %s no es %s ni una direccion noreply de GitHub" % (configured, NON_PERSONAL_EMAIL)]
    log = git(feed, "log", "--all", "--format=%ae%n%ce")
    if log.returncode != 0:
        return []  # sin commits todavia
    allowed = {NON_PERSONAL_EMAIL, WEB_COMMITTER_EMAIL}
    if is_noreply:
        allowed.add(configured)
    unexpected = sorted(set(log.stdout.split()) - allowed)
    if unexpected:
        return ["el historial contiene direcciones fuera de la lista permitida (%s): %s" % (", ".join(sorted(allowed)), ", ".join(unexpected))]
    return []


# --------------------------------------------------------------------------
# Orquestacion
# --------------------------------------------------------------------------

def structure_problems(feed: str, app_root: Optional[str]) -> Dict[str, List[str]]:
    results = {
        "ficheros-obligatorios": missing_files(feed),
        "lista-permitida": allowlist_problems(os.path.join(feed, "public")),
        "sin-headers": headers_problems(feed),
        "sin-secretos": secret_problems(feed),
        "sin-rutas-locales": local_path_problems(feed),
        "sin-datos-inventados": placeholder_problems(feed),
        "sin-ids-de-ejemplo": example_id_problems(feed),
        "repositorio-independiente": independent_repo_problems(feed),
        "remoto-esperado": remote_problems(feed),
        "identidad-no-personal": identity_problems(feed),
    }  # type: Dict[str, List[str]]
    if app_root:
        results["ignorado-por-la-app"] = app_side_problems(feed, app_root)
    return results


def report(results: Dict[str, List[str]]) -> int:
    failed = set()  # type: set
    for name, problems in results.items():
        if problems:
            failed.add(name)
            for problem in problems:
                print("FALLO %s: %s" % (name, problem), file=sys.stderr)
        else:
            print("ok %s" % name)
    return 1 if failed else 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estructura del repositorio del feed (US-007a)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("allowlist", "structure"):
        command = sub.add_parser(name)
        command.add_argument("--feed", required=True)
    sub.choices["allowlist"].add_argument("--public")
    sub.choices["structure"].add_argument("--app-root")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.command == "allowlist":
        public = args.public or os.path.join(args.feed, "public")
        return report({"lista-permitida": allowlist_problems(public)})
    return report(structure_problems(args.feed, args.app_root))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
