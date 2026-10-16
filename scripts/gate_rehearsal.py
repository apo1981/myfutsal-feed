"""Prepara el commit invalido del ensayo de la puerta (US-007b1, criterio 3).

Crea la rama local `ensayo-puerta` desde `main` con un unico commit que estropea la temporada
(`kind` fuera del enumerado) y avanza `updatedAt`, de modo que en CI solo falle el esquema. Comprueba
que el cambio es realmente invalido antes de confirmarlo y vuelve a `main`. No publica nada: no usa
remotos. Solo biblioteca estandar, compatible con Python 3.9.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from typing import List, Optional

BRANCH = "ensayo-puerta"
MAIN_BRANCH = "main"
INVALID_KIND = "legue"
COMMIT_MESSAGE = "test: rehearse the publish gate with an invalid kind (revert it, do not fix it)"
INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
KIND_PATTERN = re.compile(r'("kind"\s*:\s*)"league"')
UPDATED_AT_PATTERN = re.compile(r'("updatedAt"\s*:\s*)"([^"]*)"')


class RehearsalError(Exception):
    """No se puede preparar el ensayo; el mensaje dice por que."""


def git(feed: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", feed] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def git_ok(feed: str, *args: str) -> str:
    result = git(feed, *args)
    if result.returncode != 0:
        raise RehearsalError("git %s fallo: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def season_path(feed: str) -> str:
    with open(os.path.join(feed, "public", "v1", "index.json"), "r", encoding="utf-8") as handle:
        season_id = json.load(handle)["currentSeasonId"]
    return os.path.join("public", "v1", "seasons", season_id + ".json")


def branch_exists(feed: str) -> bool:
    return git(feed, "rev-parse", "--verify", "--quiet", "refs/heads/" + BRANCH).returncode == 0


def check_preconditions(feed: str, recreate: bool) -> None:
    if git_ok(feed, "branch", "--show-current") != MAIN_BRANCH:
        raise RehearsalError("hay que estar en la rama %s" % MAIN_BRANCH)
    if git_ok(feed, "status", "--porcelain", "--untracked-files=all"):
        raise RehearsalError("el arbol de trabajo no esta limpio: confirma o descarta los cambios antes")
    if branch_exists(feed) and not recreate:
        raise RehearsalError("la rama %s ya existe; usa --recreate para regenerarla" % BRANCH)


def invalid_text(text: str, now: datetime.datetime) -> str:
    """Texto con `kind` invalido y `updatedAt` posterior al actual (siempre avanza, aunque el reloj vaya atrasado)."""
    if not KIND_PATTERN.search(text):
        raise RehearsalError("no hay ningun \"kind\": \"league\" que estropear en la temporada")
    updated = UPDATED_AT_PATTERN.search(text)
    if not updated:
        raise RehearsalError("la temporada no tiene updatedAt")
    previous = datetime.datetime.strptime(updated.group(2), INSTANT_FORMAT)
    new_instant = max(now, previous + datetime.timedelta(seconds=1))
    text = KIND_PATTERN.sub(r'\1"%s"' % INVALID_KIND, text, count=1)
    return UPDATED_AT_PATTERN.sub(r'\1"%s"' % new_instant.strftime(INSTANT_FORMAT), text, count=1)


def validate_fails(feed: str, relative: str) -> bool:
    result = subprocess.run([os.path.join(feed, "scripts", "validate-schema"), os.path.join(feed, relative)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return result.returncode == 1


def prepare(feed: str, recreate: bool, now: Optional[datetime.datetime] = None) -> str:
    check_preconditions(feed, recreate)
    relative = season_path(feed)
    path = os.path.join(feed, relative)
    if recreate and branch_exists(feed):
        git_ok(feed, "branch", "-D", BRANCH)
    git_ok(feed, "switch", "-c", BRANCH)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            original = handle.read()
        changed = invalid_text(original, now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(changed)
        if not validate_fails(feed, relative):
            raise RehearsalError("el cambio preparado NO es invalido para el esquema; no se confirma")
        git_ok(feed, "add", relative)
        git_ok(feed, "commit", "-q", "-m", COMMIT_MESSAGE)
    except (RehearsalError, OSError):
        git(feed, "checkout", "--", relative)
        git(feed, "switch", MAIN_BRANCH)
        git(feed, "branch", "-D", BRANCH)
        raise
    commit = git_ok(feed, "rev-parse", "--short", "HEAD")
    git_ok(feed, "switch", MAIN_BRANCH)
    return commit


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Commit invalido del ensayo de la puerta (US-007b1)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("--recreate", action="store_true", help="regenera la rama %s si ya existe" % BRANCH)
    args = parser.parse_args(argv)
    try:
        commit = prepare(args.feed, args.recreate)
    except (RehearsalError, OSError, ValueError, KeyError) as error:
        print("FALLO ensayo: %s" % error, file=sys.stderr)
        return 1
    print("ensayo preparado: rama %s con el commit invalido %s (no publicado). Siguiente paso: RUNBOOK, «Ensayo de la puerta»." % (BRANCH, commit))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
