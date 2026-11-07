"""Aplica en bloque el canal de TV de un CSV `id,broadcasters` a la temporada (US-045; ADR-013 decision 4).

Solo biblioteca estandar, Python 3.9. Todo o nada: si una fila no es aplicable no se escribe nada.
  - `broadcasters` son nombres separados por `|`; vacio = vaciar el canal (queda null). No se aceptan URLs.
  - Solo partidos que no estan `finished` ni `cancelled`. El resto del partido no se toca.
  - Solo cuenta lo que cambia; si cambia algo, `updatedAt` nuevo (con `--same-commit` se acepta el que ya puso otro lote del mismo commit).
La lista blanca y las reglas de fecha las aplica quien genera el CSV (`auto-apply-rfef`); este modulo no lee la RFEF.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import apply_results
import apply_schedule
import round_sheet

INSTANT_FORMAT = apply_schedule.INSTANT_FORMAT
REQUIRED_COLUMNS = ("id", "broadcasters")
SEPARATOR = "|"
FIRST_DATA_LINE = 2

Row = Tuple[str, List[str]]  # (id, nombres; [] = vaciar)


def read_rows(path: str) -> List[Row]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise apply_results.ApplyError("faltan columnas en el CSV: %s" % ", ".join(missing))
        rows = []  # type: List[Row]
        seen = set()
        for line, row in enumerate(reader, start=FIRST_DATA_LINE):
            identifier = (row["id"] or "").strip()
            if identifier in seen:
                raise apply_results.ApplyError("linea %d: %s aparece repetido" % (line, identifier))
            seen.add(identifier)
            names = [name.strip() for name in (row["broadcasters"] or "").split(SEPARATOR) if name.strip()]
            if any("://" in name for name in names):
                raise apply_results.ApplyError("linea %d: %s trae una URL en vez de un nombre de canal" % (line, identifier))
            rows.append((identifier, names))
        return rows


def apply_rows(season: Dict[str, Any], rows: List[Row], updated_at: str, now: datetime.datetime, same_commit: bool = False) -> int:
    """Modifica `season` en memoria y devuelve cuantos partidos cambian. Lanza ApplyError sin haber cambiado nada si una fila no es aplicable."""
    index = {match["id"]: match for phase in season["phases"] for round_ in phase["rounds"] for match in round_["matches"]}
    changes = []  # type: List[Tuple[Dict[str, Any], List[str]]]
    for identifier, names in rows:
        match = index.get(identifier)
        if match is None:
            raise apply_results.ApplyError("%s: no existe ese partido en la temporada" % identifier)
        if match["status"] in apply_schedule.UNTOUCHED_STATUSES:
            raise apply_results.ApplyError("%s: esta %s; su canal de TV no se toca" % (identifier, match["status"]))
        if (match.get("broadcasters") or []) != names:
            changes.append((match, names))
    if changes:
        apply_results.check_updated_at(season, updated_at, now, same_batch=same_commit)
    for match, names in changes:
        match["broadcasters"] = names or None
    if changes:
        season["updatedAt"] = updated_at
    return len(changes)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplica el canal de TV en bloque (US-045)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("csv", help="CSV con columnas id,broadcasters (nombres separados por |; vacio = vaciar)")
    parser.add_argument("--now", help=argparse.SUPPRESS)  # AAAA-MM-DDTHH:MM:SSZ; solo para pruebas
    parser.add_argument("--updated-at", help="AAAA-MM-DDTHH:MM:SSZ; por defecto, ahora en UTC")
    parser.add_argument("--same-commit", action="store_true",
                        help="otro lote del mismo commit ya puso este updatedAt: se acepta igual al actual")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        season = round_sheet.load_season(args.feed)
        with open(os.path.join(args.feed, round_sheet.INDEX_PATH), "r", encoding="utf-8") as handle:
            season_path = os.path.join(args.feed, round_sheet.SEASON_PATH, json.load(handle)["currentSeasonId"] + ".json")
        rows = read_rows(args.csv)
        now = datetime.datetime.strptime(args.now, INSTANT_FORMAT) if args.now else apply_results.now_utc()
        applied = apply_rows(season, rows, args.updated_at or now.strftime(INSTANT_FORMAT), now, args.same_commit)
    except (OSError, ValueError, KeyError, apply_results.ApplyError) as error:
        print("FALLO canal de TV: %s. No se ha escrito nada." % error, file=sys.stderr)
        return 1
    if applied == 0:
        print("Sin cambios: los canales ya coinciden con el feed. El feed no se ha tocado.")
        return 0
    with open(season_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(season, indent=2, ensure_ascii=False) + "\n")
    print("Aplicados %d canales de TV; updatedAt = %s" % (applied, season["updatedAt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
