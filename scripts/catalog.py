"""Catalogo de competiciones y espejo de Primera (US-064, ADR-014 §1).

Solo biblioteca estandar, compatible con Python 3.9. No escribe nada. Lo usa `invariants.py`.
Comprueba lo que el esquema no expresa: ids derivados (`feedId`, `url`), unicidad, una temporada
`current` por competicion, `defaultFeedId`, coincidencia de cada documento con su entrada y que
`v1/index.json` y `v1/seasons/<id>.json` sean copia byte a byte de Primera en la ruta nueva.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, Iterator, List, Optional, Tuple

CATALOG_FILE = "catalog.json"
LEGACY_COMPETITION_ID = "primera"
LEGACY_INDEX = "index.json"
LEGACY_SEASON_URL = "seasons/%s.json"
COMPETITION_INDEX_URL = "competitions/%s/index.json"

Entry = Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]


def catalog_feeds(catalog: Dict[str, Any]) -> Iterator[Entry]:
    for competition in catalog["competitions"]:
        for season in competition["seasons"]:
            for feed in season["feeds"]:
                yield competition, season, feed


def expected_feed_id(competition_id: str, season_id: str, group_id: Optional[str]) -> str:
    return ".".join([competition_id, season_id] + ([group_id] if group_id else []))


def expected_feed_url(competition_id: str, season_id: str, group_id: Optional[str]) -> str:
    return "competitions/%s/%s/%s.json" % (competition_id, season_id, group_id or "main")


def catalog_problems(catalog: Dict[str, Any]) -> List[str]:
    problems = []  # type: List[str]
    for competition in catalog["competitions"]:
        current = [s["seasonId"] for s in competition["seasons"] if s["current"]]
        if len(current) != 1:
            problems.append("%s: debe tener exactamente una temporada current (tiene %d)" % (competition["id"], len(current)))
    seen = set()  # type: set
    for competition, season, feed in catalog_feeds(catalog):
        wanted = expected_feed_id(competition["id"], season["seasonId"], feed["groupId"])
        if feed["feedId"] != wanted:
            problems.append("feedId %r debe ser %r" % (feed["feedId"], wanted))
        wanted_url = expected_feed_url(competition["id"], season["seasonId"], feed["groupId"])
        if feed["url"] != wanted_url:
            problems.append("%s: url %r debe ser %r" % (feed["feedId"], feed["url"], wanted_url))
        if feed["feedId"] in seen:
            problems.append("feedId repetido: %s" % feed["feedId"])
        seen.add(feed["feedId"])
    if not any(f["feedId"] == catalog["defaultFeedId"] and s["current"] for _, s, f in catalog_feeds(catalog)):
        problems.append("defaultFeedId %r no es un feed de una temporada current" % catalog["defaultFeedId"])
    return problems


def document_problems(doc: Dict[str, Any], competition: Dict[str, Any], season: Dict[str, Any], feed: Dict[str, Any]) -> List[str]:
    """`competitionId` y `groupId` son opcionales: solo se cotejan si el documento los lleva."""
    problems = []  # type: List[str]
    if doc.get("seasonId") != season["seasonId"]:
        problems.append("seasonId %r != %r" % (doc.get("seasonId"), season["seasonId"]))
    if "competitionId" in doc and doc["competitionId"] != competition["id"]:
        problems.append("competitionId %r != %r" % (doc["competitionId"], competition["id"]))
    if "groupId" in doc and doc["groupId"] != feed["groupId"]:
        problems.append("groupId %r != %r" % (doc["groupId"], feed["groupId"]))
    return problems


def read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def mirror_problems(v1_dir: str, catalog: Dict[str, Any]) -> List[str]:
    """Primera (temporada current, sin grupo) y su indice en la ruta nueva == ficheros antiguos, byte a byte."""
    primera = [(c, s, f) for c, s, f in catalog_feeds(catalog)
               if c["id"] == LEGACY_COMPETITION_ID and s["current"] and f["groupId"] is None]
    if not primera:
        return ["el catalogo no tiene Primera (current, sin grupo): los clientes antiguos no tendrian datos"]
    _, season, feed = primera[0]
    pairs = [
        (os.path.join(v1_dir, LEGACY_SEASON_URL % season["seasonId"]), os.path.join(v1_dir, feed["url"])),
        (os.path.join(v1_dir, LEGACY_INDEX), os.path.join(v1_dir, COMPETITION_INDEX_URL % LEGACY_COMPETITION_ID)),
    ]
    problems = []  # type: List[str]
    for legacy, canonical in pairs:
        legacy_bytes, canonical_bytes = read_bytes(legacy), read_bytes(canonical)
        names = "%s y %s" % (os.path.relpath(legacy, v1_dir), os.path.relpath(canonical, v1_dir))
        if legacy_bytes is None or canonical_bytes is None:
            problems.append("espejo de Primera: falta un fichero de %s" % names)
        elif legacy_bytes != canonical_bytes:
            problems.append("espejo de Primera: %s no son identicos byte a byte" % names)
    return problems


def catalog_feed_problems(v1_dir: str, catalog: Dict[str, Any]) -> List[str]:
    """Cada feed del catalogo existe dentro de v1/ y coincide con su entrada."""
    problems = []  # type: List[str]
    root = os.path.realpath(v1_dir)
    for competition, season, feed in catalog_feeds(catalog):
        target = os.path.realpath(os.path.join(v1_dir, feed["url"]))
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            problems.append("%s: no existe %s" % (feed["feedId"], feed["url"]))
            continue
        with open(target, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
        problems.extend("%s: %s" % (feed["feedId"], p) for p in document_problems(doc, competition, season, feed))
    return problems


def check_catalog(v1_dir: str) -> List[str]:
    """Problemas del catalogo publicado, cada uno ya redactado. Sin catalogo es un fallo."""
    path = os.path.join(v1_dir, CATALOG_FILE)
    if not os.path.isfile(path):
        return ["falta v1/%s" % CATALOG_FILE]
    with open(path, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    problems = catalog_problems(catalog)
    if problems:
        return problems
    return catalog_feed_problems(v1_dir, catalog) + mirror_problems(v1_dir, catalog)


def primera_mirror_path(season_path: str) -> str:
    """Ruta nueva de Primera para `<...>/v1/seasons/<id>.json` (puede no existir)."""
    v1_dir = os.path.dirname(os.path.dirname(season_path))
    season_id = os.path.splitext(os.path.basename(season_path))[0]
    return os.path.join(v1_dir, expected_feed_url(LEGACY_COMPETITION_ID, season_id, None))


def sync_primera_mirror(season_path: str) -> None:
    """Tras escribir `v1/seasons/<id>.json`, copia el fichero a la ruta nueva de Primera si esta publicada.

    Los scripts `apply-*` editan el fichero antiguo; esta copia mantiene el espejo byte a byte (ADR-014 §1).
    """
    canonical = primera_mirror_path(season_path)
    if os.path.isfile(canonical):
        shutil.copyfile(season_path, canonical)


def sync_all(public_dir: str) -> List[str]:
    """Copia `v1/index.json` y cada `v1/seasons/<id>.json` a la ruta nueva de Primera. Devuelve lo copiado."""
    v1_dir = os.path.join(public_dir, "v1")
    copied = []  # type: List[str]
    seasons = os.path.join(v1_dir, "seasons")
    for name in sorted(os.listdir(seasons)) if os.path.isdir(seasons) else []:
        path = os.path.join(seasons, name)
        if name.endswith(".json") and os.path.isfile(primera_mirror_path(path)):
            sync_primera_mirror(path)
            copied.append(os.path.relpath(primera_mirror_path(path), v1_dir))
    mirror_index = os.path.join(v1_dir, COMPETITION_INDEX_URL % LEGACY_COMPETITION_ID)
    if os.path.isfile(mirror_index):
        shutil.copyfile(os.path.join(v1_dir, LEGACY_INDEX), mirror_index)
        copied.append(os.path.relpath(mirror_index, v1_dir))
    return copied


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Sincroniza el espejo de Primera (US-064)")
    parser.add_argument("--feed", required=True)
    args = parser.parse_args(argv)
    copied = sync_all(os.path.join(args.feed, "public"))
    print("espejo de Primera sincronizado: %s" % (", ".join(copied) or "nada que copiar"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
