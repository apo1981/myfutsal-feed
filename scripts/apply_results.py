"""Aplica en bloque los marcadores de un CSV `id,home,away` a la temporada (US-007b2c, criterio 8; D-180).

Solo biblioteca estandar, Python 3.9. Todo o nada: si una fila es invalida no se escribe nada.
  - `home` y `away` vacios: la fila se ignora y el partido queda como esta (nunca se inventa un resultado).
  - Solo uno de los dos vacio, no entero o negativo, id desconocido o repetido: error.
  - Solo se aplica a partidos `unknown`, `live` o `scheduled` con fecha anterior a hoy (Madrid), de fases de liga; un partido ya `finished`,
    aplazado, suspendido o de eliminatoria se edita a mano (ver el runbook).
Pone `status: finished`, `score` y un `updatedAt` nuevo (UTC, estrictamente posterior al actual y
nunca futuro, D-544; si el actual ya esta en el futuro usa la hora actual y lo avisa).
Las columnas extra del CSV (jornada, fecha, local, visitante) se ignoran. La verificacion posterior la hace
`scripts/apply-results`.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import invariants
import round_sheet

UPDATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
APPLICABLE_STATUSES = ("unknown", "scheduled", "live")
LEAGUE_KIND = "league"
REQUIRED_COLUMNS = ("id", "home", "away")
FIRST_DATA_LINE = 2


class ApplyError(Exception):
    """Fila o fichero invalido; el mensaje dice cual."""


def parse_goals(value: str, line: int, column: str) -> int:
    try:
        goals = int(value.strip())
    except ValueError:
        raise ApplyError("linea %d: %s=%r no es un numero entero" % (line, column, value))
    if goals < 0:
        raise ApplyError("linea %d: %s=%d es negativo" % (line, column, goals))
    return goals


def read_rows(path: str) -> List[Tuple[str, int, int]]:
    """Filas con marcador completo: (id, home, away). Ignora las que tienen los dos goles vacios."""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ApplyError("faltan columnas en el CSV: %s" % ", ".join(missing))
        rows = []  # type: List[Tuple[str, int, int]]
        seen = set()
        for line, row in enumerate(reader, start=FIRST_DATA_LINE):
            identifier = (row["id"] or "").strip()
            home, away = (row["home"] or "").strip(), (row["away"] or "").strip()
            if not home and not away:
                continue
            if not home or not away:
                raise ApplyError("linea %d: %s tiene solo un gol relleno; rellena los dos o ninguno" % (line, identifier))
            if identifier in seen:
                raise ApplyError("linea %d: %s aparece repetido" % (line, identifier))
            seen.add(identifier)
            rows.append((identifier, parse_goals(home, line, "home"), parse_goals(away, line, "away")))
        return rows


def has_been_played_by(match: Dict[str, Any], today: datetime.date) -> bool:
    """Un `scheduled` solo se cierra si su fecha es anterior a hoy en Madrid (misma regla que los pendientes)."""
    date = match["kickoff"].get("date")
    return bool(date) and datetime.datetime.strptime(date, round_sheet.DATE_FORMAT).date() < today


def apply_rows(season: Dict[str, Any], rows: List[Tuple[str, int, int]], updated_at: str, today: datetime.date,
               now: datetime.datetime) -> int:
    """Modifica `season` en memoria. Lanza ApplyError sin haber cambiado nada si alguna fila no es aplicable."""
    current = datetime.datetime.strptime(season["updatedAt"], UPDATED_AT_FORMAT)
    future = invariants.future_violation({"seasonId": season.get("seasonId"), "updatedAt": updated_at}, now)
    if future:
        raise ApplyError("%s" % future.detail)
    if invariants.is_future(season["updatedAt"], now):
        print("AVISO: el updatedAt actual %s esta en el futuro; se corrige con %s" % (season["updatedAt"], updated_at), file=sys.stderr)
    elif datetime.datetime.strptime(updated_at, UPDATED_AT_FORMAT) <= current:
        raise ApplyError("el updatedAt nuevo %s no es posterior al actual %s" % (updated_at, season["updatedAt"]))
    index = {match["id"]: (phase, match) for phase in season["phases"] for round_ in phase["rounds"] for match in round_["matches"]}
    changes = []
    for identifier, home, away in rows:
        if identifier not in index:
            raise ApplyError("%s: no existe ese partido en la temporada" % identifier)
        phase, match = index[identifier]
        if phase["kind"] != LEAGUE_KIND:
            raise ApplyError("%s: es de una eliminatoria; edita a mano (decidedBy)" % identifier)
        if match["status"] not in APPLICABLE_STATUSES:
            raise ApplyError("%s: esta %s; edita a mano" % (identifier, match["status"]))
        if match["status"] == "scheduled" and not has_been_played_by(match, today):
            raise ApplyError("%s: aun no se ha jugado (fecha %s); revisa el id" % (identifier, match["kickoff"].get("date") or "sin fecha"))
        changes.append((match, home, away))
    for match, home, away in changes:
        match["status"] = "finished"
        match["score"] = {"home": home, "away": away}
    if changes:
        season["updatedAt"] = updated_at
    return len(changes)


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplica resultados en bloque (US-007b2c)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("csv", help="CSV con columnas id,home,away")
    parser.add_argument("--today", help="AAAA-MM-DD; por defecto, hoy en Europe/Madrid (solo para pruebas)")
    parser.add_argument("--now", help=argparse.SUPPRESS)  # AAAA-MM-DDTHH:MM:SSZ; solo para pruebas (D-544)
    parser.add_argument("--updated-at", help="AAAA-MM-DDTHH:MM:SSZ; por defecto, ahora en UTC")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        rows = read_rows(args.csv)
        season = round_sheet.load_season(args.feed)
        with open(os.path.join(args.feed, round_sheet.INDEX_PATH), "r", encoding="utf-8") as handle:
            season_path = os.path.join(args.feed, round_sheet.SEASON_PATH, json.load(handle)["currentSeasonId"] + ".json")
        today = datetime.datetime.strptime(args.today, round_sheet.DATE_FORMAT).date() if args.today else round_sheet.madrid_today()
        now = datetime.datetime.strptime(args.now, UPDATED_AT_FORMAT) if args.now else now_utc()
        applied = apply_rows(season, rows, args.updated_at or now.strftime(UPDATED_AT_FORMAT), today, now)
    except (OSError, ValueError, KeyError, ApplyError) as error:
        print("FALLO resultados: %s. No se ha escrito nada." % error, file=sys.stderr)
        return 1
    if applied == 0:
        print("Sin cambios: ninguna fila tiene marcador. El feed no se ha tocado.")
        return 0
    with open(season_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(season, indent=2, ensure_ascii=False) + "\n")
    print("Aplicados %d resultados; updatedAt = %s" % (applied, season["updatedAt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
